"""Persistence helpers for denied inbound access requests."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from nanobot.utils.helpers import ensure_dir


class AccessRequestStore:
    """Store denied sender requests per workspace for later approval."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.path = ensure_dir(self.workspace / "security") / "access_requests.json"

    def list_pending(self) -> list[dict[str, Any]]:
        payload = self._load()
        requests = payload.get("requests")
        if not isinstance(requests, list):
            return []
        rows = [item for item in requests if isinstance(item, dict)]
        rows.sort(key=lambda item: str(item.get("last_seen") or ""), reverse=True)
        return rows

    def record(
        self,
        *,
        channel: str,
        sender_id: str,
        chat_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now().astimezone().isoformat()
        sender = str(sender_id).strip()
        chat = str(chat_id).strip()
        preview = str(content or "").strip()[:240]
        channel_name = str(channel).strip()
        meta = metadata or {}

        payload = self._load()
        requests = payload.setdefault("requests", [])
        if not isinstance(requests, list):
            requests = []
            payload["requests"] = requests

        existing = next(
            (
                item
                for item in requests
                if isinstance(item, dict)
                and item.get("channel") == channel_name
                and item.get("sender_id") == sender
            ),
            None,
        )
        if existing is None:
            existing = {
                "request_id": f"{channel_name}:{sender}",
                "channel": channel_name,
                "sender_id": sender,
                "sender_candidates": [token for token in sender.split("|") if token],
                "chat_id": chat,
                "username": str(meta.get("username") or "").strip() or None,
                "first_seen": now,
                "last_seen": now,
                "count": 0,
                "last_content": "",
            }
            requests.append(existing)

        existing["last_seen"] = now
        existing["chat_id"] = chat or existing.get("chat_id")
        if meta.get("username"):
            existing["username"] = str(meta.get("username")).strip()
        if preview:
            existing["last_content"] = preview
        existing["count"] = int(existing.get("count") or 0) + 1
        self._save(payload)
        return existing

    def remove(self, *, channel: str, sender_id: str) -> int:
        payload = self._load()
        requests = payload.get("requests")
        if not isinstance(requests, list) or not requests:
            return 0
        channel_name = str(channel).strip()
        sender = str(sender_id).strip()
        before = len(requests)
        filtered = [
            item
            for item in requests
            if not (
                isinstance(item, dict)
                and item.get("channel") == channel_name
                and item.get("sender_id") == sender
            )
        ]
        removed = before - len(filtered)
        if removed > 0:
            payload["requests"] = filtered
            self._save(payload)
        return removed

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"requests": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"requests": []}
        if not isinstance(data, dict):
            return {"requests": []}
        data.setdefault("requests", [])
        return data

    def _save(self, payload: dict[str, Any]) -> None:
        payload.setdefault("requests", [])
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
