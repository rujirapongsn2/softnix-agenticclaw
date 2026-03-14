from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from nanobot.admin.auth import get_request_audit_context, iso_in, iso_now, is_session_expired, normalize_email, normalize_role, normalize_username
from nanobot.admin.layout import get_softnix_admin_dir
from nanobot.utils.helpers import ensure_dir


def _infer_audit_category(event_type: str) -> str:
    if event_type.startswith("auth."):
        return "authentication"
    if event_type.startswith("user."):
        return "user_management"
    if event_type.startswith("instance.") or event_type.startswith("runtime."):
        return "instance_management"
    if event_type.startswith("schedule."):
        return "configuration"
    if event_type.startswith("config.") or event_type.startswith("security.") or event_type.startswith("channel."):
        return "configuration"
    if event_type.startswith("access_request."):
        return "authorization"
    return "administration"


class AdminAuthStore:
    def __init__(self, base_dir: Path):
        self.base_dir = ensure_dir(base_dir)
        self.security_dir = ensure_dir(self.base_dir / "security")
        self.users_path = self.security_dir / "users.json"
        self.sessions_path = self.security_dir / "sessions.json"
        self.audit_path = self.security_dir / "auth_audit.jsonl"

    @classmethod
    def from_registry_or_config(cls, *, registry_path: Path | None, config_path: Path) -> "AdminAuthStore":
        if registry_path is not None:
            base_dir = get_softnix_admin_dir(registry_path.parent.parent if registry_path.parent.name == "admin" else None)
        else:
            base_dir = ensure_dir(config_path.expanduser().resolve().parent / ".nanobot-admin")
        return cls(base_dir)

    def has_users(self) -> bool:
        return len(self.list_users(include_disabled=True)) > 0

    def list_users(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        payload = self._load_json(self.users_path, {"users": []})
        users = [item for item in payload.get("users", []) if isinstance(item, dict)]
        if not include_disabled:
            users = [item for item in users if str(item.get("status") or "active") != "disabled"]
        users.sort(key=lambda item: (str(item.get("username") or ""), str(item.get("id") or "")))
        return users

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        key = str(user_id or "").strip()
        if not key:
            return None
        for user in self.list_users(include_disabled=True):
            if str(user.get("id") or "") == key:
                return user
        return None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        normalized = normalize_username(username)
        if not normalized:
            return None
        for user in self.list_users(include_disabled=True):
            if normalize_username(str(user.get("username") or "")) == normalized:
                return user
        return None

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        normalized = normalize_email(email)
        if not normalized:
            return None
        for user in self.list_users(include_disabled=True):
            if normalize_email(str(user.get("email") or "")) == normalized:
                return user
        return None

    def upsert_user(self, user: dict[str, Any]) -> dict[str, Any]:
        payload = self._load_json(self.users_path, {"users": []})
        users = [item for item in payload.get("users", []) if isinstance(item, dict)]
        user_id = str(user.get("id") or "").strip()
        if not user_id:
            raise ValueError("User id is required")
        record = dict(user)
        record["username"] = normalize_username(str(record.get("username") or ""))
        record["email"] = normalize_email(record.get("email"))
        record["role"] = normalize_role(str(record.get("role") or "viewer"))
        record["status"] = str(record.get("status") or "active").strip().lower() or "active"
        if not record["username"]:
            raise ValueError("Username is required")
        replaced = False
        next_users: list[dict[str, Any]] = []
        for item in users:
            if str(item.get("id") or "") == user_id:
                next_users.append(record)
                replaced = True
            else:
                next_users.append(item)
        if not replaced:
            next_users.append(record)
        next_users.sort(key=lambda item: (str(item.get("username") or ""), str(item.get("id") or "")))
        payload["users"] = next_users
        self._save_json(self.users_path, payload)
        return record

    def delete_user(self, user_id: str) -> bool:
        payload = self._load_json(self.users_path, {"users": []})
        users = [item for item in payload.get("users", []) if isinstance(item, dict)]
        before = len(users)
        payload["users"] = [item for item in users if str(item.get("id") or "") != str(user_id or "").strip()]
        if len(payload["users"]) == before:
            return False
        self._save_json(self.users_path, payload)
        return True

    def create_session(
        self,
        *,
        session_id: str,
        user_id: str,
        ip: str | None,
        user_agent: str | None,
        csrf_token: str | None = None,
    ) -> dict[str, Any]:
        payload = self._load_json(self.sessions_path, {"sessions": []})
        sessions = [item for item in payload.get("sessions", []) if isinstance(item, dict)]
        record = {
            "id": session_id,
            "user_id": str(user_id or "").strip(),
            "created_at": iso_now(),
            "last_seen_at": iso_now(),
            "expires_at": iso_in(days=7),
            "idle_expires_at": iso_in(hours=12),
            "ip": str(ip or "").strip() or None,
            "user_agent": str(user_agent or "").strip()[:500] or None,
            "revoked": False,
            "csrf_token": csrf_token,
        }
        sessions = [item for item in sessions if str(item.get("id") or "") != session_id]
        sessions.append(record)
        payload["sessions"] = sessions
        self._save_json(self.sessions_path, payload)
        return record

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        key = str(session_id or "").strip()
        if not key:
            return None
        payload = self._load_json(self.sessions_path, {"sessions": []})
        sessions = [item for item in payload.get("sessions", []) if isinstance(item, dict)]
        dirty = False
        active_sessions: list[dict[str, Any]] = []
        found: dict[str, Any] | None = None
        for item in sessions:
            if str(item.get("id") or "") == key:
                if bool(item.get("revoked")) or is_session_expired(item.get("expires_at")) or is_session_expired(item.get("idle_expires_at")):
                    dirty = True
                    continue
                found = item
            active_sessions.append(item)
        if dirty:
            payload["sessions"] = active_sessions
            self._save_json(self.sessions_path, payload)
        return found

    def touch_session(self, session_id: str, *, csrf_token: str | None = None) -> dict[str, Any] | None:
        payload = self._load_json(self.sessions_path, {"sessions": []})
        sessions = [item for item in payload.get("sessions", []) if isinstance(item, dict)]
        found = None
        next_sessions: list[dict[str, Any]] = []
        for item in sessions:
            if str(item.get("id") or "") == str(session_id or "").strip():
                if bool(item.get("revoked")) or is_session_expired(item.get("expires_at")) or is_session_expired(item.get("idle_expires_at")):
                    continue
                updated = dict(item)
                updated["last_seen_at"] = iso_now()
                updated["idle_expires_at"] = iso_in(hours=12)
                if csrf_token is not None:
                    updated["csrf_token"] = csrf_token
                found = updated
                next_sessions.append(updated)
                continue
            next_sessions.append(item)
        payload["sessions"] = next_sessions
        self._save_json(self.sessions_path, payload)
        return found

    def revoke_session(self, session_id: str) -> bool:
        payload = self._load_json(self.sessions_path, {"sessions": []})
        sessions = [item for item in payload.get("sessions", []) if isinstance(item, dict)]
        changed = False
        next_sessions: list[dict[str, Any]] = []
        for item in sessions:
            if str(item.get("id") or "") == str(session_id or "").strip():
                updated = dict(item)
                updated["revoked"] = True
                next_sessions.append(updated)
                changed = True
            else:
                next_sessions.append(item)
        if changed:
            payload["sessions"] = next_sessions
            self._save_json(self.sessions_path, payload)
        return changed

    def revoke_sessions_for_user(self, user_id: str) -> int:
        payload = self._load_json(self.sessions_path, {"sessions": []})
        sessions = [item for item in payload.get("sessions", []) if isinstance(item, dict)]
        count = 0
        next_sessions: list[dict[str, Any]] = []
        for item in sessions:
            if str(item.get("user_id") or "") == str(user_id or "").strip() and not bool(item.get("revoked")):
                updated = dict(item)
                updated["revoked"] = True
                next_sessions.append(updated)
                count += 1
            else:
                next_sessions.append(item)
        if count:
            payload["sessions"] = next_sessions
            self._save_json(self.sessions_path, payload)
        return count

    def append_audit(
        self,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        outcome: str = "success",
        category: str | None = None,
        actor: dict[str, Any] | None = None,
        resource: dict[str, Any] | None = None,
    ) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_actor = actor or get_request_audit_context() or {}
        inferred_category = category or _infer_audit_category(str(event_type or ""))
        record: dict[str, Any] = {
            "ts": iso_now(),
            "event_type": str(event_type or "auth.unknown"),
            "category": inferred_category,
            "outcome": str(outcome or "success"),
            "actor": resolved_actor,
        }
        if resource:
            record["resource"] = resource
        if payload:
            record["detail"] = payload
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return dict(default)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return dict(default)
        if not isinstance(data, dict):
            return dict(default)
        return data

    @staticmethod
    def _save_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
        temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(path)
