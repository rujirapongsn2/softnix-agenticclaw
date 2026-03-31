"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import re
import weakref
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.memory import MemoryStore
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.runtime.audit import RuntimeAuditLogger
from nanobot.runtime.ephemeral_runner import DockerEphemeralTaskRunner
from nanobot.security.policy import GlobalControlPolicyStore, GlobalPolicyEnforcer, PolicyCache
from nanobot.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from nanobot.config.schema import ChannelsConfig, ExecToolConfig
    from nanobot.cron.service import CronService


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _TOOL_RESULT_MAX_CHARS = 500
    _ASSISTANT_CONTENT_MAX_CHARS = 4000
    _TOOL_CALL_ARGS_MAX_CHARS = 800
    _HISTORY_MAX_PAYLOAD_CHARS = 24000
    _GENERIC_ERROR_PATTERNS = (
        "sorry, i encountered an error",
        "error calling the ai model",
        "something went wrong",
        "internal server error",
        "request failed",
    )

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        memory_window: int = 100,
        reasoning_effort: str | None = None,
        brave_api_key: str | None = None,
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        tool_task_runner: DockerEphemeralTaskRunner | None = None,
        tool_execution_strategy: str = "persistent",
        enable_interactive_tools: bool = True,
        global_policy_path: Path | None = None,
        instance_id: str = "default",
        instance_name: str | None = None,
    ):
        from nanobot.config.schema import ExecToolConfig
        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.reasoning_effort = reasoning_effort
        self.brave_api_key = brave_api_key
        self.web_proxy = web_proxy
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.tool_task_runner = tool_task_runner
        self.tool_execution_strategy = tool_execution_strategy
        self.enable_interactive_tools = enable_interactive_tools
        self.instance_id = instance_id
        self.instance_name = instance_name or instance_id

        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        self._runtime_audit = RuntimeAuditLogger(workspace)
        self._policy_enforcer: GlobalPolicyEnforcer | None = None
        if global_policy_path is not None:
            try:
                self._policy_enforcer = GlobalPolicyEnforcer(
                    PolicyCache(GlobalControlPolicyStore(global_policy_path))
                )
            except Exception:
                self._policy_enforcer = None
        self.tools = ToolRegistry(
            on_execute=self._audit_tool_execution,
            on_start=self._audit_tool_start,
        )
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=reasoning_effort,
            brave_api_key=brave_api_key,
            web_proxy=web_proxy,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
            audit_logger=self._runtime_audit,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._consolidating: set[str] = set()  # Session keys with consolidation in progress
        self._consolidation_tasks: set[asyncio.Task] = set()  # Strong refs to in-flight tasks
        self._consolidation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._processing_lock = asyncio.Lock()
        self._register_default_tools()

    def _audit_tool_execution(self, tool_name: str, params: dict[str, Any], result: str) -> None:
        self._runtime_audit.log_tool_call(tool_name, params, result)

    def _audit_tool_start(self, tool_name: str, params: dict[str, Any]) -> None:
        self._runtime_audit.log_tool_start(tool_name, params)

    def _record_policy_decision(
        self,
        decision: Any,
        *,
        scope: str,
        channel: str | None = None,
        session_key: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        if not decision or not getattr(decision, "matched", False):
            return
        try:
            self._runtime_audit.log_policy_event(
                scope=scope,
                decision={
                    "action": decision.action,
                    "blocked": decision.blocked,
                    "severity": decision.severity,
                    "matched_rules": decision.matched_rules,
                    "mode": decision.mode,
                    "monitor_only": decision.monitor_only,
                    "text": decision.text,
                    "sanitized_text": decision.sanitized_text,
                    "policy_version": self._policy_enforcer.cache.version if self._policy_enforcer else None,
                },
                channel=channel,
                session_key=session_key,
                tool_name=tool_name,
                instance_id=self.instance_id,
                instance_name=self.instance_name,
            )
        except Exception:
            return

    def _enforce_text(
        self,
        text: str,
        *,
        scope: str,
        channel: str | None = None,
        session_key: str | None = None,
        tool_name: str | None = None,
    ) -> Any:
        if self._policy_enforcer is None:
            return None
        try:
            decision = self._policy_enforcer.enforce_text(
                text,
                scope=scope,
                channel=channel,
                session_key=session_key,
                tool_name=tool_name,
            )
        except Exception:
            return None
        self._record_policy_decision(
            decision,
            scope=scope,
            channel=channel,
            session_key=session_key,
            tool_name=tool_name,
        )
        return decision

    @staticmethod
    def _contains_thai(text: str) -> bool:
        return bool(re.search(r"[\u0E00-\u0E7F]", str(text or "")))

    def _is_sensitive_persistence_request(self, original_text: str, sanitized_text: str) -> bool:
        if not original_text or original_text == sanitized_text:
            return False
        normalized = str(original_text or "").casefold()
        patterns = (
            r"\bremember\b",
            r"\bsave\b",
            r"\bstore\b",
            r"\bkeep\b",
            r"\brecord\b",
            r"\bmemorize\b",
            r"\bretain\b",
            r"จำ",
            r"บันทึก",
            r"เก็บ",
            r"เซฟ",
        )
        return any(re.search(pattern, normalized) for pattern in patterns)

    def _masked_persistence_response(self, original_text: str) -> str:
        if self._contains_thai(original_text):
            return (
                "ผมไม่สามารถเก็บข้อมูลจริงที่ถูกจัดเป็นข้อมูลอ่อนไหวได้\n"
                "ระบบจะเก็บได้เฉพาะค่าที่ถูกปกปิดแล้วเท่านั้น เช่น `[REDACTED_EMAIL]`"
            )
        return (
            "I can't store the original sensitive data.\n"
            "The system can only retain the redacted value, such as `[REDACTED_EMAIL]`."
        )

    def _tool_arg_field(self, tool_name: str, args: dict[str, Any]) -> tuple[str | None, str | None]:
        if tool_name == "message" and isinstance(args.get("content"), str):
            return "content", str(args.get("content") or "")
        if tool_name == "exec" and isinstance(args.get("command"), str):
            return "command", str(args.get("command") or "")
        if tool_name == "write_file" and isinstance(args.get("content"), str):
            return "content", str(args.get("content") or "")
        if tool_name == "edit_file" and isinstance(args.get("new_text"), str):
            return "new_text", str(args.get("new_text") or "")
        if tool_name == "web_fetch" and isinstance(args.get("url"), str):
            return "url", str(args.get("url") or "")
        return None, None

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
            path_append=self.exec_config.path_append,
        ))
        self.tools.register(WebSearchTool(api_key=self.brave_api_key, proxy=self.web_proxy))
        self.tools.register(WebFetchTool(proxy=self.web_proxy))
        if self.enable_interactive_tools:
            self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
            self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service and self.enable_interactive_tools:
            self.tools.register(CronTool(self.cron_service))

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from nanobot.agent.tools.mcp import connect_mcp_servers
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except Exception as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""
        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}…")' if len(val) > 40 else f'{tc.name}("{val}")'
        return ", ".join(_fmt(tc) for tc in tool_calls)

    @classmethod
    def _is_generic_error_text(cls, text: str | None) -> bool:
        if not text:
            return True
        lowered = text.strip().lower()
        return any(pattern in lowered for pattern in cls._GENERIC_ERROR_PATTERNS)

    @classmethod
    def _extract_error_reason(cls, raw_error: str | None = None, exc: Exception | None = None) -> str:
        details = " ".join(
            part for part in (
                (raw_error or "").strip(),
                str(exc).strip() if exc else "",
                exc.__class__.__name__ if exc else "",
            ) if part
        ).lower()

        if any(token in details for token in ("timeout", "timed out", "deadline", "read timeout")):
            return "การเชื่อมต่อหมดเวลา (timeout)"
        if any(token in details for token in ("401", "403", "unauthorized", "forbidden", "invalid api key", "authentication", "auth")):
            return "การยืนยันตัวตนกับผู้ให้บริการไม่สำเร็จ (API key/สิทธิ์ไม่ถูกต้อง)"
        if any(token in details for token in ("429", "rate limit", "too many requests", "quota")):
            return "เกินขีดจำกัดการเรียกใช้งาน (rate limit)"
        if any(token in details for token in ("404", "not found", "no results", "no data")):
            return "ไม่พบข้อมูลที่ตรงกับคำขอ"
        if any(token in details for token in ("connection", "network", "dns", "temporar", "unreachable", "ssl")):
            return "ไม่สามารถเชื่อมต่อเครือข่ายหรือบริการปลายทางได้"
        if any(token in details for token in ("tool", "mcp", "subprocess", "command", "exit code", "traceback")):
            return "เครื่องมือที่ถูกเรียกใช้งานทำงานไม่สำเร็จ"
        if raw_error:
            return "ระบบภายนอกส่งข้อผิดพลาดกลับมา"
        return "เกิดข้อผิดพลาดภายในระบบ"

    @classmethod
    def _format_user_error_message(
        cls,
        *,
        action: str,
        raw_error: str | None = None,
        exc: Exception | None = None,
    ) -> str:
        reason = cls._extract_error_reason(raw_error=raw_error, exc=exc)
        return (
            f"ไม่สามารถ{action}ได้ เนื่องจาก{reason} "
            "กรุณาลองใหม่อีกครั้ง หรือตรวจสอบการตั้งค่าที่เกี่ยวข้อง"
        )

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        """Run the agent iteration loop. Returns (final_content, tools_used, messages)."""
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []

        while iteration < self.max_iterations:
            iteration += 1

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                reasoning_effort=self.reasoning_effort,
            )

            if response.has_tool_calls:
                if on_progress:
                    thoughts = [
                        self._strip_think(response.content),
                        response.reasoning_content,
                        *(
                            f"Thinking [{b.get('signature', '...')}]:\n{b.get('thought', '...')}"
                            for b in (response.thinking_blocks or [])
                            if isinstance(b, dict) and "signature" in b
                        ),
                    ]
                    combined_thoughts = "\n\n".join(filter(None, thoughts))
                    if combined_thoughts:
                        await on_progress(combined_thoughts)
                    await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)

                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    tool_args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
                    field_name, field_value = self._tool_arg_field(tool_call.name, tool_args)
                    if field_name and field_value is not None:
                        decision = self._enforce_text(
                            field_value,
                            scope="tool_args",
                            tool_name=tool_call.name,
                        )
                        if decision and decision.blocked:
                            result = (
                                "Error: Tool execution blocked by global control policy."
                                if not decision.message else f"Error: {decision.message}"
                            )
                        else:
                            if decision and decision.action == "mask" and not decision.monitor_only:
                                tool_args[field_name] = decision.effective_text()
                            result = await self.tools.execute(tool_call.name, tool_args)
                    else:
                        result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                clean = self._strip_think(response.content)
                # Don't persist error responses to session history — they can
                # poison the context and cause permanent 400 loops (#1303).
                if response.finish_reason == "error":
                    logger.error("LLM returned error: {}", (clean or "")[:200])
                    final_content = self._format_user_error_message(
                        action="ติดต่อโมเดล AI",
                        raw_error=clean,
                    )
                    break
                messages = self.context.add_assistant_message(
                    messages, clean, reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                final_content = clean
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        return final_content, tools_used, messages

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if msg.content.strip().lower() == "/stop":
                await self._handle_stop(msg)
            else:
                task = asyncio.create_task(self._dispatch(msg))
                self._active_tasks.setdefault(msg.session_key, []).append(task)
                task.add_done_callback(lambda t, k=msg.session_key: self._active_tasks.get(k, []) and self._active_tasks[k].remove(t) if t in self._active_tasks.get(k, []) else None)

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Cancel all active tasks and subagents for the session."""
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        content = f"⏹ Stopped {total} task(s)." if total else "No active task to stop."
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under the global lock."""
        async with self._processing_lock:
            try:
                response = await self._process_message(msg)
                if response is not None:
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="", metadata=msg.metadata or {},
                    ))
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception as exc:
                logger.exception("Error processing message for session {}", msg.session_key)
                error_content = self._format_user_error_message(
                    action="ประมวลผลคำขอ",
                    exc=exc,
                )
                self._runtime_audit.log_message_event(
                    "completed",
                    channel=msg.channel,
                    session_key=msg.session_key,
                    content=error_content,
                    status="error",
                )
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=error_content,
                ))

    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            self._runtime_audit.log_message_event(
                "received",
                channel=channel,
                session_key=key,
                content=msg.content,
            )
            session = self.sessions.get_or_create(key)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            history = session.get_history(
                max_messages=self.memory_window,
                max_payload_chars=self._HISTORY_MAX_PAYLOAD_CHARS,
            )
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content, channel=channel, chat_id=chat_id,
            )
            final_content, _, all_msgs = await self._run_agent_loop(messages)
            self._save_turn(session, all_msgs, 1 + len(history))
            self.sessions.save(session)
            self._runtime_audit.log_message_event(
                "completed",
                channel=channel,
                session_key=key,
                content=final_content or "Background task completed.",
            )
            return OutboundMessage(channel=channel, chat_id=chat_id,
                                  content=final_content or "Background task completed.")

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        input_decision = self._enforce_text(
            msg.content,
            scope="input",
            channel=msg.channel,
            session_key=key,
        )
        incoming_content = msg.content
        if input_decision and input_decision.blocked:
            blocked_content = input_decision.message or "Request blocked by global control policy."
            self._runtime_audit.log_message_event(
                "completed",
                channel=msg.channel,
                session_key=key,
                content=blocked_content,
                status="error",
            )
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=blocked_content)
        if input_decision and input_decision.action == "mask" and not input_decision.monitor_only:
            incoming_content = input_decision.effective_text()
            if self._is_sensitive_persistence_request(msg.content, incoming_content):
                refusal = self._masked_persistence_response(msg.content)
                self._runtime_audit.log_message_event(
                    "received",
                    channel=msg.channel,
                    session_key=key,
                    content=incoming_content,
                )
                self._runtime_audit.log_message_event(
                    "completed",
                    channel=msg.channel,
                    session_key=key,
                    content=refusal,
                    status="error",
                )
                return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=refusal)
        self._runtime_audit.log_message_event(
            "received",
            channel=msg.channel,
            session_key=key,
            content=incoming_content,
        )
        session = self.sessions.get_or_create(key)

        # Slash commands
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())
            self._consolidating.add(session.key)
            try:
                async with lock:
                    snapshot = session.messages[session.last_consolidated:]
                    if snapshot:
                        temp = Session(key=session.key)
                        temp.messages = list(snapshot)
                        if not await self._consolidate_memory(temp, archive_all=True):
                            return OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content="Memory archival failed, session not cleared. Please try again.",
                            )
            except Exception:
                logger.exception("/new archival failed for {}", session.key)
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Memory archival failed, session not cleared. Please try again.",
                )
            finally:
                self._consolidating.discard(session.key)

            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="New session started.")
        if cmd == "/help":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="🐈 nanobot commands:\n/new — Start a new conversation\n/stop — Stop the current task\n/help — Show available commands")

        unconsolidated = len(session.messages) - session.last_consolidated
        if (unconsolidated >= self.memory_window and session.key not in self._consolidating):
            self._consolidating.add(session.key)
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())

            async def _consolidate_and_unlock():
                try:
                    async with lock:
                        await self._consolidate_memory(session)
                finally:
                    self._consolidating.discard(session.key)
                    _task = asyncio.current_task()
                    if _task is not None:
                        self._consolidation_tasks.discard(_task)

            _task = asyncio.create_task(_consolidate_and_unlock())
            self._consolidation_tasks.add(_task)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(
            max_messages=self.memory_window,
            max_payload_chars=self._HISTORY_MAX_PAYLOAD_CHARS,
        )
        initial_messages = self.context.build_messages(
            history=history,
            current_message=incoming_content,
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=msg.chat_id,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        if self._should_delegate_tool_tasks():
            final_content, all_msgs = await self._process_with_tool_strategy(
                initial_messages=initial_messages,
                session_key=key,
                channel=msg.channel,
                chat_id=msg.chat_id,
                on_progress=on_progress or _bus_progress,
            )
        else:
            final_content, _, all_msgs = await self._run_agent_loop(
                initial_messages, on_progress=on_progress or _bus_progress,
            )

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        output_decision = self._enforce_text(
            final_content,
            scope="output",
            channel=msg.channel,
            session_key=key,
        )
        if output_decision and output_decision.blocked:
            final_content = output_decision.message or "Response withheld by global control policy."
        elif output_decision and output_decision.action == "mask" and not output_decision.monitor_only:
            final_content = output_decision.effective_text()

        self._save_turn(session, all_msgs, 1 + len(history))
        self.sessions.save(session)

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            self._runtime_audit.log_message_event(
                "completed",
                channel=msg.channel,
                session_key=key,
                content="Response sent via message tool.",
            )
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        self._runtime_audit.log_message_event(
            "completed",
            channel=msg.channel,
            session_key=key,
            content=final_content,
        )
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=msg.metadata or {},
        )

    def _should_delegate_tool_tasks(self) -> bool:
        return self.tool_execution_strategy == "tool_ephemeral" and self.tool_task_runner is not None

    async def _process_with_tool_strategy(
        self,
        *,
        initial_messages: list[dict[str, Any]],
        session_key: str,
        channel: str,
        chat_id: str,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[dict]]:
        response = await self.provider.chat(
            messages=initial_messages,
            tools=self.tools.get_definitions(),
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=self.reasoning_effort,
        )

        if response.has_tool_calls:
            if on_progress:
                await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)
                await on_progress("Launching ephemeral sandbox for tool-using task...")
            final_content = await self.tool_task_runner.run_messages(
                initial_messages,
                session_key=session_key,
                channel=channel,
                chat_id=chat_id,
            )
            messages = self.context.add_assistant_message(initial_messages, final_content)
            return final_content, messages

        clean = self._strip_think(response.content)
        if response.finish_reason == "error":
            logger.error("LLM returned error: {}", (clean or "")[:200])
            final_content = self._format_user_error_message(
                action="ติดต่อโมเดล AI",
                raw_error=clean,
            )
            messages = self.context.add_assistant_message(initial_messages, final_content)
            return final_content, messages

        messages = self.context.add_assistant_message(
            initial_messages,
            clean,
            reasoning_content=response.reasoning_content,
            thinking_blocks=response.thinking_blocks,
        )
        return clean, messages

    async def run_messages(self, initial_messages: list[dict[str, Any]]) -> str:
        """Run a prepared message list directly and return final content."""
        await self._connect_mcp()
        final_content, _, _ = await self._run_agent_loop(initial_messages)
        return final_content or ""

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "assistant":
                if isinstance(content, str) and len(content) > self._ASSISTANT_CONTENT_MAX_CHARS:
                    entry["content"] = content[:self._ASSISTANT_CONTENT_MAX_CHARS] + "\n... (truncated)"
                tool_calls = entry.get("tool_calls")
                if isinstance(tool_calls, list):
                    compacted_calls = []
                    for tool_call in tool_calls:
                        if not isinstance(tool_call, dict):
                            compacted_calls.append(tool_call)
                            continue
                        compacted = dict(tool_call)
                        function = compacted.get("function")
                        if isinstance(function, dict):
                            compacted_function = dict(function)
                            args = compacted_function.get("arguments")
                            if args is not None:
                                args_text = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
                                if len(args_text) > self._TOOL_CALL_ARGS_MAX_CHARS:
                                    compacted_function["arguments"] = json.dumps(
                                        {
                                            "_truncated": True,
                                            "preview": args_text[:self._TOOL_CALL_ARGS_MAX_CHARS],
                                        },
                                        ensure_ascii=False,
                                    )
                            compacted["function"] = compacted_function
                        compacted_calls.append(compacted)
                    entry["tool_calls"] = compacted_calls
            if role == "tool" and isinstance(content, str) and len(content) > self._TOOL_RESULT_MAX_CHARS:
                entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # Strip the runtime-context prefix, keep only the user text.
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue
                if isinstance(content, list):
                    filtered = []
                    for c in content:
                        if c.get("type") == "text" and isinstance(c.get("text"), str) and c["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                            continue  # Strip runtime context from multimodal messages
                        if (c.get("type") == "image_url"
                                and c.get("image_url", {}).get("url", "").startswith("data:image/")):
                            filtered.append({"type": "text", "text": "[image]"})
                        else:
                            filtered.append(c)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    async def _consolidate_memory(self, session, archive_all: bool = False) -> bool:
        """Delegate to MemoryStore.consolidate(). Returns True on success."""
        return await MemoryStore(self.workspace, policy_enforcer=self._policy_enforcer).consolidate(
            session, self.provider, self.model,
            archive_all=archive_all, memory_window=self.memory_window,
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        response = await self._process_message(msg, session_key=session_key, on_progress=on_progress)
        return response.content if response else ""
