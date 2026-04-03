"""Context builder for assembling agent prompts."""

import base64
import mimetypes
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.utils.helpers import detect_image_mime


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)

    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills."""
        parts = [self._get_identity()]

        connector_routing = self._build_connector_routing()
        if connector_routing:
            parts.append(connector_routing)

        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        memory = self.memory.get_memory_context()
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")

        return "\n\n---\n\n".join(parts)

    def _build_connector_routing(self) -> str:
        """Add explicit connector selection guidance when built-in connector skills exist."""
        available = set(self.skills.get_always_skills())
        routing_lines = ["# Connector Routing", ""]
        has_any = False
        if "insightdoc-connector" in available:
            routing_lines.append("- Use the InsightDOC connector for job, document upload, OCR, review, confirmation, rejection, and integration dispatch tasks.")
            routing_lines.append("- If the task is about document workflows, InsightDOC wins even if GitHub, Gmail, or Notion are also installed.")
            has_any = True
        if "notion-connector" in available:
            routing_lines.append("- Use the Notion connector for any Notion page, database, workspace, or content-reading question.")
            has_any = True
        if "gmail-connector" in available:
            routing_lines.append("- Use the Gmail connector for any email, inbox search, message, thread, attachment, sender, recipient, label, or mailbox question.")
            routing_lines.append("- If the task is about email, Gmail wins even if GitHub or Notion are also installed.")
            routing_lines.append("- Never route email or inbox questions to the GitHub connector.")
            has_any = True
        if "composio-connector" in available:
            routing_lines.append("- Use the Composio connector for supported third-party app actions that are exposed through Composio and are not already covered by a dedicated built-in connector.")
            routing_lines.append("- When a dedicated connector exists for a domain, prefer that dedicated connector over Composio.")
            has_any = True
        if "github-connector" in available:
            routing_lines.append("- Use the GitHub connector for any repository, issue, pull request, workflow, or commit question.")
            has_any = True
        if not has_any:
            return ""
        routing_lines += [
            "- Only use connectors whose tools are actually available in the current tool list. A configured or remembered connector that is not present in the tool list should be treated as unavailable for this turn.",
            "- Connector context tools such as `*_get_connector_context` are diagnostic only. Use them only for the connector that matches the user's requested product or when explicitly debugging connector readiness.",
            "- Do not substitute one connector for another.",
            "- If the user mentions a product name explicitly, prefer that connector first before falling back to a generic skill.",
            "- If a connector is available and the task matches its domain, call that connector even if another connector is also installed.",
            "- If the user explicitly asks for Google Sheets, Google Drive, Google Calendar, Slack, or another named app and there is no matching connector/tool available, say that the required connector is unavailable or disabled. Do not search Notion, Gmail, GitHub, or other unrelated apps as a substitute unless the user explicitly asks for that broader search.",
        ]
        return "\n".join(routing_lines)

    def _get_identity(self) -> str:
        """Get the core identity section."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return f"""# nanobot 🐈

You are nanobot, a helpful AI assistant.

## Runtime
{runtime}

## Workspace
Your workspace is at: {workspace_path}
- Long-term memory: {workspace_path}/memory/MEMORY.md (write important facts here)
- History log: {workspace_path}/memory/HISTORY.md (grep-searchable). Each entry starts with [YYYY-MM-DD HH:MM].
- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md
- File tools resolve relative paths from the workspace root. Use paths like `skills/my-skill/SKILL.md`, not `workspace/skills/my-skill/SKILL.md`.

## nanobot Guidelines
- State intent before tool calls, but NEVER predict or claim results before receiving them.
- Before modifying a file, read it first. Do not assume files or directories exist.
- After writing or editing a file, re-read it if accuracy matters.
- If a tool call fails, analyze the error before retrying with a different approach.
- Ask for clarification when the request is ambiguous.

Reply directly with text for conversations. Only use the 'message' tool to send to a specific chat channel."""

    @staticmethod
    def _build_runtime_context(channel: str | None, chat_id: str | None) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = time.strftime("%Z") or "UTC"
        lines = [f"Current Time: {now} ({tz})"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines)

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        runtime_ctx = self._build_runtime_context(channel, chat_id)
        user_content = self._build_user_content(current_message, media)

        # Merge runtime context and user content into a single user message
        # to avoid consecutive same-role messages that some providers reject.
        if isinstance(user_content, str):
            merged = f"{runtime_ctx}\n\n{user_content}"
        else:
            merged = list(user_content) + [{
                "type": "text",
                "text": (
                    f"{runtime_ctx}\n"
                    "The metadata above is not the user's request. "
                    "Use it only as background session information."
                ),
            }]

        return [
            {"role": "system", "content": self.build_system_prompt(skill_names)},
            *history,
            {"role": "user", "content": merged},
        ]

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional attachment grounding and images."""
        if not media:
            return text

        images = []
        attachment_details: list[dict[str, str]] = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                continue
            display_path = self._display_media_path(p)
            raw = p.read_bytes()
            # Detect real MIME type from magic bytes; fallback to filename guess
            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            attachment_details.append(
                {
                    "name": p.name,
                    "path": display_path,
                    "kind": "image" if mime and mime.startswith("image/") else "file",
                }
            )
            if not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(raw).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

        if not attachment_details:
            return text
        grounding_text = self._build_attachment_grounding_text(text, attachment_details)
        if not images:
            return grounding_text
        return images + [{"type": "text", "text": grounding_text}]

    def _display_media_path(self, path: Path) -> str:
        try:
            relative = path.resolve().relative_to(self.workspace.resolve())
        except ValueError:
            return str(path)
        return f"workspace/{relative.as_posix()}"

    @staticmethod
    def _build_attachment_grounding_text(text: str, attachments: list[dict[str, str]]) -> str:
        """Add clear grounding instructions when files are attached."""
        normalized = (text or "").strip() or "Please analyze the attached file(s)."
        image_count = sum(1 for item in attachments if item.get("kind") == "image")
        file_lines = "\n".join(
            f"- {item.get('name') or 'attachment'} at {item.get('path') or 'unknown path'}"
            for item in attachments
        )
        instructions = [
            f"The user attached {len(attachments)} file(s).",
            "These files are available inside the workspace at the paths below.",
            "Use the listed workspace paths if you need to inspect or process the files with tools.",
        ]
        if image_count:
            instructions.append("For image attachments, base your answer primarily on the attached image content.")
            instructions.append("Base your answer primarily on the attached image content.")
        return (
            "[Attachment Context]\n"
            + "\n".join(instructions)
            + "\n"
            + file_lines
            + "\n"
            + "If an attachment is unreadable or insufficient, say that explicitly instead of guessing.\n"
            + "Do not answer from unrelated prior conversation context when the attachment is the main source.\n\n"
            "[User Message]\n"
            + normalized
        )

    def add_tool_result(
        self, messages: list[dict[str, Any]],
        tool_call_id: str, tool_name: str, result: str,
    ) -> list[dict[str, Any]]:
        """Add a tool result to the message list."""
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result})
        return messages

    def add_assistant_message(
        self, messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Add an assistant message to the message list."""
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if reasoning_content is not None:
            msg["reasoning_content"] = reasoning_content
        if thinking_blocks:
            msg["thinking_blocks"] = thinking_blocks
        messages.append(msg)
        return messages
