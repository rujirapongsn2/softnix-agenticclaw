"""Admin service for operational metadata and safe config updates."""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import asyncio
import socket
import os
import signal
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from nanobot import __version__
from nanobot.admin.auth import (
    get_request_audit_context,
    new_csrf_token,
    new_session_id,
    new_user_id,
    hash_password,
    has_permission,
    iso_now,
    normalize_email,
    normalize_role,
    normalize_username,
    sanitize_user,
    verify_password,
)
from nanobot.admin.auth_store import AdminAuthStore
from nanobot.admin.layout import (
    bootstrap_softnix_instance,
    delete_softnix_instance,
    get_softnix_admin_dir,
    infer_softnix_home_from_registry,
    update_softnix_instance,
)
from nanobot.config.loader import get_config_path, load_config, save_config
from nanobot.config.schema import Config, MCPServerConfig
from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule
from nanobot.channels.access_requests import AccessRequestStore
from nanobot.providers.registry import PROVIDERS
from nanobot.runtime.audit import runtime_audit_path
from nanobot.session.manager import SessionManager
from nanobot.utils.helpers import sync_workspace_templates


def _expand_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    return Path(value).expanduser()


def _mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return f"{'*' * max(len(value) - keep, 3)}{value[-keep:]}"


def _file_mode(path: Path) -> str | None:
    try:
        return oct(path.stat().st_mode & 0o777)
    except OSError:
        return None


def _is_permissions_too_open(path: Path, allowed_mask: int) -> bool:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return False
    return (mode & ~allowed_mask) != 0


def _normalize_optional_int(value: Any, *, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _truncate_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _ts_sort_key(value: Any) -> tuple[float, str]:
    """Normalize timestamp values into a stable sortable key (epoch, raw)."""
    raw = str(value or "").strip()
    if not raw:
        return (0.0, "")
    normalized = raw
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt.timestamp(), raw)
    except Exception:
        return (0.0, raw)


def _safe_parse_ts(raw: str) -> float | None:
    """Parse an ISO timestamp string to epoch seconds, or *None* on failure."""
    raw = raw.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _safe_skill_name(name: str) -> str:
    """Validate and return a safe skill directory name."""
    normalized = (name or "").strip().replace("\\", "/").strip("/")
    if not normalized or "/" in normalized or ".." in normalized or normalized.startswith("."):
        raise ValueError(f"Invalid skill name: '{name}'")
    return normalized


def _parse_skill_frontmatter(raw: str) -> dict[str, Any]:
    """Parse YAML-style frontmatter from SKILL.md (simple key: value lines only)."""
    result: dict[str, Any] = {}
    if not raw.startswith("---"):
        return result
    end = raw.find("---", 3)
    if end == -1:
        return result
    fm_block = raw[3:end].strip()
    for line in fm_block.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if val.startswith("{") and val.endswith("}"):
            try:
                result[key] = json.loads(val)
            except Exception:
                result[key] = val
        else:
            result[key] = val
    return result


INSTANCE_MEMORY_FILES: tuple[str, ...] = (
    "AGENTS.md",
    "HEARTBEAT.md",
    "SOUL.md",
    "TOOLS.md",
    "USER.md",
    "memory/HISTORY.md",
    "memory/MEMORY.md",
)


@dataclass(frozen=True)
class InstanceTarget:
    """One admin-managed instance target."""

    id: str
    name: str
    config_path: Path
    workspace_path: Path
    source: str = "default"
    lifecycle: dict[str, Any] | None = None
    working_dir: Path | None = None
    owner: str | None = None
    environment: str | None = None
    instance_home: Path | None = None
    nanobot_bin: str | None = None


class AdminService:
    """Collect operational data and safe config updates for the admin API."""

    def __init__(
        self,
        *,
        config_path: Path | None = None,
        workspace: str | None = None,
        registry_path: Path | None = None,
    ):
        self.config_path = config_path or get_config_path()
        self.workspace_override = _expand_path(workspace)
        self.registry_path = registry_path
        self.auth_store = self._create_auth_store()
        self._sync_workspace_identities()

    def _create_auth_store(self) -> AdminAuthStore:
        if self.registry_path is not None:
            admin_dir = get_softnix_admin_dir(infer_softnix_home_from_registry(self.registry_path))
        else:
            admin_dir = self.config_path.expanduser().resolve().parent / ".nanobot-admin"
        return AdminAuthStore(admin_dir)

    def get_health(self) -> dict[str, Any]:
        instances = self.list_instances()
        warnings = sum(len(item["security"]["findings"]) for item in instances)
        return {
            "status": "ok",
            "service": "nanobot-admin",
            "version": __version__,
            "mode": "safe-config",
            "instance_count": len(instances),
            "warning_count": warnings,
            "capabilities": {
                "runtime_state": any(item["runtime"]["probe"]["available"] for item in instances),
                "instance_control": any(item["runtime"]["manageable"] for item in instances),
                "config_write": True,
            },
        }

    def get_mobile_pairing_data(self, instance_id: str) -> dict[str, Any]:
        """Generate temporary pairing data for mobile app QR scan."""
        # Find instance config to get public URL hint if available
        config = self.get_instance_config(instance_id)
        
        # In a real setup, this would include a short-lived token
        # For now, we provide the essentials for pairing
        return {
            "instance_id": instance_id,
            "pairing_token": f"pair-{new_csrf_token()[:12]}",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        }

    def relay_mobile_message(self, instance_id: str, sender_id: str, text: str) -> dict[str, Any]:
        """Relay a message from a mobile app to a specific instance via file-based queue."""
        target = next((t for t in self._load_targets() if t.id == instance_id), None)
        if not target:
            raise ValueError(f"Instance '{instance_id}' not found")
            
        relay_dir = target.workspace_path / "mobile_relay"
        relay_dir.mkdir(parents=True, exist_ok=True)
        inbound_file = relay_dir / "inbound.jsonl"
        
        data = {
            "text": text,
            "sender_id": sender_id,
            "timestamp": iso_now(),
        }
        
        with inbound_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")
            
        return {"status": "relayed", "instance_id": instance_id}

    def get_mobile_replies(self, instance_id: str, sender_id: str) -> list[dict[str, Any]]:
        """Fetch agent replies for a mobile user from the outbound queue."""
        target = next((t for t in self._load_targets() if t.id == instance_id), None)
        if not target:
            return []
            
        outbound_file = target.workspace_path / "mobile_relay" / "outbound.jsonl"
        if not outbound_file.exists():
            return []
            
        all_replies = []
        remaining_lines = []
        
        try:
            lines = outbound_file.read_text().splitlines()
            for line in lines:
                if not line.strip():
                    continue
                data = json.loads(line)
                if data.get("sender_id") == sender_id:
                    all_replies.append(data)
                else:
                    remaining_lines.append(line)
            
            # Update file to remove fetched messages
            if all_replies:
                outbound_file.write_text("\n".join(remaining_lines) + ("\n" if remaining_lines else ""))
                
        except Exception as e:
            logger.error(f"Error fetching mobile replies: {e}")
            
        return all_replies

    def register_mobile_client(self, instance_id: str, device_id: str) -> dict[str, Any]:
        """Add a mobile device ID to the instance's allow_from list."""
        config = self.get_instance_config(instance_id)
        
        # Ensure softnix_app channel is enabled
        if not config.channels.softnix_app.enabled:
            config.channels.softnix_app.enabled = True
            
        # Add to allow_from if not already there
        if device_id not in config.channels.softnix_app.allow_from:
            config.channels.softnix_app.allow_from.append(device_id)
            self.update_instance_config(instance_id, config)
            return {"status": "registered", "new": True}
            
        return {"status": "registered", "new": False}

    def _sync_workspace_identities(self) -> None:
        """Best-effort sync of per-instance agent identity into workspace prompt files."""
        for target in self._load_targets():
            workspace_path = target.workspace_path
            if not workspace_path.exists():
                continue
            agent_name = target.name or target.id
            try:
                sync_workspace_templates(
                    workspace_path,
                    silent=True,
                    agent_name=agent_name,
                    apply_identity=True,
                )
            except Exception:
                continue

    def _audit_dir(self) -> Path:
        if self.registry_path is not None:
            return get_softnix_admin_dir(infer_softnix_home_from_registry(self.registry_path)) / "audit"
        return self.config_path.expanduser().resolve().parent / ".nanobot-admin" / "audit"

    def _audit_path(self, instance_id: str) -> Path:
        return self._audit_dir() / f"{instance_id}.jsonl"

    def _append_audit_event(
        self,
        *,
        instance_id: str,
        event_type: str,
        severity: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> None:
        path = self._audit_path(instance_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {
            "ts": datetime.now().astimezone().isoformat(),
            "instance_id": instance_id,
            "event_type": event_type,
            "severity": severity,
            "payload": payload or {},
        }
        actor = get_request_audit_context()
        if actor:
            record["actor"] = actor
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def has_admin_users(self) -> bool:
        return self.auth_store.has_users()

    def get_auth_audit_log(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        category: str = "all",
        outcome: str = "all",
        search: str = "",
    ) -> dict[str, Any]:
        """Read and filter the auth audit log for the Security Audit viewer."""
        audit_path = self.auth_store.audit_path
        if not audit_path.exists():
            return {"events": [], "total": 0, "offset": offset, "limit": limit}
        try:
            raw_lines = [line for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except Exception:
            return {"events": [], "total": 0, "offset": offset, "limit": limit}

        events: list[dict[str, Any]] = []
        for raw in raw_lines:
            try:
                record = json.loads(raw)
            except Exception:
                continue
            if not isinstance(record, dict):
                continue
            ev_category = str(record.get("category") or "")
            ev_outcome = str(record.get("outcome") or "")
            if category != "all" and ev_category != category:
                continue
            if outcome != "all" and ev_outcome != outcome:
                continue
            if search:
                needle = search.lower()
                actor = record.get("actor") or {}
                resource = record.get("resource") or {}
                detail = record.get("detail") or {}
                haystack = " ".join([
                    str(record.get("event_type") or ""),
                    str(record.get("category") or ""),
                    str(record.get("outcome") or ""),
                    str(actor.get("username") or ""),
                    str(actor.get("user_id") or ""),
                    str(actor.get("ip") or ""),
                    str(resource.get("name") or ""),
                    str(resource.get("id") or ""),
                    str(resource.get("type") or ""),
                    json.dumps(detail, ensure_ascii=False),
                ]).lower()
                if needle not in haystack:
                    continue
            events.append(record)

        events.sort(key=lambda e: str(e.get("ts") or ""), reverse=True)
        total = len(events)
        page = events[offset : offset + limit]
        return {"events": page, "total": total, "offset": offset, "limit": limit}

    def bootstrap_admin_user(
        self,
        *,
        username: str,
        password: str,
        display_name: str | None = None,
        email: str | None = None,
    ) -> dict[str, Any]:
        if self.auth_store.has_users():
            raise ValueError("Bootstrap is no longer available")
        now = iso_now()
        user = self.auth_store.upsert_user(
            {
                "id": new_user_id(),
                "username": normalize_username(username),
                "display_name": str(display_name or "").strip() or normalize_username(username),
                "email": normalize_email(email),
                "role": "owner",
                "status": "active",
                "password_hash": hash_password(password),
                "created_at": now,
                "updated_at": now,
                "last_login_at": None,
            }
        )
        self.auth_store.append_audit(
            event_type="auth.bootstrap",
            category="authentication",
            outcome="success",
            resource={"type": "user", "id": user["id"], "name": user["username"]},
            payload={"role": "owner"},
        )
        return sanitize_user(user)

    def authenticate_admin_user(
        self,
        *,
        login: str,
        password: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        raw_login = str(login or "").strip()
        if not raw_login:
            raise ValueError("Username or email is required")
        user = self.auth_store.get_user_by_username(raw_login) or self.auth_store.get_user_by_email(raw_login)
        if user is None or str(user.get("status") or "active") != "active":
            self.auth_store.append_audit(
                event_type="auth.login_failed",
                category="authentication",
                outcome="failure",
                actor={"ip": ip, "user_agent": (user_agent or "")[:300] or None},
                payload={"login": raw_login, "reason": "unknown_user_or_disabled"},
            )
            raise ValueError("Invalid username/email or password")
        if not verify_password(password, str(user.get("password_hash") or "")):
            self.auth_store.append_audit(
                event_type="auth.login_failed",
                category="authentication",
                outcome="failure",
                actor={"user_id": user.get("id"), "username": user.get("username"), "ip": ip, "user_agent": (user_agent or "")[:300] or None},
                payload={"login": raw_login, "reason": "bad_password"},
            )
            raise ValueError("Invalid username/email or password")
        csrf_token = new_csrf_token()
        session = self.auth_store.create_session(
            session_id=new_session_id(),
            user_id=str(user.get("id") or ""),
            ip=ip,
            user_agent=user_agent,
            csrf_token=csrf_token,
        )
        updated_user = dict(user)
        updated_user["last_login_at"] = iso_now()
        updated_user["updated_at"] = iso_now()
        self.auth_store.upsert_user(updated_user)
        self.auth_store.append_audit(
            event_type="auth.login_succeeded",
            category="authentication",
            outcome="success",
            actor={
                "user_id": user.get("id"),
                "username": user.get("username"),
                "role": user.get("role"),
                "ip": ip,
                "user_agent": (user_agent or "")[:300] or None,
            },
            resource={"type": "session", "id": session["id"]},
        )
        return {
            "user": sanitize_user(updated_user),
            "session": {
                "id": session["id"],
                "expires_at": session.get("expires_at"),
                "idle_expires_at": session.get("idle_expires_at"),
                "csrf_token": session.get("csrf_token"),
            },
        }

    def get_authenticated_user(self, *, session_id: str) -> dict[str, Any] | None:
        session = self.auth_store.get_session(session_id)
        if session is None:
            return None
        user = self.auth_store.get_user_by_id(str(session.get("user_id") or ""))
        if user is None or str(user.get("status") or "active") != "active":
            self.auth_store.revoke_session(session_id)
            return None
        csrf_token = str(session.get("csrf_token") or "")
        if not csrf_token:
            csrf_token = new_csrf_token()
        session = self.auth_store.touch_session(session_id, csrf_token=csrf_token) or session
        return {
            "user": sanitize_user(user),
            "session": {
                "id": session.get("id"),
                "expires_at": session.get("expires_at"),
                "idle_expires_at": session.get("idle_expires_at"),
                "csrf_token": session.get("csrf_token"),
            },
        }

    def logout_admin_session(self, *, session_id: str) -> dict[str, Any]:
        revoked = self.auth_store.revoke_session(session_id)
        if revoked:
            self.auth_store.append_audit(
                event_type="auth.logout",
                category="authentication",
                outcome="success",
                resource={"type": "session", "id": session_id},
            )
        return {"ok": True}

    def list_admin_users(self) -> dict[str, Any]:
        users = [sanitize_user(user) for user in self.auth_store.list_users(include_disabled=True)]
        return {"users": users, "count": len(users)}

    def create_admin_user(
        self,
        *,
        username: str,
        password: str,
        display_name: str | None = None,
        email: str | None = None,
        role: str = "viewer",
        status: str = "active",
    ) -> dict[str, Any]:
        normalized_username = normalize_username(username)
        if not normalized_username:
            raise ValueError("Username is required")
        if self.auth_store.get_user_by_username(normalized_username) is not None:
            raise ValueError(f"Username '{normalized_username}' already exists")
        normalized_email = normalize_email(email)
        if normalized_email and self.auth_store.get_user_by_email(normalized_email) is not None:
            raise ValueError(f"Email '{normalized_email}' already exists")
        now = iso_now()
        user = self.auth_store.upsert_user(
            {
                "id": new_user_id(),
                "username": normalized_username,
                "display_name": str(display_name or "").strip() or normalized_username,
                "email": normalized_email,
                "role": normalize_role(role),
                "status": str(status or "active").strip().lower() or "active",
                "password_hash": hash_password(password),
                "created_at": now,
                "updated_at": now,
                "last_login_at": None,
            }
        )
        self.auth_store.append_audit(
            event_type="user.created",
            category="user_management",
            outcome="success",
            resource={"type": "user", "id": user["id"], "name": user["username"]},
            payload={"role": user["role"], "status": user.get("status", "active")},
        )
        return {"user": sanitize_user(user)}

    def update_admin_user(
        self,
        *,
        user_id: str,
        display_name: str | None = None,
        email: str | None = None,
        role: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        user = self.auth_store.get_user_by_id(user_id)
        if user is None:
            raise ValueError("User not found")
        updated = dict(user)
        if display_name is not None:
            updated["display_name"] = str(display_name).strip() or updated.get("username")
        if email is not None:
            normalized_email = normalize_email(email)
            existing = self.auth_store.get_user_by_email(normalized_email or "")
            if existing is not None and str(existing.get("id")) != str(user_id):
                raise ValueError(f"Email '{normalized_email}' already exists")
            updated["email"] = normalized_email
        if role is not None:
            updated["role"] = normalize_role(role)
        if status is not None:
            next_status = str(status or "").strip().lower() or "active"
            if next_status == "disabled" and self._is_last_owner(user_id=user_id):
                raise ValueError("Cannot disable the last owner")
            updated["status"] = next_status
            if next_status == "disabled":
                self.auth_store.revoke_sessions_for_user(user_id)
        updated["updated_at"] = iso_now()
        updated = self.auth_store.upsert_user(updated)
        self.auth_store.append_audit(
            event_type="user.updated",
            category="user_management",
            outcome="success",
            resource={"type": "user", "id": updated["id"], "name": updated["username"]},
            payload={"role": updated["role"], "status": updated["status"]},
        )
        return {"user": sanitize_user(updated)}

    def reset_admin_user_password(self, *, user_id: str, new_password: str) -> dict[str, Any]:
        user = self.auth_store.get_user_by_id(user_id)
        if user is None:
            raise ValueError("User not found")
        updated = dict(user)
        updated["password_hash"] = hash_password(new_password)
        updated["updated_at"] = iso_now()
        self.auth_store.upsert_user(updated)
        self.auth_store.revoke_sessions_for_user(user_id)
        self.auth_store.append_audit(
            event_type="user.password_reset",
            category="user_management",
            outcome="success",
            resource={"type": "user", "id": updated["id"], "name": updated["username"]},
        )
        return {"ok": True}

    def change_admin_password(self, *, user_id: str, current_password: str, new_password: str) -> dict[str, Any]:
        user = self.auth_store.get_user_by_id(user_id)
        if user is None:
            raise ValueError("User not found")
        if not verify_password(current_password, str(user.get("password_hash") or "")):
            self.auth_store.append_audit(
                event_type="auth.password_change_failed",
                category="authentication",
                outcome="failure",
                resource={"type": "user", "id": user["id"], "name": user.get("username")},
                payload={"reason": "bad_current_password"},
            )
            raise ValueError("Current password is incorrect")
        updated = dict(user)
        updated["password_hash"] = hash_password(new_password)
        updated["updated_at"] = iso_now()
        self.auth_store.upsert_user(updated)
        self.auth_store.revoke_sessions_for_user(user_id)
        self.auth_store.append_audit(
            event_type="auth.password_changed",
            category="authentication",
            outcome="success",
            resource={"type": "user", "id": updated["id"], "name": updated["username"]},
        )
        return {"ok": True}

    def _is_last_owner(self, *, user_id: str) -> bool:
        owners = [
            item
            for item in self.auth_store.list_users(include_disabled=False)
            if normalize_role(str(item.get("role") or "")) == "owner"
        ]
        return len(owners) == 1 and str(owners[0].get("id") or "") == str(user_id)

    def get_overview(self) -> dict[str, Any]:
        instances = self.list_instances()
        enabled_channels = sorted(
            {channel["name"] for item in instances for channel in item["channels"] if channel["enabled"]}
        )
        return {
            "instances": instances,
            "summary": {
                "instance_count": len(instances),
                "enabled_channels": enabled_channels,
                "enabled_channel_count": len(enabled_channels),
                "session_count": sum(item["sessions"]["count"] for item in instances),
                "cron_job_count": sum(item["cron"]["jobs"] for item in instances),
                "mcp_server_count": sum(item["mcp"]["server_count"] for item in instances),
                "warning_count": sum(len(item["security"]["findings"]) for item in instances),
            },
        }

    def list_instances(self) -> list[dict[str, Any]]:
        return [self._collect_instance(target) for target in self._load_targets()]

    def get_instance(self, instance_id: str) -> dict[str, Any] | None:
        for target in self._load_targets():
            if target.id == instance_id:
                return self._collect_instance(target)
        return None

    def list_channels(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for instance in self.list_instances():
            for channel in instance["channels"]:
                rows.append(
                    {
                        "instance_id": instance["id"],
                        "instance_name": instance["name"],
                        **channel,
                    }
                )
        return rows

    def list_access_requests(self) -> dict[str, Any]:
        requests: list[dict[str, Any]] = []
        for target in self._load_targets():
            store = AccessRequestStore(target.workspace_path)
            for item in store.list_pending():
                requests.append(
                    {
                        "instance_id": target.id,
                        "instance_name": target.name,
                        **item,
                    }
                )
        requests.sort(key=lambda item: str(item.get("last_seen") or ""), reverse=True)
        return {
            "requests": requests,
            "count": len(requests),
        }

    def list_providers(self) -> dict[str, Any]:
        instances = self.list_instances()
        return {
            "instances": [
                {
                    "instance_id": item["id"],
                    "instance_name": item["name"],
                    "model": item["model"],
                    "selected_provider": item["selected_provider"],
                    "providers": item["providers"],
                }
                for item in instances
            ]
        }

    def list_mcp_servers(self) -> dict[str, Any]:
        instances = self.list_instances()
        return {
            "instances": [
                {
                    "instance_id": item["id"],
                    "instance_name": item["name"],
                    "mcp": item["mcp"],
                }
                for item in instances
            ]
        }

    def get_security(self) -> dict[str, Any]:
        instances = self.list_instances()
        findings = []
        for item in instances:
            for finding in item["security"]["findings"]:
                findings.append(
                    {
                        "instance_id": item["id"],
                        "instance_name": item["name"],
                        **finding,
                    }
                )
        return {
            "instances": [
                {
                    "instance_id": item["id"],
                    "instance_name": item["name"],
                    "security": item["security"],
                }
                for item in instances
            ],
            "findings": findings,
        }

    def get_runtime_audit_events(
        self,
        *,
        instance_id: str,
        limit: int = 40,
        cursor: int | str | None = None,
        status: str | None = None,
        operation: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Return paginated runtime-audit events for one instance."""
        target = self._get_target(instance_id)
        all_events = self._read_runtime_audit_events(
            workspace_path=target.workspace_path,
            instance_id=target.id,
            instance_name=target.name,
        )
        summary = self._summarize_runtime_audit_events(all_events)

        status_filter = str(status or "all").strip().lower()
        operation_filter = str(operation or "all").strip().lower()
        search_filter = str(search or "").strip().lower()
        if status_filter not in {"all", "ok", "error"}:
            raise ValueError("status must be one of: all, ok, error")
        if operation_filter in {"", "*"}:
            operation_filter = "all"

        filtered = self._filter_runtime_audit_events(
            all_events,
            status_filter=status_filter,
            operation_filter=operation_filter,
            search_filter=search_filter,
        )
        filtered.sort(
            key=lambda item: (_ts_sort_key(item.get("ts")), item.get("line") or 0),
            reverse=True,
        )

        cursor_value = _normalize_optional_int(cursor, field_name="cursor")
        start = max(cursor_value or 0, 0)
        limit_value = _normalize_optional_int(limit, field_name="limit") or 40
        limit_value = max(1, min(limit_value, 200))
        page = filtered[start : start + limit_value]
        next_cursor = start + len(page)

        return {
            "instance_id": target.id,
            "instance_name": target.name,
            "path": str(runtime_audit_path(target.workspace_path)),
            "exists": runtime_audit_path(target.workspace_path).exists(),
            "summary": summary,
            "filters": {
                "status": status_filter,
                "operation": operation_filter,
                "search": search_filter,
            },
            "count": len(page),
            "filtered_count": len(filtered),
            "events": page,
            "next_cursor": next_cursor if next_cursor < len(filtered) else None,
        }

    def get_activity(self, *, limit: int = 50) -> dict[str, Any]:
        """Collect recent activity across configured instances."""
        events: list[dict[str, Any]] = []
        instances = self.list_instances()
        for item in instances:
            events.extend(self._collect_session_events(item, limit=limit))
            events.extend(self._collect_cron_events(item))

        if not events:
            for item in instances:
                events.append(self._collect_runtime_snapshot_event(item))

        events.sort(key=lambda event: _ts_sort_key(event.get("ts")), reverse=True)
        return {
            "events": events[:limit],
            "count": min(len(events), limit),
        }

    def get_activity_debug(self, *, limit: int = 50) -> dict[str, Any]:
        """Collect recent activity with parser diagnostics per instance."""
        events: list[dict[str, Any]] = []
        instances = self.list_instances()
        diagnostics: list[dict[str, Any]] = []

        for item in instances:
            instance_diag: dict[str, Any] = {
                "instance_id": item["id"],
                "instance_name": item["name"],
                "session_files_seen": 0,
                "session_files_missing": 0,
                "session_files_read": 0,
                "session_read_errors": 0,
                "session_lines_considered": 0,
                "session_metadata_lines_skipped": 0,
                "session_json_parse_errors": 0,
                "session_events": 0,
                "cron_events": 0,
                "sample_errors": [],
            }
            session_events = self._collect_session_events(item, limit=limit, diagnostics=instance_diag)
            cron_events = self._collect_cron_events(item)
            instance_diag["session_events"] = len(session_events)
            instance_diag["cron_events"] = len(cron_events)
            diagnostics.append(instance_diag)
            events.extend(session_events)
            events.extend(cron_events)

        runtime_fallback = False
        if not events:
            runtime_fallback = True
            for item in instances:
                events.append(self._collect_runtime_snapshot_event(item))

        events.sort(key=lambda event: _ts_sort_key(event.get("ts")), reverse=True)
        return {
            "events": events[:limit],
            "count": min(len(events), limit),
            "runtime_fallback": runtime_fallback,
            "instances": diagnostics,
        }

    def get_activity_heatmap(
        self,
        *,
        instance_id: str | None = None,
        period: str = "week",
        days: int = 30,
    ) -> dict[str, Any]:
        """Generate activity heatmap data for visualization."""
        instances = self.list_instances()
        if instance_id:
            instances = [inst for inst in instances if inst["id"] == instance_id]

        # Collect all events with timestamps
        all_events: list[dict[str, Any]] = []
        for instance in instances:
            all_events.extend(self._collect_session_events(instance, limit=10000))
            all_events.extend(self._collect_cron_events(instance))

        # Parse timestamps and build timeline
        now = datetime.now(timezone.utc)
        start_date = now - timedelta(days=days)
        
        # Initialize timeline buckets (daily)
        timeline: dict[str, int] = {}
        for i in range(days + 1):
            date = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
            timeline[date] = 0

        # Initialize heatmap buckets (day of week x hour)
        heatmap: dict[str, list[int]] = {
            "Mon": [0] * 24,
            "Tue": [0] * 24,
            "Wed": [0] * 24,
            "Thu": [0] * 24,
            "Fri": [0] * 24,
            "Sat": [0] * 24,
            "Sun": [0] * 24,
        }
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        # Aggregate events
        for event in all_events:
            ts_str = event.get("ts")
            if not ts_str:
                continue
            
            try:
                # Parse timestamp
                ts_normalized = str(ts_str).strip()
                if ts_normalized.endswith("Z"):
                    ts_normalized = f"{ts_normalized[:-1]}+00:00"
                
                dt = datetime.fromisoformat(ts_normalized)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                
                # Skip events outside our range
                if dt < start_date or dt > now:
                    continue
                
                # Update timeline (daily count)
                date_key = dt.strftime("%Y-%m-%d")
                if date_key in timeline:
                    timeline[date_key] += 1
                
                # Update heatmap (day of week x hour)
                day_of_week = dt.weekday()  # 0=Monday, 6=Sunday
                hour = dt.hour
                day_name = day_names[day_of_week]
                heatmap[day_name][hour] += 1
                
            except Exception:
                continue

        # Convert timeline to sorted list
        timeline_list = [
            {"date": date, "count": count}
            for date, count in sorted(timeline.items())
        ]

        return {
            "period": period,
            "days": days,
            "timeline": timeline_list,
            "heatmap": heatmap,
            "total_events": sum(timeline.values()),
            "instance_count": len(instances),
        }

    def list_schedules(self) -> dict[str, Any]:
        """List schedules across instances."""
        instances = []
        for target in self._load_targets():
            cron = self._cron_service_for_target(target)
            jobs = cron.list_jobs(include_disabled=True)
            instances.append(
                {
                    "instance_id": target.id,
                    "instance_name": target.name,
                    "jobs": [self._serialize_cron_job(job) for job in jobs],
                }
            )
        return {"instances": instances}

    def get_instance_memory_files(self, *, instance_id: str) -> dict[str, Any]:
        """Read editable memory/prompt markdown files from instance workspace."""
        target = self._get_target(instance_id)
        workspace = target.workspace_path
        files: list[dict[str, Any]] = []
        for relative_path in INSTANCE_MEMORY_FILES:
            file_path = workspace / relative_path
            exists = file_path.exists()
            content = ""
            if exists:
                try:
                    content = file_path.read_text(encoding="utf-8")
                except Exception as exc:
                    raise ValueError(f"Unable to read '{relative_path}': {exc}") from exc
            files.append(
                {
                    "path": relative_path,
                    "exists": exists,
                    "content": content,
                }
            )
        return {
            "instance_id": target.id,
            "workspace_path": str(workspace),
            "files": files,
        }

    def update_instance_memory_file(self, *, instance_id: str, relative_path: str, content: str) -> dict[str, Any]:
        """Update one allowed memory/prompt markdown file."""
        target = self._get_target(instance_id)
        normalized = (relative_path or "").strip().replace("\\", "/").lstrip("/")
        if normalized not in INSTANCE_MEMORY_FILES:
            allowed = ", ".join(INSTANCE_MEMORY_FILES)
            raise ValueError(f"Unsupported memory file '{relative_path}'. Allowed: {allowed}")
        if not isinstance(content, str):
            raise ValueError("content must be a string")

        file_path = target.workspace_path / normalized
        file_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            file_path.write_text(content, encoding="utf-8")
        except Exception as exc:
            raise ValueError(f"Unable to write '{normalized}': {exc}") from exc

        self._append_audit_event(
            instance_id=target.id,
            event_type="instance.memory_file_updated",
            payload={
                "path": normalized,
                "size": len(content),
            },
        )
        return {
            "instance_id": target.id,
            "workspace_path": str(target.workspace_path),
            "path": normalized,
            "exists": file_path.exists(),
            "content": content,
        }

    def list_instance_skills(self, *, instance_id: str) -> dict[str, Any]:
        """List skills in workspace/skills/, parsing SKILL.md frontmatter."""
        target = self._get_target(instance_id)
        skills_dir = target.workspace_path / "skills"
        skills: list[dict[str, Any]] = []
        if not skills_dir.is_dir():
            return {"instance_id": target.id, "skills": []}
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_name = skill_dir.name
            skill_md = skill_dir / "SKILL.md"
            name = skill_name
            description = ""
            metadata: dict[str, Any] = {}
            if skill_md.exists():
                try:
                    raw = skill_md.read_text(encoding="utf-8")
                    fm = _parse_skill_frontmatter(raw)
                    name = fm.get("name") or skill_name
                    description = fm.get("description") or ""
                    metadata = fm.get("metadata") or {}
                except Exception:
                    pass
            file_count = sum(1 for _ in skill_dir.rglob("*") if _.is_file())
            skills.append({
                "skill_name": skill_name,
                "name": name,
                "description": description,
                "metadata": metadata,
                "file_count": file_count,
                "has_skill_md": skill_md.exists(),
            })
        return {"instance_id": target.id, "skills": skills}

    def get_instance_skill(self, *, instance_id: str, skill_name: str) -> dict[str, Any]:
        """Return all files in workspace/skills/{skill_name}/ with content."""
        target = self._get_target(instance_id)
        skill_dir = target.workspace_path / "skills" / _safe_skill_name(skill_name)
        if not skill_dir.is_dir():
            raise ValueError(f"Skill '{skill_name}' not found")
        files: list[dict[str, Any]] = []
        for file_path in sorted(skill_dir.rglob("*")):
            if not file_path.is_file():
                continue
            relative = str(file_path.relative_to(skill_dir))
            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception:
                content = ""
            files.append({"path": relative, "content": content, "size": file_path.stat().st_size})
        return {"instance_id": target.id, "skill_name": skill_name, "files": files}

    def update_instance_skill_file(
        self,
        *,
        instance_id: str,
        skill_name: str,
        relative_path: str,
        content: str,
    ) -> dict[str, Any]:
        """Update one file within workspace/skills/{skill_name}/."""
        target = self._get_target(instance_id)
        safe_name = _safe_skill_name(skill_name)
        skill_dir = target.workspace_path / "skills" / safe_name
        if not skill_dir.is_dir():
            raise ValueError(f"Skill '{skill_name}' not found")
        normalized = (relative_path or "").strip().replace("\\", "/").lstrip("/")
        if not normalized or ".." in normalized:
            raise ValueError(f"Invalid file path: '{relative_path}'")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        file_path = (skill_dir / normalized).resolve()
        if not str(file_path).startswith(str(skill_dir.resolve())):
            raise ValueError(f"Path '{relative_path}' escapes skill directory")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            file_path.write_text(content, encoding="utf-8")
        except Exception as exc:
            raise ValueError(f"Unable to write '{normalized}': {exc}") from exc
        self._append_audit_event(
            instance_id=target.id,
            event_type="instance.skill_file_updated",
            payload={"skill_name": skill_name, "path": normalized, "size": len(content)},
        )
        return {"instance_id": target.id, "skill_name": skill_name, "path": normalized, "content": content}

    def delete_instance_skill(self, *, instance_id: str, skill_name: str) -> dict[str, Any]:
        """Delete an entire skill directory from workspace/skills/."""
        target = self._get_target(instance_id)
        safe_name = _safe_skill_name(skill_name)
        skill_dir = target.workspace_path / "skills" / safe_name
        if not skill_dir.is_dir():
            raise ValueError(f"Skill '{skill_name}' not found")
        try:
            shutil.rmtree(skill_dir)
        except Exception as exc:
            raise ValueError(f"Unable to delete skill '{skill_name}': {exc}") from exc
        self._append_audit_event(
            instance_id=target.id,
            event_type="instance.skill_deleted",
            severity="warning",
            payload={"skill_name": skill_name},
        )
        return {"instance_id": target.id, "skill_name": skill_name, "deleted": True}

    def get_instance_config(self, *, instance_id: str) -> dict[str, Any]:
        """Return one instance config as JSON-ready data."""
        target = self._get_target(instance_id)
        config = self._load_target_config(target)
        return {
            "instance_id": target.id,
            "config_path": str(target.config_path),
            "config": config.model_dump(by_alias=True),
        }

    def update_instance_config(self, *, instance_id: str, config_data: dict[str, Any]) -> dict[str, Any]:
        """Validate and save one instance config from raw JSON data."""
        target = self._get_target(instance_id)
        try:
            config = Config.model_validate(config_data)
        except Exception as exc:
            raise ValueError(f"Invalid config: {exc}") from exc
        save_config(config, target.config_path)
        if self.registry_path and target.source == "registry":
            update_softnix_instance(
                registry_path=self.registry_path,
                instance_id=target.id,
                gateway_port=int(config.gateway.port),
                runtime_mode=config.runtime.mode,
                sandbox_profile=getattr(config.runtime.sandbox, "profile", "balanced"),
                sandbox_image=config.runtime.sandbox.image,
                sandbox_execution_strategy=config.runtime.sandbox.execution_strategy,
                sandbox_cpu_limit=config.runtime.sandbox.cpu_limit,
                sandbox_memory_limit=config.runtime.sandbox.memory_limit,
                sandbox_pids_limit=int(config.runtime.sandbox.pids_limit),
                sandbox_tmpfs_size_mb=int(config.runtime.sandbox.tmpfs_size_mb),
                sandbox_network_policy=config.runtime.sandbox.network_policy,
                sandbox_timeout_seconds=int(config.runtime.sandbox.timeout_seconds),
            )
        self._append_audit_event(
            instance_id=target.id,
            event_type="instance.config_updated",
            payload={
                "runtime_mode": config.runtime.mode,
                "gateway_port": int(config.gateway.port),
            },
        )
        return {
            "instance": self._collect_instance(target),
            "config_path": str(target.config_path),
            "config": config.model_dump(by_alias=True),
        }

    def create_instance(
        self,
        *,
        instance_id: str,
        name: str,
        owner: str,
        env: str,
        repo_root: str,
        nanobot_bin: str,
        source_config: str | None = None,
        gateway_port: int | None = None,
        runtime_mode: str | None = None,
        sandbox_profile: str | None = "balanced",
        sandbox_image: str | None = None,
        sandbox_execution_strategy: str | None = None,
        sandbox_cpu_limit: str | None = None,
        sandbox_memory_limit: str | None = None,
        sandbox_pids_limit: int | None = None,
        sandbox_tmpfs_size_mb: int | None = None,
        sandbox_network_policy: str | None = None,
        sandbox_timeout_seconds: int | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Create one instance in the configured registry."""
        registry_path = self._require_registry_path()
        gateway_port = _normalize_optional_int(gateway_port, field_name="gateway_port")
        result = bootstrap_softnix_instance(
            instance_id=instance_id,
            name=name,
            owner=owner,
            env=env,
            nanobot_bin=nanobot_bin,
            repo_root=Path(repo_root).expanduser(),
            base_dir=infer_softnix_home_from_registry(registry_path),
            source_config=Path(source_config).expanduser() if source_config else None,
            gateway_port=gateway_port,
            runtime_mode=runtime_mode,
            sandbox_profile=sandbox_profile,
            sandbox_image=sandbox_image,
            sandbox_execution_strategy=sandbox_execution_strategy,
            sandbox_cpu_limit=sandbox_cpu_limit,
            sandbox_memory_limit=sandbox_memory_limit,
            sandbox_pids_limit=_normalize_optional_int(
                sandbox_pids_limit,
                field_name="sandbox_pids_limit",
            ),
            sandbox_tmpfs_size_mb=_normalize_optional_int(
                sandbox_tmpfs_size_mb,
                field_name="sandbox_tmpfs_size_mb",
            ),
            sandbox_network_policy=sandbox_network_policy,
            sandbox_timeout_seconds=_normalize_optional_int(
                sandbox_timeout_seconds,
                field_name="sandbox_timeout_seconds",
            ),
            force=force,
        )
        instance = self.get_instance(instance_id)
        self._append_audit_event(
            instance_id=instance_id,
            event_type="instance.created",
            payload={
                "runtime_mode": instance["runtime_config"]["mode"],
                "gateway_port": instance["gateway_port"],
                "config_path": instance["config_path"],
            },
        )
        return {"instance": instance, "registry_entry": result["registry_entry"]}

    def update_instance(
        self,
        *,
        instance_id: str,
        name: str | None = None,
        owner: str | None = None,
        env: str | None = None,
        repo_root: str | None = None,
        nanobot_bin: str | None = None,
        gateway_port: int | None = None,
        runtime_mode: str | None = None,
        sandbox_profile: str | None = None,
        sandbox_image: str | None = None,
        sandbox_execution_strategy: str | None = None,
        sandbox_cpu_limit: str | None = None,
        sandbox_memory_limit: str | None = None,
        sandbox_pids_limit: int | None = None,
        sandbox_tmpfs_size_mb: int | None = None,
        sandbox_network_policy: str | None = None,
        sandbox_timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Update one registry-backed instance."""
        registry_path = self._require_registry_path()
        gateway_port = _normalize_optional_int(gateway_port, field_name="gateway_port")
        entry = update_softnix_instance(
            registry_path=registry_path,
            instance_id=instance_id,
            name=name,
            owner=owner,
            env=env,
            repo_root=Path(repo_root).expanduser() if repo_root else None,
            nanobot_bin=nanobot_bin,
            gateway_port=gateway_port,
            runtime_mode=runtime_mode,
            sandbox_profile=sandbox_profile,
            sandbox_image=sandbox_image,
            sandbox_execution_strategy=sandbox_execution_strategy,
            sandbox_cpu_limit=sandbox_cpu_limit,
            sandbox_memory_limit=sandbox_memory_limit,
            sandbox_pids_limit=_normalize_optional_int(
                sandbox_pids_limit,
                field_name="sandbox_pids_limit",
            ),
            sandbox_tmpfs_size_mb=_normalize_optional_int(
                sandbox_tmpfs_size_mb,
                field_name="sandbox_tmpfs_size_mb",
            ),
            sandbox_network_policy=sandbox_network_policy,
            sandbox_timeout_seconds=_normalize_optional_int(
                sandbox_timeout_seconds,
                field_name="sandbox_timeout_seconds",
            ),
        )
        # Reconcile stale runtime artifacts when profile/mode switches.
        self._reconcile_runtime_artifacts(instance_id=instance_id)
        instance = self.get_instance(instance_id)
        self._append_audit_event(
            instance_id=instance_id,
            event_type="instance.updated",
            payload={
                "runtime_mode": instance["runtime_config"]["mode"],
                "gateway_port": instance["gateway_port"],
                "owner": instance["owner"],
                "env": instance["env"],
            },
        )
        return {"instance": instance, "registry_entry": entry}

    def _reconcile_runtime_artifacts(self, *, instance_id: str) -> None:
        """Best-effort cleanup of stale pid/cid/container artifacts after runtime mode changes."""
        try:
            target = self._get_target(instance_id)
        except ValueError:
            return
        if not target.instance_home:
            return

        run_dir = Path(target.instance_home) / "run"
        pid_path = run_dir / "gateway.pid"
        cid_path = run_dir / "gateway.cid"
        container_name = f"softnix-{instance_id}-gateway"
        try:
            config = self._load_target_config(target)
        except Exception:
            return

        runtime_mode = config.runtime.mode
        if runtime_mode == "host":
            # Host mode owns pid; cid/container should not remain from sandbox mode.
            if cid_path.exists():
                cid_path.unlink(missing_ok=True)
            if self._docker_container_exists(container_name):
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            return

        # Sandbox mode owns cid; stale host pid should be removed if dead.
        if pid_path.exists():
            pid_raw = (pid_path.read_text(encoding="utf-8") or "").strip()
            if pid_raw.isdigit():
                pid = int(pid_raw)
                try:
                    os.kill(pid, 0)
                except OSError:
                    pid_path.unlink(missing_ok=True)
            else:
                pid_path.unlink(missing_ok=True)

    def _docker_container_exists(self, name: str) -> bool:
        try:
            result = subprocess.run(
                ["docker", "container", "inspect", name],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:
            return False
        return result.returncode == 0

    def delete_instance(self, *, instance_id: str, purge_files: bool = False) -> dict[str, Any]:
        """Delete one instance from the registry."""
        registry_path = self._require_registry_path()
        target = self._get_target(instance_id)
        result = delete_softnix_instance(
            registry_path=registry_path,
            instance_id=instance_id,
            purge_files=purge_files,
        )
        self._append_audit_event(
            instance_id=instance_id,
            event_type="instance.deleted",
            severity="warning",
            payload={
                "purge_files": bool(purge_files),
                "config_path": str(target.config_path),
            },
        )
        return result

    def update_channel(
        self,
        *,
        instance_id: str,
        channel_name: str,
        enabled: bool | None = None,
        allow_from: list[str] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update one channel config safely."""
        target = self._get_target(instance_id)
        config = self._load_target_config(target)
        if not hasattr(config.channels, channel_name):
            raise ValueError(f"Unknown channel '{channel_name}'")

        channel_cfg = getattr(config.channels, channel_name)
        if enabled is not None and hasattr(channel_cfg, "enabled"):
            channel_cfg.enabled = bool(enabled)

        if allow_from is not None:
            if not isinstance(allow_from, list) or not all(isinstance(item, str) for item in allow_from):
                raise ValueError("allow_from must be a list of strings")
            channel_cfg.allow_from = [item.strip() for item in allow_from if item.strip()]

        if settings is not None:
            if not isinstance(settings, dict):
                raise ValueError("settings must be an object")
            for key, raw_value in settings.items():
                if key in {"enabled", "allow_from"}:
                    continue
                if not hasattr(channel_cfg, key):
                    raise ValueError(f"Unknown setting '{key}' for channel '{channel_name}'")
                current_value = getattr(channel_cfg, key)
                if isinstance(current_value, bool):
                    setattr(channel_cfg, key, bool(raw_value))
                    continue
                if isinstance(current_value, int):
                    try:
                        setattr(channel_cfg, key, int(raw_value))
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"Setting '{key}' must be an integer") from exc
                    continue
                if isinstance(current_value, str) or current_value is None:
                    setattr(channel_cfg, key, None if raw_value is None else str(raw_value))
                    continue
                raise ValueError(f"Setting '{key}' uses an unsupported value type")

        save_config(config, target.config_path)
        changed_fields = [f for f, v in {"enabled": enabled, "allow_from": allow_from, "settings": settings}.items() if v is not None]
        self.auth_store.append_audit(
            event_type="config.channel_updated",
            category="configuration",
            outcome="success",
            resource={"type": "instance", "id": target.id, "name": getattr(target, 'name', target.id)},
            payload={"channel_name": channel_name, "fields_updated": changed_fields},
        )
        if allow_from is not None:
            runtime = self._apply_runtime_channel_reload(target)
            self._append_audit_event(
                instance_id=target.id,
                event_type="channel.allowlist_updated",
                severity="warning" if not runtime.get("applied") else "info",
                payload={
                    "channel_name": channel_name,
                    "runtime_apply_method": runtime.get("method"),
                    "runtime_apply_detail": _truncate_text(runtime.get("detail")),
                },
            )
        return self._collect_instance(target)

    def approve_access_request(
        self,
        *,
        instance_id: str,
        channel_name: str,
        sender_id: str,
    ) -> dict[str, Any]:
        """Approve one denied sender and add it to allow_from."""
        target = self._get_target(instance_id)
        config = self._load_target_config(target)
        if not hasattr(config.channels, channel_name):
            raise ValueError(f"Unknown channel '{channel_name}'")
        sender = str(sender_id or "").strip()
        if not sender:
            raise ValueError("sender_id must not be empty")

        channel_cfg = getattr(config.channels, channel_name)
        current_allow = list(getattr(channel_cfg, "allow_from", []) or [])
        allow_item = self._allow_item_from_sender(sender)
        if allow_item not in current_allow:
            current_allow.append(allow_item)
            channel_cfg.allow_from = current_allow
            save_config(config, target.config_path)

        store = AccessRequestStore(target.workspace_path)
        removed = store.remove(channel=channel_name, sender_id=sender)
        runtime = self._apply_runtime_channel_reload(target)
        self._append_audit_event(
            instance_id=target.id,
            event_type="access_request.approved",
            severity="warning" if not runtime.get("applied") else "info",
            payload={
                "channel_name": channel_name,
                "sender_id": sender,
                "allow_item": allow_item,
                "runtime_apply_method": runtime.get("method"),
                "runtime_apply_detail": _truncate_text(runtime.get("detail")),
            },
        )
        return {
            "instance": self._collect_instance(target),
            "approved": {
                "channel_name": channel_name,
                "sender_id": sender,
                "allow_item": allow_item,
                "removed_pending": removed,
            },
            "runtime": runtime,
        }

    def reject_access_request(
        self,
        *,
        instance_id: str,
        channel_name: str,
        sender_id: str,
    ) -> dict[str, Any]:
        """Reject one denied sender and remove it from pending list."""
        target = self._get_target(instance_id)
        sender = str(sender_id or "").strip()
        if not sender:
            raise ValueError("sender_id must not be empty")
        store = AccessRequestStore(target.workspace_path)
        removed = store.remove(channel=channel_name, sender_id=sender)
        self._append_audit_event(
            instance_id=target.id,
            event_type="access_request.rejected",
            payload={
                "channel_name": channel_name,
                "sender_id": sender,
                "removed_pending": removed,
            },
        )
        return {
            "ok": True,
            "removed_pending": removed,
            "instance_id": target.id,
            "channel_name": channel_name,
            "sender_id": sender,
        }

    def update_workspace_restriction(self, *, instance_id: str, restrict_to_workspace: bool) -> dict[str, Any]:
        """Update workspace restriction for one instance."""
        target = self._get_target(instance_id)
        config = self._load_target_config(target)
        config.tools.restrict_to_workspace = bool(restrict_to_workspace)
        save_config(config, target.config_path)
        self._append_audit_event(
            instance_id=target.id,
            event_type="security.workspace_restriction_updated",
            severity="warning" if not restrict_to_workspace else "info",
            payload={"restrict_to_workspace": bool(restrict_to_workspace)},
        )
        return self._collect_instance(target)

    def update_provider_defaults(
        self,
        *,
        instance_id: str,
        model: str | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """Update default model/provider selection."""
        target = self._get_target(instance_id)
        config = self._load_target_config(target)
        if model is not None:
            model = model.strip()
            if not model:
                raise ValueError("model must not be empty")
            config.agents.defaults.model = model
        if provider is not None:
            provider = provider.strip().replace("-", "_")
            valid = {"auto", *(spec.name for spec in PROVIDERS)}
            if provider not in valid:
                raise ValueError(f"Unknown provider '{provider}'")
            config.agents.defaults.provider = provider
        save_config(config, target.config_path)
        self.auth_store.append_audit(
            event_type="config.provider_defaults_updated",
            category="configuration",
            outcome="success",
            resource={"type": "instance", "id": target.id, "name": getattr(target, 'name', target.id)},
            payload={k: v for k, v in {"model": model, "provider": provider}.items() if v is not None},
        )
        return self._collect_instance(target)

    def update_provider_config(
        self,
        *,
        instance_id: str,
        provider_name: str,
        api_key: str | None = None,
        api_base: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Update one provider configuration."""
        target = self._get_target(instance_id)
        config = self._load_target_config(target)
        provider_name = provider_name.replace("-", "_")
        if not hasattr(config.providers, provider_name):
            raise ValueError(f"Unknown provider '{provider_name}'")
        provider_cfg = getattr(config.providers, provider_name)
        if api_key is not None:
            provider_cfg.api_key = api_key.strip()
        if api_base is not None:
            provider_cfg.api_base = api_base.strip() or None
        if extra_headers is not None:
            if not isinstance(extra_headers, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in extra_headers.items()
            ):
                raise ValueError("extra_headers must be a string-to-string object")
            provider_cfg.extra_headers = {k.strip(): v.strip() for k, v in extra_headers.items() if k.strip()}
        save_config(config, target.config_path)
        self.auth_store.append_audit(
            event_type="config.provider_updated",
            category="configuration",
            outcome="success",
            resource={"type": "instance", "id": target.id, "name": getattr(target, 'name', target.id)},
            payload={"provider": provider_name, "fields_updated": [k for k, v in {"api_key": api_key, "api_base": api_base, "extra_headers": extra_headers}.items() if v is not None]},
        )
        return self._collect_instance(target)

    def upsert_mcp_server(
        self,
        *,
        instance_id: str,
        server_name: str,
        server_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Create or update one MCP server definition."""
        target = self._get_target(instance_id)
        config = self._load_target_config(target)
        name = server_name.strip()
        if not name:
            raise ValueError("server_name must not be empty")
        try:
            config.tools.mcp_servers[name] = MCPServerConfig.model_validate(server_data)
        except Exception as exc:
            raise ValueError(f"Invalid MCP server config: {exc}") from exc
        save_config(config, target.config_path)
        self.auth_store.append_audit(
            event_type="config.mcp_server_upserted",
            category="configuration",
            outcome="success",
            resource={"type": "instance", "id": target.id, "name": getattr(target, 'name', target.id)},
            payload={"server_name": name},
        )
        return self._collect_instance(target)

    def delete_mcp_server(self, *, instance_id: str, server_name: str) -> dict[str, Any]:
        """Delete one MCP server definition."""
        target = self._get_target(instance_id)
        config = self._load_target_config(target)
        if server_name not in config.tools.mcp_servers:
            raise ValueError(f"Unknown MCP server '{server_name}'")
        config.tools.mcp_servers.pop(server_name, None)
        save_config(config, target.config_path)
        self.auth_store.append_audit(
            event_type="config.mcp_server_deleted",
            category="configuration",
            outcome="success",
            resource={"type": "instance", "id": target.id, "name": getattr(target, 'name', target.id)},
            payload={"server_name": server_name},
        )
        return self._collect_instance(target)

    def validate_provider(self, *, instance_id: str, provider_name: str) -> dict[str, Any]:
        """Validate provider configuration without making external network calls."""
        target = self._get_target(instance_id)
        config = self._load_target_config(target)
        provider_name = provider_name.replace("-", "_")
        if not hasattr(config.providers, provider_name):
            raise ValueError(f"Unknown provider '{provider_name}'")
        provider_cfg = getattr(config.providers, provider_name)
        spec = next((item for item in PROVIDERS if item.name == provider_name), None)
        if spec is None:
            raise ValueError(f"Unknown provider '{provider_name}'")

        findings = []
        if spec.is_oauth:
            findings.append(
                {
                    "severity": "info",
                    "code": "oauth_provider",
                    "detail": "OAuth provider; validation only checks model selection and configuration shape.",
                }
            )
        elif not provider_cfg.api_key and not (
            spec.is_local or provider_name in {"custom", "softnix_gen_ai"}
        ):
            findings.append(
                {
                    "severity": "error",
                    "code": "missing_api_key",
                    "detail": "API key is missing.",
                }
            )

        if provider_cfg.api_base:
            parsed = urlparse(provider_cfg.api_base)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                findings.append(
                    {
                        "severity": "error",
                        "code": "invalid_api_base",
                        "detail": "API base must be a valid http(s) URL.",
                    }
                )

        selected_provider = config.agents.defaults.provider.replace("-", "_")
        model = config.agents.defaults.model
        if selected_provider not in {"auto", provider_name}:
            findings.append(
                {
                    "severity": "warning",
                    "code": "not_selected_default",
                    "detail": f"Current default provider is '{selected_provider}', not '{provider_name}'.",
                }
            )
        if provider_name not in model.replace("-", "_") and selected_provider == "auto" and provider_cfg.api_key:
            findings.append(
                {
                    "severity": "info",
                    "code": "model_prefix_mismatch",
                    "detail": f"Model '{model}' does not explicitly reference provider '{provider_name}'. Auto-detection will decide routing.",
                }
            )
        if provider_cfg.extra_headers and not all(provider_cfg.extra_headers.values()):
            findings.append(
                {
                    "severity": "warning",
                    "code": "empty_extra_header_value",
                    "detail": "One or more extra header values are empty.",
                }
            )

        status = "ok" if not any(item["severity"] == "error" for item in findings) else "error"
        if status == "ok" and any(item["severity"] == "warning" for item in findings):
            status = "warning"
        return {
            "instance_id": target.id,
            "provider_name": provider_name,
            "status": status,
            "findings": findings,
        }

    def validate_mcp_server(self, *, instance_id: str, server_name: str) -> dict[str, Any]:
        """Validate MCP server configuration without connecting to it."""
        target = self._get_target(instance_id)
        config = self._load_target_config(target)
        server = config.tools.mcp_servers.get(server_name)
        if server is None:
            raise ValueError(f"Unknown MCP server '{server_name}'")

        findings = []
        transport = server.type or ("stdio" if server.command else "sse" if server.url.endswith("/sse") else "streamableHttp" if server.url else None)
        if transport is None:
            findings.append(
                {
                    "severity": "error",
                    "code": "missing_transport",
                    "detail": "Set either a command or a URL so the transport can be determined.",
                }
            )
        if transport == "stdio" and not server.command:
            findings.append(
                {
                    "severity": "error",
                    "code": "missing_command",
                    "detail": "stdio transport requires a command.",
                }
            )
        if transport in {"sse", "streamableHttp"}:
            parsed = urlparse(server.url or "")
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                findings.append(
                    {
                        "severity": "error",
                        "code": "invalid_url",
                        "detail": "HTTP-based MCP transport requires a valid http(s) URL.",
                    }
                )
        if server.tool_timeout <= 0:
            findings.append(
                {
                    "severity": "error",
                    "code": "invalid_timeout",
                    "detail": "tool_timeout must be greater than zero.",
                }
            )
        if transport == "stdio" and server.url:
            findings.append(
                {
                    "severity": "warning",
                    "code": "unused_url_for_stdio",
                    "detail": "URL is set but stdio transport will ignore it.",
                }
            )
        if transport in {"sse", "streamableHttp"} and server.command:
            findings.append(
                {
                    "severity": "warning",
                    "code": "unused_command_for_http",
                    "detail": "Command is set but HTTP transport will ignore it.",
                }
            )
        if server.headers and not all(server.headers.values()):
            findings.append(
                {
                    "severity": "warning",
                    "code": "empty_header_value",
                    "detail": "One or more MCP header values are empty.",
                }
            )

        status = "ok" if not any(item["severity"] == "error" for item in findings) else "error"
        if status == "ok" and any(item["severity"] == "warning" for item in findings):
            status = "warning"
        return {
            "instance_id": target.id,
            "server_name": server_name,
            "status": status,
            "findings": findings,
        }

    def execute_instance_action(self, *, instance_id: str, action: str) -> dict[str, Any]:
        """Run one lifecycle action for an instance through its configured supervisor command."""
        if action not in {"start", "stop", "restart"}:
            raise ValueError(f"Unsupported action '{action}'")
        target = self._get_target(instance_id)
        if action == "start":
            port = self._read_instance_gateway_port(target)
            if port is not None:
                runtime = self._probe_instance_runtime(target)
                if runtime["status"] != "running":
                    in_use_by = self._find_running_instance_using_port(port=port, exclude_instance_id=target.id)
                    if in_use_by:
                        raise ValueError(
                            f"Gateway Port {port} is already used by running instance '{in_use_by}'."
                        )
                    if not self._is_tcp_port_available(port):
                        raise ValueError(
                            f"Gateway Port {port} is already in use by another process."
                        )
        command = self._lifecycle_command(target, action)
        if not command:
            raise ValueError(f"Instance '{instance_id}' does not define '{action}'")
        if action in {"start", "restart"} and target.workspace_path.exists():
            sync_workspace_templates(
                target.workspace_path,
                silent=True,
                agent_name=target.name or target.id,
                apply_identity=True,
            )

        result = subprocess.run(
            command,
            cwd=str(target.working_dir) if target.working_dir else None,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        payload = {
            "instance": self._collect_instance(target),
            "action": action,
            "command": command,
            "returncode": result.returncode,
            "stdout": (result.stdout or "").strip()[:2000],
            "stderr": (result.stderr or "").strip()[:2000],
            "ok": result.returncode == 0,
        }
        self._append_audit_event(
            instance_id=target.id,
            event_type=f"runtime.{action}",
            severity="info" if result.returncode == 0 else "warning",
            payload={
                "ok": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": _truncate_text(result.stdout),
                "stderr": _truncate_text(result.stderr),
            },
        )
        return payload

    def _lifecycle_command(self, target: InstanceTarget, action: str) -> list[str] | None:
        value = (target.lifecycle or {}).get(action)
        if value is None:
            return None
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise ValueError(f"Lifecycle action '{action}' must be a non-empty string array")
        return value

    def _probe_instance_runtime(self, target: InstanceTarget) -> dict[str, Any]:
        lifecycle = target.lifecycle or {}
        action_names = [
            action for action in ("start", "stop", "restart") if self._lifecycle_command(target, action)
        ]
        probe_command = self._lifecycle_command(target, "status")

        if not lifecycle:
            return {
                "status": "unmanaged",
                "management": "unmanaged",
                "manageable": False,
                "actions": [],
                "reason": "No lifecycle command hooks configured for this instance.",
                "probe": {
                    "available": False,
                    "detail": "Configure lifecycle hooks to enable Start/Stop/Restart.",
                    "returncode": None,
                },
            }

        runtime = {
            "status": "unknown",
            "management": "externally_managed",
            "manageable": bool(action_names),
            "actions": action_names,
            "reason": "Lifecycle actions are delegated to the configured supervisor command hooks.",
            "probe": {
                "available": bool(probe_command),
                "detail": "No status probe configured.",
                "returncode": None,
            },
        }

        if not probe_command:
            return runtime

        try:
            result = subprocess.run(
                probe_command,
                cwd=str(target.working_dir) if target.working_dir else None,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            runtime["status"] = "unknown"
            runtime["probe"] = {
                "available": True,
                "detail": str(exc),
                "returncode": None,
            }
            runtime["reason"] = "Status probe failed."
            return runtime

        status = "running" if result.returncode == 0 else "stopped"
        detail = (result.stdout or "").strip() or (result.stderr or "").strip() or (
            "Running" if status == "running" else "Stopped"
        )
        runtime["status"] = status
        runtime["reason"] = f"Status probe reports instance is {status}."
        runtime["probe"] = {
            "available": True,
            "detail": detail[:500],
            "returncode": result.returncode,
        }
        return runtime

    def create_schedule(
        self,
        *,
        instance_id: str,
        name: str,
        schedule_data: dict[str, Any],
        message: str,
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        delete_after_run: bool = False,
    ) -> dict[str, Any]:
        """Create one cron job in the target workspace."""
        target = self._get_target(instance_id)
        cron = self._cron_service_for_target(target)
        schedule = CronSchedule(
            kind=schedule_data.get("kind", "every"),
            at_ms=schedule_data.get("at_ms"),
            every_ms=schedule_data.get("every_ms"),
            expr=schedule_data.get("expr"),
            tz=schedule_data.get("tz"),
        )
        job = cron.add_job(
            name=name.strip(),
            schedule=schedule,
            message=message.strip(),
            deliver=deliver,
            channel=(channel or "").strip() or None,
            to=(to or "").strip() or None,
            delete_after_run=delete_after_run,
        )
        self.auth_store.append_audit(
            event_type="schedule.created",
            category="configuration",
            outcome="success",
            resource={"type": "schedule", "id": job.id, "name": job.name},
            payload={"instance_id": instance_id, "schedule_kind": schedule_data.get("kind")},
        )
        return {
            "instance": self._collect_instance(target),
            "job": self._serialize_cron_job(job),
        }

    def set_schedule_enabled(self, *, instance_id: str, job_id: str, enabled: bool) -> dict[str, Any]:
        """Enable or disable a cron job."""
        target = self._get_target(instance_id)
        cron = self._cron_service_for_target(target)
        job = cron.enable_job(job_id, enabled=enabled)
        if job is None:
            raise ValueError(f"Unknown schedule '{job_id}'")
        self.auth_store.append_audit(
            event_type="schedule.enabled_changed",
            category="configuration",
            outcome="success",
            resource={"type": "schedule", "id": job_id, "name": job.name},
            payload={"instance_id": instance_id, "enabled": enabled},
        )
        return {
            "instance": self._collect_instance(target),
            "job": self._serialize_cron_job(job),
        }

    def run_schedule(self, *, instance_id: str, job_id: str, force: bool = True) -> dict[str, Any]:
        """Run a cron job immediately."""
        target = self._get_target(instance_id)
        cron = self._cron_service_for_target(target)
        ok = asyncio.run(cron.run_job(job_id, force=force))
        if not ok:
            raise ValueError(f"Unknown or disabled schedule '{job_id}'")
        job = next((item for item in cron.list_jobs(include_disabled=True) if item.id == job_id), None)
        if job is None:
            raise ValueError(f"Unknown schedule '{job_id}'")
        self.auth_store.append_audit(
            event_type="schedule.manual_run",
            category="configuration",
            outcome="success",
            resource={"type": "schedule", "id": job_id, "name": job.name},
            payload={"instance_id": instance_id, "forced": force},
        )
        return {
            "instance": self._collect_instance(target),
            "job": self._serialize_cron_job(job),
            "ok": True,
        }

    def delete_schedule(self, *, instance_id: str, job_id: str) -> dict[str, Any]:
        """Delete one cron job."""
        target = self._get_target(instance_id)
        cron = self._cron_service_for_target(target)
        if not cron.remove_job(job_id):
            raise ValueError(f"Unknown schedule '{job_id}'")
        self.auth_store.append_audit(
            event_type="schedule.deleted",
            category="configuration",
            outcome="success",
            resource={"type": "schedule", "id": job_id},
            payload={"instance_id": instance_id},
        )
        return {
            "instance": self._collect_instance(target),
            "ok": True,
            "job_id": job_id,
        }

    def _get_target(self, instance_id: str) -> InstanceTarget:
        for target in self._load_targets():
            if target.id == instance_id:
                return target
        raise ValueError(f"Unknown instance '{instance_id}'")

    def _require_registry_path(self) -> Path:
        if self.registry_path is None:
            raise ValueError("Instance registry is not configured")
        return self.registry_path

    def _load_target_config(self, target: InstanceTarget) -> Config:
        config = load_config(target.config_path if target.config_path.exists() else None)
        if self.workspace_override and target.source == "default":
            config.agents.defaults.workspace = str(self.workspace_override)
        return config

    def _cron_service_for_target(self, target: InstanceTarget) -> CronService:
        return CronService(target.workspace_path / "cron" / "jobs.json")

    def _load_targets(self) -> list[InstanceTarget]:
        if self.registry_path and self.registry_path.exists():
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            instances = []
            for idx, item in enumerate(data.get("instances", []), start=1):
                config_path = _expand_path(item.get("config")) or self.config_path
                config = load_config(config_path if config_path.exists() else None)
                workspace_path = _expand_path(item.get("workspace")) or config.workspace_path
                instances.append(
                    InstanceTarget(
                        id=item.get("id") or f"instance-{idx}",
                        name=item.get("name") or item.get("id") or f"Instance {idx}",
                        config_path=config_path,
                        workspace_path=workspace_path,
                        source="registry",
                        lifecycle=item.get("lifecycle") if isinstance(item.get("lifecycle"), dict) else None,
                        working_dir=_expand_path(item.get("cwd")),
                        owner=item.get("owner"),
                        environment=item.get("env"),
                        instance_home=_expand_path(item.get("instance_home")),
                        nanobot_bin=item.get("nanobot_bin"),
                    )
                )
            if instances:
                return instances

        config = load_config(self.config_path if self.config_path.exists() else None)
        workspace_path = self.workspace_override or config.workspace_path
        return [
            InstanceTarget(
                id="default",
                name="Default",
                config_path=self.config_path,
                workspace_path=workspace_path,
                source="default",
                lifecycle=None,
                working_dir=None,
                owner=None,
                environment=None,
                instance_home=None,
                nanobot_bin=None,
            )
        ]

    def _collect_instance(self, target: InstanceTarget) -> dict[str, Any]:
        config = self._load_target_config(target)

        workspace_path = target.workspace_path
        sessions = SessionManager(workspace_path).list_sessions() if workspace_path.exists() else []
        cron = CronService(workspace_path / "cron" / "jobs.json")
        cron_status = cron.status()
        runtime = self._probe_instance_runtime(target)

        providers = []
        for spec in PROVIDERS:
            provider_cfg = getattr(config.providers, spec.name, None)
            if provider_cfg is None:
                continue
            providers.append(
                {
                    "name": spec.name,
                    "label": spec.label,
                    "configured": bool(spec.is_oauth or provider_cfg.api_key or provider_cfg.api_base),
                    "api_key_masked": "" if spec.is_oauth else _mask_secret(provider_cfg.api_key),
                    "api_base": provider_cfg.api_base,
                    "extra_headers": provider_cfg.extra_headers or {},
                    "oauth": spec.is_oauth,
                }
            )

        selected_provider = config.get_provider_name(config.agents.defaults.model)
        channels = self._collect_channels(config)
        mcp = self._collect_mcp(config)
        runtime_audit = self._collect_runtime_audit(workspace_path)
        security = self._collect_security(target, config, channels, runtime_audit)
        latest_session_at = sessions[0]["updated_at"] if sessions else None

        return {
            "id": target.id,
            "name": target.name,
            "owner": target.owner,
            "env": target.environment,
            "source": target.source,
            "instance_home": str(target.instance_home) if target.instance_home else None,
            "nanobot_bin": target.nanobot_bin,
            "working_dir": str(target.working_dir) if target.working_dir else None,
            "gateway_port": int(config.gateway.port),
            "runtime": runtime,
            "runtime_config": config.runtime.model_dump(by_alias=True),
            "config_path": str(target.config_path),
            "config_exists": target.config_path.exists(),
            "workspace_path": str(workspace_path),
            "workspace_exists": workspace_path.exists(),
            "model": config.agents.defaults.model,
            "selected_provider": selected_provider,
            "channels_enabled": [item["name"] for item in channels if item["enabled"]],
            "channels": channels,
            "providers": providers,
            "mcp": mcp,
            "heartbeat": {
                "enabled": config.gateway.heartbeat.enabled,
                "interval_s": config.gateway.heartbeat.interval_s,
                "file": str(workspace_path / "HEARTBEAT.md"),
                "exists": (workspace_path / "HEARTBEAT.md").exists(),
            },
            "cron": cron_status,
            "sessions": {
                "count": len(sessions),
                "latest_updated_at": latest_session_at,
                "items": sessions[:10],
            },
            "runtime_audit": runtime_audit,
            "security": security,
        }

    def _collect_runtime_audit(self, workspace_path: Path) -> dict[str, Any]:
        path = runtime_audit_path(workspace_path)
        events = self._read_runtime_audit_events(
            workspace_path=workspace_path,
            instance_id="",
            instance_name="",
        )
        summary = self._summarize_runtime_audit_events(events)
        recent_events = [
            {
                "ts": event["ts"],
                "tool_name": event["tool_name"],
                "operation": event["operation"],
                "status": event["status"],
                "command": event["command"],
                "path": event["path"],
                "package_manager": event["package_manager"],
                "result_preview": event["result_preview"][:180],
            }
            for event in events[-10:][::-1]
        ]
        return {
            "path": str(path),
            "exists": path.exists(),
            **summary,
            "recent_events": recent_events,
        }

    def _read_runtime_audit_events(
        self,
        *,
        workspace_path: Path,
        instance_id: str,
        instance_name: str,
    ) -> list[dict[str, Any]]:
        path = runtime_audit_path(workspace_path)
        lines: list[str] = []
        try:
            if path.exists():
                lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except Exception:
            lines = []

        events: list[dict[str, Any]] = []
        for line_number, raw in enumerate(lines, start=1):
            try:
                item = json.loads(raw)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            operation = str(payload.get("operation") or "")
            ts = str(item.get("ts") or "")
            result_preview = _truncate_text(payload.get("result_preview"), limit=500)
            status = str(payload.get("status") or "").strip().lower()
            if status not in {"ok", "error", "running"}:
                status = "error" if "blocked by safety guard" in result_preview.lower() else "ok"
            event = {
                "event_id": f"{instance_id}:{line_number}" if instance_id else str(line_number),
                "line": line_number,
                "ts": ts,
                "instance_id": instance_id or None,
                "instance_name": instance_name or None,
                "event_type": str(item.get("event_type") or ""),
                "tool_name": str(payload.get("tool_name") or ""),
                "operation": operation,
                "status": status,
                "command": _truncate_text(payload.get("command"), limit=320),
                "path": _truncate_text(payload.get("path"), limit=320),
                "package_manager": _truncate_text(payload.get("package_manager"), limit=64),
                "channel": _truncate_text(payload.get("channel"), limit=64),
                "session_key": _truncate_text(payload.get("session_key"), limit=160),
                "message_preview": _truncate_text(payload.get("message_preview"), limit=320),
                "result_preview": result_preview,
                "exit_code": payload.get("exit_code"),
            }
            events.append(event)

        # Keep Execution Visualize live even when a task does not emit tool-level runtime-audit lines.
        # We synthesize message_received/message_completed from session logs and merge with runtime events.
        session_events = self._read_session_events_for_runtime_audit(
            workspace_path=workspace_path,
            instance_id=instance_id,
            instance_name=instance_name,
            start_line=len(lines) + 1,
        )
        existing_keys = {
            (
                str(item.get("ts") or ""),
                str(item.get("operation") or ""),
                str(item.get("session_key") or ""),
                str(item.get("message_preview") or ""),
            )
            for item in events
        }
        # Build a lookup of (operation, session_key) -> [timestamps] from audit
        # events so we can detect near-duplicate session events that would
        # otherwise create spurious traces in Execution Visualize.
        _audit_ts_by_op_sk: dict[tuple[str, str], list[float]] = {}
        for item in events:
            op = str(item.get("operation") or "")
            sk = str(item.get("session_key") or "")
            if op in ("message_received", "message_completed") and sk:
                parsed = _safe_parse_ts(str(item.get("ts") or ""))
                if parsed is not None:
                    _audit_ts_by_op_sk.setdefault((op, sk), []).append(parsed)

        for item in session_events:
            key = (
                str(item.get("ts") or ""),
                str(item.get("operation") or ""),
                str(item.get("session_key") or ""),
                str(item.get("message_preview") or ""),
            )
            if key in existing_keys:
                continue
            # Skip session events that are near-duplicates of existing audit
            # events (same operation + session_key within 120 seconds).
            op = str(item.get("operation") or "")
            sk = str(item.get("session_key") or "")
            item_ts = _safe_parse_ts(str(item.get("ts") or ""))
            if item_ts is not None and (op, sk) in _audit_ts_by_op_sk:
                if any(abs(item_ts - ats) < 120 for ats in _audit_ts_by_op_sk[(op, sk)]):
                    continue
            events.append(item)
            existing_keys.add(key)
        return events

    def _read_session_events_for_runtime_audit(
        self,
        *,
        workspace_path: Path,
        instance_id: str,
        instance_name: str,
        start_line: int,
    ) -> list[dict[str, Any]]:
        sessions_dir = workspace_path / "sessions"
        if not sessions_dir.exists() or not sessions_dir.is_dir():
            return []
        try:
            session_files = sorted(
                sessions_dir.glob("*.jsonl"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )[:8]
        except Exception:
            return []

        events: list[dict[str, Any]] = []
        next_line = int(start_line)
        for session_path in session_files:
            try:
                raw_lines = session_path.read_text(encoding="utf-8").splitlines()
            except Exception:
                continue
            recent_lines = [line for line in raw_lines if line.strip()][-80:]
            session_stem = session_path.stem
            channel = session_stem.split("_", 1)[0] if "_" in session_stem else "unknown"
            session_key = session_stem.replace("_", ":", 1)
            for raw in recent_lines:
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                role = self._extract_event_role(data)
                if role not in {"user", "assistant"}:
                    continue
                content = self._extract_event_content(data, role=role)
                if not content:
                    continue
                ts = self._extract_event_timestamp(data, fallback=None)
                ts_value = str(ts or "").strip()
                has_explicit_tz = (
                    ts_value.endswith("Z")
                    or (
                        len(ts_value) >= 6
                        and ts_value[-6] in {"+", "-"}
                        and ts_value[-3] == ":"
                    )
                )
                if ts_value and not has_explicit_tz:
                    # Naive timestamps were written by datetime.now() (no
                    # tz-info).  Assume they originate from the same
                    # machine as this admin service (i.e. local time).
                    _local_offset = datetime.now().astimezone().strftime("%z")
                    # strftime %z gives e.g. "+0700"; insert colon for ISO 8601
                    _tz_suffix = f"{_local_offset[:3]}:{_local_offset[3:]}" if len(_local_offset) == 5 else "+00:00"
                    ts_value = f"{ts_value}{_tz_suffix}"
                operation = "message_received" if role == "user" else "message_completed"
                events.append(
                    {
                        "event_id": f"{instance_id}:session:{session_stem}:{next_line}",
                        "line": next_line,
                        "ts": ts_value,
                        "instance_id": instance_id or None,
                        "instance_name": instance_name or None,
                        "event_type": "session.message",
                        "tool_name": "session",
                        "operation": operation,
                        "status": "ok",
                        "command": "",
                        "path": "",
                        "package_manager": "",
                        "channel": channel,
                        "session_key": session_key,
                        "message_preview": _truncate_text(content, limit=320),
                        "result_preview": _truncate_text(content, limit=500) if role == "assistant" else "",
                        "exit_code": None,
                    }
                )
                next_line += 1
        return events

    @staticmethod
    def _summarize_runtime_audit_events(events: list[dict[str, Any]]) -> dict[str, Any]:
        event_count = len(events)
        exec_count = 0
        file_op_count = 0
        package_install_count = 0
        blocked_count = 0
        last_event_at: str | None = None
        for event in events:
            operation = str(event.get("operation") or "")
            if operation in {"command", "package_install"}:
                exec_count += 1
            if operation.startswith("file_"):
                file_op_count += 1
            if operation == "package_install":
                package_install_count += 1
            if "blocked by safety guard" in str(event.get("result_preview") or "").lower():
                blocked_count += 1
            ts = str(event.get("ts") or "")
            if ts:
                last_event_at = ts
        return {
            "event_count": event_count,
            "exec_count": exec_count,
            "file_op_count": file_op_count,
            "package_install_count": package_install_count,
            "blocked_count": blocked_count,
            "last_event_at": last_event_at,
        }

    @staticmethod
    def _filter_runtime_audit_events(
        events: list[dict[str, Any]],
        *,
        status_filter: str,
        operation_filter: str,
        search_filter: str,
    ) -> list[dict[str, Any]]:
        def _matches(event: dict[str, Any]) -> bool:
            if status_filter != "all" and str(event.get("status") or "") != status_filter:
                return False
            if operation_filter != "all" and str(event.get("operation") or "") != operation_filter:
                return False
            if search_filter:
                haystack = " ".join(
                    str(event.get(field) or "")
                    for field in ("command", "path", "result_preview", "tool_name", "operation", "package_manager", "message_preview", "channel", "session_key")
                ).lower()
                if search_filter not in haystack:
                    return False
            return True

        return [event for event in events if _matches(event)]

    @staticmethod
    def _allow_item_from_sender(sender_id: str) -> str:
        sender = str(sender_id).strip()
        if not sender:
            return sender
        parts = [part.strip() for part in sender.split("|") if part.strip()]
        return parts[0] if parts else sender

    def _apply_runtime_channel_reload(self, target: InstanceTarget) -> dict[str, Any]:
        config = self._load_target_config(target)
        if config.runtime.mode == "sandbox":
            container_name = f"softnix-{target.id}-gateway"
            try:
                result = subprocess.run(
                    ["docker", "kill", "--signal", "HUP", container_name],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return {"applied": False, "method": "docker-signal", "detail": str(exc)}
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "docker signal failed").strip()[:500]
                return {"applied": False, "method": "docker-signal", "detail": detail}
            return {"applied": True, "method": "docker-signal", "container": container_name}
        if not hasattr(signal, "SIGHUP"):
            return {"applied": False, "method": None, "detail": "SIGHUP is not supported on this platform."}
        if not target.instance_home:
            return {"applied": False, "method": None, "detail": "Instance home is not configured."}
        pid_path = Path(target.instance_home) / "run" / "gateway.pid"
        if not pid_path.exists():
            return {"applied": False, "method": "sighup", "detail": f"PID file not found: {pid_path}"}
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except Exception as exc:
            return {"applied": False, "method": "sighup", "detail": f"Invalid PID file: {exc}"}
        try:
            os.kill(pid, 0)
        except OSError as exc:
            return {"applied": False, "method": "sighup", "detail": f"Gateway process is not running: {exc}"}
        try:
            os.kill(pid, signal.SIGHUP)
        except OSError as exc:
            return {"applied": False, "method": "sighup", "detail": f"Failed to signal process {pid}: {exc}"}
        return {"applied": True, "method": "sighup", "pid": pid}

    def _read_instance_gateway_port(self, target: InstanceTarget) -> int | None:
        try:
            config = self._load_target_config(target)
        except Exception:
            return None
        return int(config.gateway.port)

    def _find_running_instance_using_port(self, *, port: int, exclude_instance_id: str) -> str | None:
        for target in self._load_targets():
            if target.id == exclude_instance_id:
                continue
            target_port = self._read_instance_gateway_port(target)
            if target_port != port:
                continue
            runtime = self._probe_instance_runtime(target)
            if runtime["status"] == "running":
                return target.id
        return None

    @staticmethod
    def _is_tcp_port_available(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False

    def _collect_session_events(
        self,
        instance: dict[str, Any],
        *,
        limit: int,
        diagnostics: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for session_info in instance["sessions"]["items"][:10]:
            path = Path(session_info["path"])
            if diagnostics is not None:
                diagnostics["session_files_seen"] += 1
            if not path.exists():
                if diagnostics is not None:
                    diagnostics["session_files_missing"] += 1
                continue
            try:
                with open(path, encoding="utf-8") as handle:
                    lines = [line.strip() for line in handle if line.strip()]
                if diagnostics is not None:
                    diagnostics["session_files_read"] += 1
                for raw in lines[-8:]:
                    if diagnostics is not None:
                        diagnostics["session_lines_considered"] += 1
                    try:
                        data = json.loads(raw)
                    except Exception as exc:
                        if diagnostics is not None:
                            diagnostics["session_json_parse_errors"] += 1
                            if len(diagnostics["sample_errors"]) < 5:
                                diagnostics["sample_errors"].append(f"{path.name}: invalid JSON ({exc})")
                        continue
                    if not isinstance(data, dict):
                        continue
                    if data.get("_type") == "metadata":
                        if diagnostics is not None:
                            diagnostics["session_metadata_lines_skipped"] += 1
                        continue
                    ts = self._extract_event_timestamp(data, fallback=session_info.get("updated_at"))
                    role = self._extract_event_role(data)
                    content = self._extract_event_content(data, role=role)
                    events.append(
                        {
                            "instance_id": instance["id"],
                            "instance_name": instance["name"],
                            "ts": ts,
                            "type": self._event_type_for_role(role),
                            "severity": "info" if role == "user" else "ok" if role == "assistant" else "warning",
                            "session_key": session_info["key"],
                            "channel": session_info["key"].split(":", 1)[0] if ":" in session_info["key"] else "unknown",
                            "actor": role,
                            "summary": content[:160],
                            "detail": content[:4000],
                        }
                    )
            except Exception as exc:
                if diagnostics is not None:
                    diagnostics["session_read_errors"] += 1
                    if len(diagnostics["sample_errors"]) < 5:
                        diagnostics["sample_errors"].append(f"{path.name}: read error ({exc})")
                continue

        events.sort(key=lambda event: _ts_sort_key(event.get("ts")), reverse=True)
        return events[:limit]

    @staticmethod
    def _extract_event_timestamp(data: dict[str, Any], *, fallback: str | None = None) -> str | None:
        ts_value = data.get("timestamp") or data.get("ts") or data.get("created_at") or data.get("time") or fallback
        if ts_value is None:
            return None
        if isinstance(ts_value, (int, float)):
            ts_float = float(ts_value)
            if ts_float > 1_000_000_000_000:
                ts_float /= 1000
            return datetime.fromtimestamp(ts_float).astimezone().isoformat()
        return str(ts_value)

    @staticmethod
    def _extract_event_role(data: dict[str, Any]) -> str:
        role = data.get("role") or data.get("actor") or data.get("source") or "unknown"
        return str(role).strip().lower() or "unknown"

    @staticmethod
    def _extract_event_content(data: dict[str, Any], *, role: str) -> str:
        content = data.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, str) and item.strip():
                    text_parts.append(item.strip())
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str) and text.strip():
                        text_parts.append(text.strip())
            if text_parts:
                return " ".join(text_parts)
        for key in ("message", "text"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if role == "assistant" and data.get("tool_calls"):
            tool_calls = data.get("tool_calls")
            if isinstance(tool_calls, list) and len(tool_calls) > 0:
                tool_summaries: list[str] = []
                for tc in tool_calls[:4]:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function")
                    if not isinstance(fn, dict):
                        continue
                    name = str(fn.get("name") or "")
                    if not name:
                        continue
                    args_raw = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw if isinstance(args_raw, dict) else {})
                    except Exception:
                        args = {}
                    if name == "web_fetch" and args.get("url"):
                        detail = str(args["url"])[:80]
                        tool_summaries.append(f"web_fetch({detail})")
                    elif name == "web_search" and args.get("query"):
                        detail = str(args["query"])[:60]
                        tool_summaries.append(f'web_search("{detail}")')
                    elif name == "exec" and args.get("command"):
                        detail = str(args["command"])[:50]
                        tool_summaries.append(f"exec({detail})")
                    else:
                        tool_summaries.append(name)
                if tool_summaries:
                    suffix = f" +{len(tool_calls) - 4} more" if len(tool_calls) > 4 else ""
                    return ", ".join(tool_summaries) + suffix
            return "Tool invocation"
        return "(empty)"

    def _collect_cron_events(self, instance: dict[str, Any]) -> list[dict[str, Any]]:
        store_path = Path(instance["workspace_path"]) / "cron" / "jobs.json"
        if not store_path.exists():
            return []
        try:
            data = json.loads(store_path.read_text(encoding="utf-8"))
        except Exception:
            return []

        events = []
        for job in data.get("jobs", []):
            last_run_ms = (job.get("state") or {}).get("lastRunAtMs")
            if not last_run_ms:
                continue
            status = (job.get("state") or {}).get("lastStatus") or "ok"
            last_error = (job.get("state") or {}).get("lastError")
            events.append(
                {
                    "instance_id": instance["id"],
                    "instance_name": instance["name"],
                    "ts": datetime.fromtimestamp(last_run_ms / 1000).isoformat(),
                    "type": "cron",
                    "severity": "error" if status == "error" else "ok",
                    "session_key": f"cron:{job.get('id')}",
                    "channel": job.get("payload", {}).get("channel") or "system",
                    "actor": "cron",
                    "summary": last_error or f"Scheduled job '{job.get('name')}' finished with status {status}.",
                    "detail": (last_error or f"Scheduled job '{job.get('name')}' finished with status {status}.")[:4000],
                }
            )
        return events

    def _collect_runtime_snapshot_event(self, instance: dict[str, Any]) -> dict[str, Any]:
        runtime = instance.get("runtime") or {}
        status = str(runtime.get("status") or "unknown")
        probe_detail = str((runtime.get("probe") or {}).get("detail") or runtime.get("reason") or "").strip()
        status_text = status.capitalize()
        summary = f"Gateway status: {status_text}."
        if probe_detail:
            summary = f"{summary} {probe_detail}"
        return {
            "instance_id": instance["id"],
            "instance_name": instance["name"],
            "ts": datetime.now().astimezone().isoformat(),
            "type": "runtime",
            "severity": "ok" if status == "running" else "warning" if status == "stopped" else "info",
            "session_key": f"runtime:{instance['id']}",
            "channel": "system",
            "actor": "runtime",
            "summary": summary[:220],
            "detail": summary[:4000],
        }

    @staticmethod
    def _event_type_for_role(role: str) -> str:
        if role == "user":
            return "inbound"
        if role == "assistant":
            return "outbound"
        if role == "tool":
            return "tool"
        return "system"

    @staticmethod
    def _serialize_cron_job(job: Any) -> dict[str, Any]:
        return {
            "id": job.id,
            "name": job.name,
            "enabled": job.enabled,
            "schedule": {
                "kind": job.schedule.kind,
                "at_ms": job.schedule.at_ms,
                "every_ms": job.schedule.every_ms,
                "expr": job.schedule.expr,
                "tz": job.schedule.tz,
            },
            "payload": {
                "message": job.payload.message,
                "deliver": job.payload.deliver,
                "channel": job.payload.channel,
                "to": job.payload.to,
            },
            "state": {
                "next_run_at_ms": job.state.next_run_at_ms,
                "last_run_at_ms": job.state.last_run_at_ms,
                "last_status": job.state.last_status,
                "last_error": job.state.last_error,
            },
            "delete_after_run": job.delete_after_run,
        }

    def _collect_channels(self, config: Config) -> list[dict[str, Any]]:
        rows = []
        for name in (
            "whatsapp",
            "telegram",
            "discord",
            "feishu",
            "mochat",
            "dingtalk",
            "email",
            "slack",
            "qq",
            "matrix",
            "softnix_app",
        ):
            channel_cfg = getattr(config.channels, name)
            allow_list = getattr(channel_cfg, "allow_from", None)
            rows.append(
                {
                    "name": name,
                    "enabled": bool(getattr(channel_cfg, "enabled", False)),
                    "running": None,
                    "status_source": "config",
                    "allow_from": list(allow_list) if isinstance(allow_list, list) else None,
                    "allow_from_count": len(allow_list) if isinstance(allow_list, list) else None,
                    "allow_from_mode": (
                        "allow_all"
                        if isinstance(allow_list, list) and "*" in allow_list
                        else "deny_all"
                        if allow_list == []
                        else "allowlist"
                    ),
                    "settings": self._collect_channel_settings(name, channel_cfg),
                }
            )
        return rows

    @staticmethod
    def _collect_channel_settings(channel_name: str, channel_cfg: Any) -> dict[str, Any]:
        if channel_name == "telegram":
            return {
                "token": getattr(channel_cfg, "token", ""),
                "proxy": getattr(channel_cfg, "proxy", None),
                "reply_to_message": bool(getattr(channel_cfg, "reply_to_message", False)),
            }
        return {}

    def _collect_mcp(self, config: Config) -> dict[str, Any]:
        servers = []
        for name, server in config.tools.mcp_servers.items():
            servers.append(
                {
                    "name": name,
                    "type": server.type or ("stdio" if server.command else "streamableHttp" if server.url else None),
                    "command": server.command,
                    "args": server.args,
                    "env": server.env,
                    "url": server.url,
                    "headers": server.headers,
                    "tool_timeout": server.tool_timeout,
                }
            )
        return {
            "server_count": len(servers),
            "servers": servers,
        }

    def _collect_security(
        self,
        target: InstanceTarget,
        config: Config,
        channels: list[dict[str, Any]],
        runtime_audit: dict[str, Any],
    ) -> dict[str, Any]:
        findings = []
        audit_path = self._audit_path(target.id)
        connected_channels = [channel["name"] for channel in channels if channel.get("enabled")]
        has_online_provider = bool(
            config.get_provider_name(config.agents.defaults.model)
            or any(
                bool(getattr(getattr(config.providers, spec.name, None), "api_key", "") or getattr(getattr(config.providers, spec.name, None), "api_base", ""))
                for spec in PROVIDERS
            )
        )
        has_online_mcp = bool(config.tools.mcp_servers)
        requires_outbound_network = bool(connected_channels or has_online_provider or has_online_mcp)
        if not config.tools.restrict_to_workspace:
            findings.append(
                {
                    "severity": "warning",
                    "code": "workspace_restriction_disabled",
                    "title": "Workspace restriction is disabled",
                    "detail": "Agent tools are not restricted to the workspace directory.",
                }
            )

        if target.config_path.exists() and _is_permissions_too_open(target.config_path, 0o600):
            findings.append(
                {
                    "severity": "warning",
                    "code": "config_permissions_open",
                    "title": "Config file permissions are too open",
                    "detail": f"Expected 0600 or stricter, found {_file_mode(target.config_path)}.",
                }
            )

        if config.runtime.mode != "sandbox":
            findings.append(
                {
                    "severity": "warning" if (target.environment or "").lower() in {"prod", "uat", "staging"} else "info",
                    "code": "runtime_not_sandboxed",
                    "title": "Instance is not using sandbox runtime",
                    "detail": "This instance still runs in host mode. Enterprise production workloads should use sandbox mode.",
                }
            )
        if config.runtime.mode == "sandbox" and config.runtime.sandbox.execution_strategy == "tool_ephemeral":
            findings.append(
                {
                    "severity": "warning",
                    "code": "tool_ephemeral_requires_host_runtime",
                    "title": "Tool-ephemeral strategy requires host control plane",
                    "detail": "Switch runtime.mode to 'host' when using runtime.sandbox.executionStrategy='tool_ephemeral'. The host gateway decides when to launch ephemeral sandbox jobs.",
                }
            )
        elif config.runtime.sandbox.network_policy == "none" and requires_outbound_network:
            findings.append(
                {
                    "severity": "warning",
                    "code": "sandbox_network_disabled_for_connected_workload",
                    "title": "Sandbox network policy blocks connected integrations",
                    "detail": "This instance uses connected channels, provider APIs, or MCP servers. Set runtime.sandbox.networkPolicy to 'default' so the workload can reach the internet.",
                }
            )
        if config.runtime.mode == "sandbox":
            if not config.runtime.sandbox.cpu_limit.strip():
                findings.append(
                    {
                        "severity": "warning" if (target.environment or "").lower() in {"prod", "uat", "staging"} else "info",
                        "code": "sandbox_cpu_limit_missing",
                        "title": "Sandbox CPU limit is not configured",
                        "detail": "Set runtime.sandbox.cpuLimit to prevent one instance from consuming excessive CPU on the host.",
                    }
                )
            if not config.runtime.sandbox.memory_limit.strip():
                findings.append(
                    {
                        "severity": "warning" if (target.environment or "").lower() in {"prod", "uat", "staging"} else "info",
                        "code": "sandbox_memory_limit_missing",
                        "title": "Sandbox memory limit is not configured",
                        "detail": "Set runtime.sandbox.memoryLimit to contain memory pressure and reduce blast radius.",
                    }
                )
            if int(config.runtime.sandbox.pids_limit) > 1024:
                findings.append(
                    {
                        "severity": "info",
                        "code": "sandbox_pids_limit_high",
                        "title": "Sandbox PIDs limit is high",
                        "detail": "Lower runtime.sandbox.pidsLimit if the workload does not need a large process budget.",
                    }
                )
        if config.runtime.mode != "sandbox" and int(runtime_audit.get("exec_count") or 0) > 0:
            findings.append(
                {
                    "severity": "warning" if (target.environment or "").lower() in {"prod", "uat", "staging"} else "info",
                    "code": "host_mode_exec_activity_detected",
                    "title": "Host mode shell activity detected",
                    "detail": "This instance executed shell commands while running in host mode. Move to sandbox mode to reduce host risk.",
                }
            )
        if int(runtime_audit.get("package_install_count") or 0) > 0:
            findings.append(
                {
                    "severity": "info",
                    "code": "runtime_package_installs_detected",
                    "title": "Runtime package installs were detected",
                    "detail": "The agent installed packages during runtime. Review runtime-audit.jsonl for supply-chain and change-control verification.",
                }
            )
        if int(runtime_audit.get("blocked_count") or 0) > 0:
            findings.append(
                {
                    "severity": "info",
                    "code": "runtime_guard_blocks_detected",
                    "title": "Safety guard blocked runtime commands",
                    "detail": "At least one command was blocked by the runtime safety guard. Review runtime-audit.jsonl for the rejected attempts.",
                }
            )

        wa = config.channels.whatsapp
        if wa.enabled and not wa.bridge_token:
            findings.append(
                {
                    "severity": "warning",
                    "code": "missing_whatsapp_bridge_token",
                    "title": "WhatsApp bridge token is not configured",
                    "detail": "Bridge auth should be enabled for production deployments.",
                }
            )

        for channel in channels:
            if channel["enabled"] and channel["allow_from_mode"] == "deny_all":
                findings.append(
                    {
                        "severity": "warning",
                        "code": f"{channel['name']}_deny_all",
                        "title": f"Channel '{channel['name']}' denies all users",
                        "detail": "Enabled channel has empty allow_from list.",
                    }
                )

        findings.extend(
            [
                {
                    "severity": "info",
                    "code": "no_rate_limiting",
                    "title": "No built-in rate limiting",
                    "detail": "Runtime currently relies on external controls for request throttling.",
                },
                {
                    "severity": "info",
                    "code": "no_session_expiry",
                    "title": "No automatic session expiry",
                    "detail": "Stored sessions persist until manually managed.",
                },
            ]
        )

        if not audit_path.exists():
            findings.append(
                {
                    "severity": "info",
                    "code": "audit_log_not_initialized",
                    "title": "Audit log has not been initialized",
                    "detail": "No sandbox or admin-change events have been written yet for this instance.",
                }
            )

        return {
            "finding_count": len(findings),
            "config_permissions": {
                "path": str(target.config_path),
                "mode": _file_mode(target.config_path),
            },
            "bridge": {
                "whatsapp_enabled": wa.enabled,
                "bridge_url": wa.bridge_url,
                "bridge_token_configured": bool(wa.bridge_token),
            },
            "audit": {
                "path": str(audit_path),
                "exists": audit_path.exists(),
            },
            "runtime_audit": runtime_audit,
            "findings": findings,
        }
