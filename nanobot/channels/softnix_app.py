"""Native Softnix Mobile App channel implementation with file-based relay."""

import asyncio
import json
import os
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
                    
                    await self._handle_message(
                        sender_id=sender_id,
                        chat_id=chat_id,
                        content=content
                    )
                except Exception as e:
                    logger.error(f"Failed to parse inbound mobile message: {e}")
        except Exception as e:
            logger.error(f"Error reading mobile inbound file: {e}")

    async def send(self, message: OutboundMessage) -> None:
        """Write the agent reply to the outbound relay file."""
        try:
            # chat_id for mobile channel is used as the session identifier
            # We want to relay back the sender_id so the mobile app knows who it's for.
            # Usually chat_id was constructed as mobile-{sender_id}
            sender_id = message.chat_id
            if sender_id.startswith("mobile-"):
                sender_id = sender_id[len("mobile-"):]

            data = {
                "text": message.content,
                "sender_id": sender_id,
                "session_id": message.chat_id,
                "timestamp": os.getpid(), # Simplified TS or similar
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
