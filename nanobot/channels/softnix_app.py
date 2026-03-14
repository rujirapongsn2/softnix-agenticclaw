"""Native Softnix Mobile App channel implementation with file-based relay."""

import asyncio
import json
import mimetypes
import os
import shutil
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel


class SoftnixAppChannel(BaseChannel):
    """
    Channel for the native Softnix Mobile App using a file-based relay.
    
    This allows the Admin API (running in a separate process) to send 
    messages to the agent and receive replies.
    """

    name: str = "softnix_app"

    def __init__(self, config: Any, bus: MessageBus, workspace_path: Path):
        super().__init__(config, bus)
        self.workspace_path = workspace_path
        self.relay_dir = workspace_path / "mobile_relay"
        self.inbound_file = self.relay_dir / "inbound.jsonl"
        self.outbound_file = self.relay_dir / "outbound.jsonl"
        self.reply_callback: Callable[[OutboundMessage], Any] | None = None
        
        # Ensure relay directory exists
        self.relay_dir.mkdir(parents=True, exist_ok=True)

    async def start(self) -> None:
        """Start watching the inbound relay file."""
        self._running = True
        logger.info(f"Softnix Mobile Channel watching {self.inbound_file}")
        
        # Truncate inbound file on start to avoid processing old messages
        if self.inbound_file.exists():
            self.inbound_file.write_text("")

        while self._running:
            try:
                if self.inbound_file.exists() and self.inbound_file.stat().st_size > 0:
                    await self._process_inbound()
                
                await asyncio.sleep(1) # Poll interval
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Softnix Mobile Channel loop: {e}")
                await asyncio.sleep(2)

    async def stop(self) -> None:
        """Stop the channel."""
        self._running = False

    async def _process_inbound(self) -> None:
        """Read and clear the inbound relay file."""
        # Dynamically refresh allow_from from relay/allow_from.json so newly-registered
        # devices are accepted without requiring an agent restart or SIGHUP reload.
        _af_file = self.relay_dir / "allow_from.json"
        if _af_file.exists():
            try:
                _af_data = json.loads(_af_file.read_text(encoding="utf-8"))
                _dynamic_allow_from = _af_data.get("allow_from")
                if isinstance(_dynamic_allow_from, list) and hasattr(self.config, "allow_from"):
                    self.config.allow_from = _dynamic_allow_from
            except Exception:
                pass

        try:
            # Read all lines
            lines = self.inbound_file.read_text().splitlines()
            # Clear file immediately to avoid double processing
            self.inbound_file.write_text("")
            
            for line in lines:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    sender_id = data["sender_id"]
                    chat_id = data.get("session_id") or f"mobile-{sender_id}"
                    content = data["text"]
                    metadata = {
                        "sender_id": sender_id,
                        "message_id": data.get("message_id"),
                        "reply_to": data.get("reply_to"),
                        "thread_root_id": data.get("thread_root_id"),
                    }
                    
                    await self._handle_message(
                        sender_id=sender_id,
                        chat_id=chat_id,
                        content=content,
                        media=list(data.get("media") or []),
                        metadata={k: v for k, v in metadata.items() if v},
                        session_key=chat_id,
                    )
                except Exception as e:
                    logger.error(f"Failed to parse inbound mobile message: {e}")
        except Exception as e:
            logger.error(f"Error reading mobile inbound file: {e}")

    def _extract_sender_id(self, chat_id: str, metadata: dict[str, Any]) -> str:
        sender_id = str(metadata.get("sender_id") or "").strip()
        if sender_id:
            return sender_id
        raw = str(chat_id or "")
        if raw.startswith("mobile-"):
            raw = raw[len("mobile-"):]
        for sep in ("#thread:", ":thread:", "|thread:"):
            if sep in raw:
                raw = raw.split(sep, 1)[0]
                break
        return raw

    def _relay_media_ref(self, sender_id: str, media_path: str) -> dict[str, Any] | None:
        source = Path(str(media_path or "")).expanduser()
        if not source.exists() or not source.is_file():
            logger.warning("Softnix mobile outbound media not found: {}", media_path)
            return None

        safe_sender = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in sender_id) or "sender"
        media_dir = self.relay_dir / "outbound_media" / safe_sender
        media_dir.mkdir(parents=True, exist_ok=True)

        safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in source.name) or "attachment"
        relay_name = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}-{safe_name}"
        relay_path = media_dir / relay_name
        shutil.copy2(source, relay_path)

        mime_type = mimetypes.guess_type(relay_name)[0] or "application/octet-stream"
        kind = mime_type.split("/", 1)[0]
        if kind not in {"image", "audio", "video"}:
            kind = "file"
        return {
            "name": source.name,
            "file_name": relay_name,
            "mime_type": mime_type,
            "size": relay_path.stat().st_size,
            "kind": kind,
            "url": (
                f"/admin/mobile/media?instance_id={self.workspace_path.name}"
                f"&sender_id={sender_id}&file={relay_name}"
            ),
        }

    async def send(self, message: OutboundMessage) -> None:
        """Write the agent reply to the outbound relay file."""
        try:
            # chat_id for mobile channel is used as the session identifier
            # We want to relay back the sender_id so the mobile app knows who it's for.
            # Usually chat_id was constructed as mobile-{sender_id}
            sender_id = self._extract_sender_id(message.chat_id, message.metadata or {})

            # Determine message type from metadata flags set by the agent loop
            is_progress = message.metadata.get("_progress", False)
            is_tool_hint = message.metadata.get("_tool_hint", False)
            if is_tool_hint:
                msg_type = "tool"
            elif is_progress:
                msg_type = "progress"
            else:
                msg_type = "answer"

            attachments = []
            for media_path in (message.media or []):
                if item := self._relay_media_ref(sender_id, media_path):
                    attachments.append(item)

            data = {
                "message_id": f"mobr-{secrets.token_hex(8)}",
                "text": message.content,
                "type": msg_type,
                "sender_id": sender_id,
                "session_id": message.chat_id,
                "reply_to": message.reply_to or message.metadata.get("reply_to") or message.metadata.get("message_id"),
                "thread_root_id": message.metadata.get("thread_root_id") or message.metadata.get("reply_to") or message.metadata.get("message_id"),
                "attachments": attachments,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with self.outbound_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(data) + "\n")
            
            # Also call local callback if set (useful if running in same process)
            if self.reply_callback:
                if asyncio.iscoroutinefunction(self.reply_callback):
                    await self.reply_callback(message)
                else:
                    self.reply_callback(message)
        except Exception as e:
            logger.error(f"Failed to write outbound mobile message: {e}")

    def set_reply_callback(self, callback: Callable[[OutboundMessage], Any]) -> None:
        """Set the callback for delivering agent replies (same-process only)."""
        self.reply_callback = callback
