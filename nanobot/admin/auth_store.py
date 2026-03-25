from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

import hmac

from nanobot.admin.auth import (
    get_request_audit_context,
    iso_in,
    iso_now,
    is_session_expired,
    normalize_email,
    normalize_instance_ids,
    normalize_role,
    normalize_username,
)
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
        instance_ids = normalize_instance_ids(record.get("instance_ids"))
        if instance_ids is None:
            record.pop("instance_ids", None)
        else:
            record["instance_ids"] = instance_ids
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

    # ── Mobile pairing & device management ──────────────────────────

    @property
    def pairing_tokens_path(self) -> Path:
        return self.security_dir / "mobile_pairing_tokens.json"

    @property
    def mobile_devices_path(self) -> Path:
        return self.security_dir / "mobile_devices.json"

    @property
    def mobile_push_keys_path(self) -> Path:
        return self.security_dir / "mobile_push_keys.json"

    @property
    def mobile_push_private_key_path(self) -> Path:
        return self.security_dir / "mobile_push_private.pem"

    @property
    def mobile_push_subscriptions_path(self) -> Path:
        return self.security_dir / "mobile_push_subscriptions.json"

    @property
    def mobile_transfer_tokens_path(self) -> Path:
        return self.security_dir / "mobile_transfer_tokens.json"

    def create_pairing_token(self, instance_id: str, token: str, expires_at: str) -> None:
        payload = self._load_json(self.pairing_tokens_path, {"tokens": []})
        payload["tokens"] = [
            t for t in payload["tokens"]
            if not t.get("used") and not is_session_expired(t.get("expires_at"))
        ]
        payload["tokens"].append({
            "token": token,
            "instance_id": instance_id,
            "expires_at": expires_at,
            "used": False,
        })
        self._save_json(self.pairing_tokens_path, payload)

    def validate_and_consume_pairing_token(self, instance_id: str, token: str) -> bool:
        payload = self._load_json(self.pairing_tokens_path, {"tokens": []})
        for t in payload["tokens"]:
            if hmac.compare_digest(str(t.get("token", "")), token) and t.get("instance_id") == instance_id:
                if t.get("used") or is_session_expired(t.get("expires_at")):
                    return False
                t["used"] = True
                self._save_json(self.pairing_tokens_path, payload)
                return True
        return False

    def list_mobile_devices(self, instance_id: str) -> list[dict[str, Any]]:
        payload = self._load_json(self.mobile_devices_path, {"devices": []})
        devices: list[dict[str, Any]] = []
        for item in payload.get("devices", []):
            if not isinstance(item, dict) or item.get("instance_id") != instance_id:
                continue
            device = dict(item)
            device.pop("device_token", None)
            devices.append(device)
        return devices

    def get_mobile_device(self, instance_id: str, device_id: str) -> dict[str, Any] | None:
        payload = self._load_json(self.mobile_devices_path, {"devices": []})
        for item in payload.get("devices", []):
            if (
                isinstance(item, dict)
                and item.get("instance_id") == instance_id
                and item.get("device_id") == device_id
            ):
                return dict(item)
        return None

    def get_mobile_device_by_token(self, token: str, *, instance_id: str | None = None) -> dict[str, Any] | None:
        raw_token = str(token or "").strip()
        if not raw_token:
            return None
        payload = self._load_json(self.mobile_devices_path, {"devices": []})
        for item in payload.get("devices", []):
            if not isinstance(item, dict):
                continue
            if instance_id is not None and item.get("instance_id") != instance_id:
                continue
            if hmac.compare_digest(str(item.get("device_token") or ""), raw_token):
                return dict(item)
        return None

    def upsert_mobile_device(self, instance_id: str, device_id: str, label: str, *, device_token: str | None = None) -> None:
        payload = self._load_json(self.mobile_devices_path, {"devices": []})
        for d in payload["devices"]:
            if d.get("device_id") == device_id and d.get("instance_id") == instance_id:
                d["label"] = label
                d["last_seen"] = iso_now()
                if device_token:
                    d["device_token"] = device_token
                self._save_json(self.mobile_devices_path, payload)
                return
        record = {
            "device_id": device_id,
            "instance_id": instance_id,
            "label": label,
            "registered_at": iso_now(),
            "last_seen": iso_now(),
        }
        if device_token:
            record["device_token"] = device_token
        payload["devices"].append(record)
        self._save_json(self.mobile_devices_path, payload)

    def delete_mobile_device(self, instance_id: str, device_id: str) -> None:
        payload = self._load_json(self.mobile_devices_path, {"devices": []})
        payload["devices"] = [
            d for d in payload["devices"]
            if not (d.get("device_id") == device_id and d.get("instance_id") == instance_id)
        ]
        self._save_json(self.mobile_devices_path, payload)

    def clear_mobile_state_for_instance(self, instance_id: str) -> dict[str, int]:
        key = str(instance_id or "").strip()
        if not key:
            return {
                "pairing_tokens_removed": 0,
                "devices_removed": 0,
                "push_subscriptions_removed": 0,
                "transfer_tokens_removed": 0,
            }

        pairing_payload = self._load_json(self.pairing_tokens_path, {"tokens": []})
        pairing_tokens = [item for item in pairing_payload.get("tokens", []) if isinstance(item, dict)]
        next_pairing_tokens = [item for item in pairing_tokens if item.get("instance_id") != key]
        pairing_removed = len(pairing_tokens) - len(next_pairing_tokens)
        if pairing_removed:
            pairing_payload["tokens"] = next_pairing_tokens
            self._save_json(self.pairing_tokens_path, pairing_payload)

        devices_payload = self._load_json(self.mobile_devices_path, {"devices": []})
        devices = [item for item in devices_payload.get("devices", []) if isinstance(item, dict)]
        next_devices = [item for item in devices if item.get("instance_id") != key]
        devices_removed = len(devices) - len(next_devices)
        if devices_removed:
            devices_payload["devices"] = next_devices
            self._save_json(self.mobile_devices_path, devices_payload)

        subscriptions_payload = self._load_json(self.mobile_push_subscriptions_path, {"subscriptions": []})
        subscriptions = [item for item in subscriptions_payload.get("subscriptions", []) if isinstance(item, dict)]
        next_subscriptions = [item for item in subscriptions if item.get("instance_id") != key]
        subscriptions_removed = len(subscriptions) - len(next_subscriptions)
        if subscriptions_removed:
            subscriptions_payload["subscriptions"] = next_subscriptions
            self._save_json(self.mobile_push_subscriptions_path, subscriptions_payload)

        transfer_payload = self._load_json(self.mobile_transfer_tokens_path, {"tokens": []})
        transfer_tokens = [item for item in transfer_payload.get("tokens", []) if isinstance(item, dict)]
        next_transfer_tokens = []
        transfer_removed = 0
        for item in transfer_tokens:
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            device = payload.get("device") if isinstance(payload.get("device"), dict) else {}
            payload_instance_id = str(payload.get("instance_id") or "").strip()
            device_instance_id = str(device.get("instance_id") or "").strip()
            if payload_instance_id == key or device_instance_id == key:
                transfer_removed += 1
                continue
            next_transfer_tokens.append(item)
        if transfer_removed:
            transfer_payload["tokens"] = next_transfer_tokens
            self._save_json(self.mobile_transfer_tokens_path, transfer_payload)

        return {
            "pairing_tokens_removed": pairing_removed,
            "devices_removed": devices_removed,
            "push_subscriptions_removed": subscriptions_removed,
            "transfer_tokens_removed": transfer_removed,
        }

    def update_device_last_seen(self, instance_id: str, device_id: str) -> None:
        payload = self._load_json(self.mobile_devices_path, {"devices": []})
        for d in payload["devices"]:
            if d.get("device_id") == device_id and d.get("instance_id") == instance_id:
                d["last_seen"] = iso_now()
                self._save_json(self.mobile_devices_path, payload)
                return

    def get_mobile_push_keys(self) -> dict[str, Any] | None:
        payload = self._load_json(self.mobile_push_keys_path, {})
        public_key = str(payload.get("public_key") or "").strip()
        subject = str(payload.get("subject") or "").strip()
        if not public_key or not subject or not self.mobile_push_private_key_path.exists():
            return None
        return {
            "public_key": public_key,
            "subject": subject,
            "private_key_path": str(self.mobile_push_private_key_path),
        }

    def save_mobile_push_keys(self, *, public_key: str, private_key_pem: str, subject: str) -> dict[str, Any]:
        self.mobile_push_private_key_path.parent.mkdir(parents=True, exist_ok=True)
        self.mobile_push_private_key_path.write_text(str(private_key_pem or ""), encoding="utf-8")
        payload = {
            "public_key": str(public_key or "").strip(),
            "subject": str(subject or "").strip(),
            "created_at": iso_now(),
            "updated_at": iso_now(),
        }
        self._save_json(self.mobile_push_keys_path, payload)
        return {
            "public_key": payload["public_key"],
            "subject": payload["subject"],
            "private_key_path": str(self.mobile_push_private_key_path),
        }

    def list_mobile_push_subscriptions(self, instance_id: str, device_id: str | None = None) -> list[dict[str, Any]]:
        payload = self._load_json(self.mobile_push_subscriptions_path, {"subscriptions": []})
        result = [
            item
            for item in payload.get("subscriptions", [])
            if isinstance(item, dict)
            and item.get("instance_id") == instance_id
            and (device_id is None or item.get("device_id") == device_id)
        ]
        result.sort(key=lambda item: (str(item.get("device_id") or ""), str(item.get("updated_at") or "")))
        return result

    def upsert_mobile_push_subscription(
        self,
        *,
        instance_id: str,
        device_id: str,
        subscription: dict[str, Any],
        endpoint: str,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        payload = self._load_json(self.mobile_push_subscriptions_path, {"subscriptions": []})
        subscriptions = [item for item in payload.get("subscriptions", []) if isinstance(item, dict)]
        record = {
            "instance_id": instance_id,
            "device_id": device_id,
            "endpoint": endpoint,
            "subscription": subscription,
            "user_agent": (str(user_agent or "").strip()[:300] or None),
            "updated_at": iso_now(),
        }
        replaced = False
        for index, item in enumerate(subscriptions):
            if item.get("instance_id") == instance_id and item.get("device_id") == device_id:
                record["created_at"] = item.get("created_at") or iso_now()
                subscriptions[index] = record
                replaced = True
                break
        if not replaced:
            record["created_at"] = iso_now()
            subscriptions.append(record)
        payload["subscriptions"] = subscriptions
        self._save_json(self.mobile_push_subscriptions_path, payload)
        return record

    def delete_mobile_push_subscription(self, *, instance_id: str, device_id: str) -> bool:
        payload = self._load_json(self.mobile_push_subscriptions_path, {"subscriptions": []})
        subscriptions = [item for item in payload.get("subscriptions", []) if isinstance(item, dict)]
        next_subscriptions = [
            item
            for item in subscriptions
            if not (item.get("instance_id") == instance_id and item.get("device_id") == device_id)
        ]
        changed = len(next_subscriptions) != len(subscriptions)
        if changed:
            payload["subscriptions"] = next_subscriptions
            self._save_json(self.mobile_push_subscriptions_path, payload)
        return changed

    def create_mobile_transfer_token(self, *, token: str, payload: dict[str, Any], expires_at: str) -> None:
        store = self._load_json(self.mobile_transfer_tokens_path, {"tokens": []})
        store["tokens"] = [
            item
            for item in store.get("tokens", [])
            if isinstance(item, dict) and not item.get("used") and not is_session_expired(item.get("expires_at"))
        ]
        store["tokens"].append(
            {
                "token": token,
                "payload": payload,
                "expires_at": expires_at,
                "used": False,
                "created_at": iso_now(),
            }
        )
        self._save_json(self.mobile_transfer_tokens_path, store)

    def consume_mobile_transfer_token(self, token: str) -> dict[str, Any] | None:
        store = self._load_json(self.mobile_transfer_tokens_path, {"tokens": []})
        tokens = [item for item in store.get("tokens", []) if isinstance(item, dict)]
        found: dict[str, Any] | None = None
        dirty = False
        next_tokens: list[dict[str, Any]] = []
        for item in tokens:
            if item.get("used") or is_session_expired(item.get("expires_at")):
                dirty = True
                continue
            if hmac.compare_digest(str(item.get("token") or ""), str(token or "").strip()):
                dirty = True
                updated = dict(item)
                updated["used"] = True
                found = dict(updated.get("payload") or {}) if isinstance(updated.get("payload"), dict) else None
                continue
            next_tokens.append(item)
        if dirty:
            store["tokens"] = next_tokens
            self._save_json(self.mobile_transfer_tokens_path, store)
        return found

    # ── JSON helpers ────────────────────────────────────────────────

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
