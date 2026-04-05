"""Admin service for operational metadata and safe config updates."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import hmac
import mimetypes
import shutil
import stat
import subprocess
import asyncio
import socket
import os
import re
import signal
import secrets
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

import httpx
from loguru import logger
from openai import AsyncOpenAI

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
    normalize_instance_ids,
    normalize_role,
    normalize_username,
    sanitize_user,
    verify_password,
)
from nanobot.admin.auth_store import AdminAuthStore
from nanobot.admin.connectors import (
    COMPOSIO_API_KEY_HEADER_DEFAULT,
    COMPOSIO_CONNECTOR_PRESET,
    COMPOSIO_MCP_URL_DEFAULT,
    GMAIL_CONNECTOR_PRESET,
    GITHUB_CONNECTOR_PRESET,
    INSIGHTDOC_CONNECTOR_PRESET,
    NOTION_CONNECTOR_PRESET,
    build_composio_mcp_server_config,
    build_gmail_stdio_server_config,
    build_github_stdio_server_config,
    build_insightdoc_stdio_server_config,
    build_notion_stdio_server_config,
    get_connector_preset,
    list_connector_presets as list_built_in_connector_presets,
)
from nanobot.admin.skills_bank import (
    build_skill_bank_archive,
    list_skill_bank_catalog,
    resolve_skill_bank_entry,
)
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
from nanobot.providers.custom_provider import SOFTNIX_GENAI_USER_AGENT
from nanobot.providers.registry import PROVIDERS
from nanobot.runtime.audit import runtime_audit_path
from nanobot.security.policy import GlobalControlPolicyStore, PolicyValidationError, get_policy_catalog
from nanobot.session.manager import SessionManager
from nanobot.utils.helpers import sync_workspace_templates
from nanobot.integrations.github_mcp_server import GitHubClient
from nanobot.integrations.gmail_mcp_server import GMAIL_API_BASE_DEFAULT, GMAIL_WRITE_SCOPES, GmailClient
from nanobot.integrations.insightdoc_mcp_server import (
    INSIGHTDOC_API_BASE_DEFAULT,
    INSIGHTDOC_EXTERNAL_BASE_DEFAULT,
    InsightDOCClient,
)
from nanobot.integrations.notion_mcp_server import NOTION_API_BASE_DEFAULT, NotionClient, normalize_notion_target_id


def _expand_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    return Path(value).expanduser()


def _read_git_commit_short(repo_root: Path) -> str:
    env_value = str(os.environ.get("SOFTNIX_BUILD_COMMIT") or "").strip()
    if env_value:
        return env_value[:12]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def _read_git_ref_short(repo_root: Path, ref: str) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", ref],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def _get_git_update_status(repo_root: Path) -> dict[str, str]:
    current_commit = _read_git_commit_short(repo_root)
    latest_commit = _read_git_ref_short(repo_root, "origin/main")
    if not current_commit or not latest_commit:
        update_status = "unknown"
    elif current_commit == latest_commit:
        update_status = "up_to_date"
    else:
        update_status = "needs_update"
    return {
        "current_commit": current_commit,
        "latest_commit": latest_commit,
        "update_status": update_status,
    }


def _mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return f"{'*' * max(len(value) - keep, 3)}{value[-keep:]}"


def _mask_ip_for_display(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if ":" in raw:
        parts = [part for part in raw.split(":") if part]
        if len(parts) <= 2:
            return "****"
        return ":".join(parts[:2] + ["****"])
    octets = raw.split(".")
    if len(octets) == 4:
        return ".".join(octets[:2] + ["x", "x"])
    return "***"


def _user_agent_label(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "Unknown browser"
    lowered = text.lower()
    if "edg/" in lowered:
        return "Microsoft Edge"
    if "chrome/" in lowered and "edg/" not in lowered:
        return "Google Chrome"
    if "safari/" in lowered and "chrome/" not in lowered:
        return "Safari"
    if "firefox/" in lowered:
        return "Firefox"
    if "iphone" in lowered:
        return "iPhone browser"
    if "android" in lowered:
        return "Android browser"
    return text.split(" ", 1)[0][:60]


def _connector_runtime_script_source(filename: str) -> Path:
    return Path(__file__).resolve().parent.parent / "integrations" / filename


def _ensure_connector_runtime_script(target: "InstanceTarget", filename: str) -> Path:
    instance_home = target.instance_home or target.config_path.expanduser().resolve().parent
    runtime_dir = instance_home / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_script = runtime_dir / filename
    runtime_script.write_text(_connector_runtime_script_source(filename).read_text(encoding="utf-8"), encoding="utf-8")
    return runtime_script


def _ensure_github_connector_runtime_script(target: "InstanceTarget") -> Path:
    return _ensure_connector_runtime_script(target, "github_mcp_server.py")


def _ensure_notion_connector_runtime_script(target: "InstanceTarget") -> Path:
    return _ensure_connector_runtime_script(target, "notion_mcp_server.py")


def _ensure_gmail_connector_runtime_script(target: "InstanceTarget") -> Path:
    return _ensure_connector_runtime_script(target, "gmail_mcp_server.py")


def _ensure_insightdoc_connector_runtime_script(target: "InstanceTarget") -> Path:
    return _ensure_connector_runtime_script(target, "insightdoc_mcp_server.py")


def _header_value_case_insensitive(headers: dict[str, Any] | None, header_name: str) -> str:
    normalized_name = str(header_name or "").strip().lower()
    if not normalized_name:
        return ""
    for key, value in (headers or {}).items():
        if str(key or "").strip().lower() == normalized_name:
            return str(value or "").strip()
    return ""


async def _probe_remote_mcp_server_async(server_config: MCPServerConfig) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamable_http_client

    transport_type = str(server_config.type or "").strip()
    if not transport_type:
        transport_type = "sse" if str(server_config.url or "").rstrip("/").endswith("/sse") else "streamableHttp"

    async with AsyncExitStack() as stack:
        if transport_type == "sse":
            def httpx_client_factory(
                headers: dict[str, str] | None = None,
                timeout: httpx.Timeout | None = None,
                auth: httpx.Auth | None = None,
            ) -> httpx.AsyncClient:
                merged_headers = {**(server_config.headers or {}), **(headers or {})}
                return httpx.AsyncClient(
                    headers=merged_headers or None,
                    follow_redirects=True,
                    timeout=timeout,
                    auth=auth,
                )

            read, write = await stack.enter_async_context(
                sse_client(server_config.url, httpx_client_factory=httpx_client_factory)
            )
        elif transport_type == "streamableHttp":
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers=server_config.headers or None,
                    follow_redirects=True,
                    timeout=None,
                )
            )
            read, write, _ = await stack.enter_async_context(
                streamable_http_client(server_config.url, http_client=http_client)
            )
        else:
            raise ValueError(f"Unsupported MCP transport '{transport_type}'")

        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return {
                "tool_count": len(tools.tools),
                "tool_names": [tool.name for tool in tools.tools],
            }


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


def _extract_list_items(payload: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _find_named_item(
    payload: Any,
    needle: str,
    *,
    list_keys: tuple[str, ...],
    field_keys: tuple[str, ...],
) -> dict[str, Any] | None:
    normalized = str(needle or "").strip().lower()
    if not normalized:
        return None
    for item in _extract_list_items(payload, list_keys):
        for key in field_keys:
            candidate = str(item.get(key) or "").strip().lower()
            if candidate and candidate == normalized:
                return item
    return None


def _truncate_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _default_mobile_push_subject() -> str:
    return "mailto:admin@example.com"


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


def _parse_iso_datetime(value: Any, *, default_tz: timezone | None = None) -> datetime | None:
    """Parse an ISO-ish timestamp into an aware datetime, or *None* on failure."""
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz or timezone.utc)
    return dt


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


def _normalize_zip_entry_path(value: str) -> Path:
    raw = str(value or "").replace("\\", "/").strip("/")
    if not raw:
        raise ValueError("Archive contains an empty path entry")
    entry = PurePosixPath(raw)
    if entry.is_absolute() or any(part in {"", ".", ".."} for part in entry.parts):
        raise ValueError(f"Archive contains unsafe path '{value}'")
    return Path(*entry.parts)


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
    max_mobile_attachment_bytes = 15 * 1024 * 1024
    max_web_chat_attachment_bytes = 50 * 1024 * 1024
    rtsp_snapshot_cache_seconds = 5

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
        self._mobile_push_offsets: dict[str, int] = self.auth_store.get_mobile_push_offsets()
        self._mobile_push_stop = threading.Event()
        self._mobile_push_worker: threading.Thread | None = None
        self._sync_workspace_identities()
        self._start_mobile_push_worker()

    def _normalize_accessible_instance_ids(
        self, accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None
    ) -> set[str] | None:
        if accessible_instance_ids is None:
            return None
        cleaned = {
            str(item or "").strip()
            for item in accessible_instance_ids
            if str(item or "").strip()
        }
        return cleaned

    def _is_target_accessible(
        self,
        target: InstanceTarget,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> bool:
        normalized = self._normalize_accessible_instance_ids(accessible_instance_ids)
        if normalized is None:
            return True
        return target.id in normalized

    def _require_target_access(
        self,
        target: InstanceTarget,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> None:
        if not self._is_target_accessible(target, accessible_instance_ids):
            raise PermissionError(f"Instance '{target.id}' is not accessible")

    def _instance_scope_for_user(self, user: dict[str, Any] | None) -> set[str] | None:
        if not user:
            return None
        instance_ids = normalize_instance_ids(user.get("instance_ids"))
        if instance_ids is None:
            return None
        return {item for item in instance_ids if item}

    def _validate_instance_selection(
        self,
        instance_ids: list[str] | None,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> list[str] | None:
        normalized = normalize_instance_ids(instance_ids)
        if normalized is None:
            available = self._normalize_accessible_instance_ids(accessible_instance_ids)
            if available is not None:
                return sorted(available)
            return None
        available = self._normalize_accessible_instance_ids(accessible_instance_ids)
        if available is None:
            available = {target.id for target in self._load_targets()}
        invalid = [item for item in normalized if item not in available]
        if invalid:
            raise ValueError(f"Unknown or inaccessible instance ids: {', '.join(invalid)}")
        return normalized

    def _create_auth_store(self) -> AdminAuthStore:
        if self.registry_path is not None:
            admin_dir = get_softnix_admin_dir(infer_softnix_home_from_registry(self.registry_path))
        else:
            admin_dir = self.config_path.expanduser().resolve().parent / ".nanobot-admin"
        return AdminAuthStore(admin_dir)

    def get_health(self) -> dict[str, Any]:
        instances = self.list_instances()
        warnings = sum(len(item["security"]["findings"]) for item in instances)
        repo_root = Path(__file__).resolve().parents[2]
        git_status = _get_git_update_status(repo_root)
        return {
            "status": "ok",
            "service": "nanobot-admin",
            "version": __version__,
            "commit": git_status["current_commit"],
            "latest_commit": git_status["latest_commit"],
            "update_status": git_status["update_status"],
            "mode": "safe-config",
            "instance_count": len(instances),
            "warning_count": warnings,
            "capabilities": {
                "runtime_state": any(item["runtime"]["probe"]["available"] for item in instances),
                "instance_control": any(item["runtime"]["manageable"] for item in instances),
                "config_write": True,
            },
        }

    def get_mobile_pairing_data(
        self,
        instance_id: str,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Generate temporary pairing data for mobile app QR scan."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)

        pairing_token = f"pair-{new_csrf_token()[:12]}"
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()

        self.auth_store.create_pairing_token(
            instance_id=instance_id,
            token=pairing_token,
            expires_at=expires_at,
        )
        self.auth_store.append_audit(
            event_type="channel.mobile_pairing_created",
            category="configuration",
            outcome="success",
            resource={"type": "instance", "id": instance_id},
            payload={"expires_at": expires_at},
        )
        return {
            "instance_id": instance_id,
            "pairing_token": pairing_token,
            "expires_at": expires_at,
        }

    def _mobile_relay_dir(self, target: InstanceTarget) -> Path:
        relay_dir = target.workspace_path / "mobile_relay"
        relay_dir.mkdir(parents=True, exist_ok=True)
        return relay_dir

    def _mobile_upload_dir(self, target: InstanceTarget, sender_id: str) -> Path:
        upload_dir = self._mobile_relay_dir(target) / "uploads" / self._safe_filename(sender_id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        return upload_dir

    def _mobile_outbound_media_dir(self, target: InstanceTarget, sender_id: str) -> Path:
        media_dir = self._mobile_relay_dir(target) / "outbound_media" / self._safe_filename(sender_id)
        media_dir.mkdir(parents=True, exist_ok=True)
        return media_dir

    def _mobile_event_log_path(self, target: InstanceTarget, sender_id: str) -> Path:
        events_dir = self._mobile_relay_dir(target) / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        return events_dir / f"{self._safe_filename(sender_id)}.jsonl"

    def _append_mobile_chat_event(
        self,
        *,
        target: InstanceTarget,
        sender_id: str,
        role: str,
        session_id: str,
        message_id: str,
        text: str,
        msg_type: str,
        direction: str,
        reply_to: str | None = None,
        thread_root_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_id": f"mobevt-{secrets.token_hex(8)}",
            "instance_id": target.id,
            "device_id": sender_id,
            "role": role,
            "direction": direction,
            "type": msg_type,
            "session_id": session_id,
            "message_id": message_id,
            "reply_to": reply_to,
            "thread_root_id": thread_root_id,
            "text": text,
            "attachments": list(attachments or []),
            "timestamp": timestamp or iso_now(),
        }
        log_path = self._mobile_event_log_path(target, sender_id)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    @staticmethod
    def _mobile_event_signature(item: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
        return (
            str(item.get("session_id") or "").strip(),
            str(item.get("role") or "").strip(),
            str(item.get("message_id") or "").strip(),
            str(item.get("text") or item.get("content") or "").strip(),
            str(item.get("reply_to") or "").strip(),
            str(item.get("thread_root_id") or "").strip(),
        )

    @staticmethod
    def _event_text_preview(value: str | None, *, limit: int = 140) -> str:
        text = str(value or "").strip().replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"\s+", " ", text)
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    @staticmethod
    def _extract_session_message_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            chunks: list[str] = []
            for item in value:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    text = str(item.get("text") or "").strip()
                    if text:
                        chunks.append(text)
            return "\n".join(chunks).strip()
        return str(value or "").strip()

    def _load_mobile_device_events(self, *, target: InstanceTarget, sender_id: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        path = self._mobile_event_log_path(target, sender_id)
        if path.exists():
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if isinstance(data, dict):
                        events.append(data)
            except Exception:
                logger.exception("Failed to read mobile chat events for {}/{}", target.id, sender_id)

        outbound_events = self._load_mobile_outbound_events(target=target, sender_id=sender_id)
        seen_signatures = {self._mobile_event_signature(item) for item in events if isinstance(item, dict)}
        seen_message_ids = {
            str(item.get("message_id") or "").strip()
            for item in events
            if isinstance(item, dict) and str(item.get("message_id") or "").strip()
        }
        for item in outbound_events:
            if not isinstance(item, dict):
                continue
            signature = self._mobile_event_signature(item)
            message_id = str(item.get("message_id") or "").strip()
            if signature in seen_signatures or (message_id and message_id in seen_message_ids):
                continue
            seen_signatures.add(signature)
            if message_id:
                seen_message_ids.add(message_id)
            events.append(item)

        seen_signatures = {self._mobile_event_signature(item) for item in events if isinstance(item, dict)}
        modern_session_ids = {
            str(item.get("session_id") or "").strip()
            for item in events
            if isinstance(item, dict) and str(item.get("session_id") or "").strip()
        }
        manager = SessionManager(target.workspace_path)
        session_prefixes = (
            f"softnix_app:mobile-{sender_id}",
            f"mobile-{sender_id}",
        )
        for session_info in manager.list_sessions():
            key = str(session_info.get("key") or "").strip()
            if not any(key.startswith(prefix) for prefix in session_prefixes):
                continue
            session_id = key.split("softnix_app:", 1)[-1] if key.startswith("softnix_app:") else key
            if session_id in modern_session_ids:
                continue
            session = manager.get_or_create(key)
            for index, message in enumerate(session.messages):
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role") or "").strip()
                if role not in {"user", "assistant"}:
                    continue
                text = self._extract_session_message_text(message.get("content"))
                event = {
                    "event_id": f"legacy-{self._safe_filename(session_id)}-{index}",
                    "instance_id": target.id,
                    "device_id": sender_id,
                    "role": "agent" if role == "assistant" else "user",
                    "direction": "outbound" if role == "assistant" else "inbound",
                    "type": "answer" if role == "assistant" else "message",
                    "session_id": session_id,
                    "message_id": f"legacy-{index}",
                    "reply_to": None,
                    "thread_root_id": None,
                    "text": text,
                    "attachments": [],
                    "timestamp": str(message.get("timestamp") or session_info.get("updated_at") or ""),
                    "legacy": True,
                }
                signature = self._mobile_event_signature(event)
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                events.append(event)

        events.sort(key=lambda item: (str(item.get("timestamp") or ""), str(item.get("event_id") or "")))
        return events

    def _load_mobile_outbound_events(self, *, target: InstanceTarget, sender_id: str) -> list[dict[str, Any]]:
        outbound_file = self._mobile_relay_dir(target) / "outbound.jsonl"
        if not outbound_file.exists():
            return []

        normalized_sender_id = self._normalize_mobile_sender_identity(sender_id)
        session_prefix = f"mobile-{normalized_sender_id}" if normalized_sender_id else ""
        events: list[dict[str, Any]] = []
        try:
            for line in outbound_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if not isinstance(data, dict):
                    continue
                data_sender_id = self._normalize_mobile_sender_identity(str(data.get("sender_id") or ""))
                data_session_id = str(data.get("session_id") or "").strip()
                if not (
                    data_sender_id == normalized_sender_id
                    or data_session_id == sender_id
                    or (session_prefix and data_session_id.startswith(session_prefix))
                ):
                    continue
                message_id = str(data.get("message_id") or "").strip() or f"outbound-{secrets.token_hex(8)}"
                events.append(
                    {
                        "event_id": str(data.get("event_id") or "").strip() or f"outbound-{message_id}",
                        "instance_id": target.id,
                        "device_id": data_sender_id or sender_id,
                        "role": "agent",
                        "direction": "outbound",
                        "type": str(data.get("type") or "answer"),
                        "session_id": data_session_id or f"mobile-{sender_id}",
                        "message_id": message_id,
                        "reply_to": str(data.get("reply_to") or "").strip() or None,
                        "thread_root_id": str(data.get("thread_root_id") or "").strip() or None,
                        "text": str(data.get("text") or ""),
                        "attachments": list(data.get("attachments") or []),
                        "timestamp": str(data.get("timestamp") or ""),
                    }
                )
        except Exception:
            logger.exception("Failed to read outbound mobile events for {}/{}", target.id, sender_id)
        return events

    def _build_mobile_conversation_summaries(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        conversations: dict[str, dict[str, Any]] = {}
        for item in events:
            session_id = str(item.get("session_id") or "").strip()
            if not session_id:
                continue
            bucket = conversations.setdefault(
                session_id,
                {
                    "session_id": session_id,
                    "message_count": 0,
                    "updated_at": "",
                    "preview": "",
                    "has_agent_reply": False,
                    "has_user_message": False,
                },
            )
            bucket["message_count"] += 1
            ts = str(item.get("timestamp") or "")
            if ts >= str(bucket.get("updated_at") or ""):
                bucket["updated_at"] = ts
                bucket["preview"] = self._event_text_preview(item.get("text"))
            if str(item.get("role") or "") == "agent":
                bucket["has_agent_reply"] = True
            if str(item.get("role") or "") == "user":
                bucket["has_user_message"] = True
        return sorted(conversations.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def _mobile_push_supported(self) -> bool:
        try:
            import pywebpush  # noqa: F401
            from cryptography.hazmat.primitives import serialization  # noqa: F401
            from cryptography.hazmat.primitives.asymmetric import ec  # noqa: F401
        except Exception:
            return False
        return True

    @staticmethod
    def _normalize_mobile_sender_identity(value: str | None) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if raw.startswith("mobile-"):
            raw = raw[len("mobile-"):]
        match = re.match(
            r"^(mob-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:-.+)?$",
            raw,
        )
        if match:
            return match.group(1)
        return raw

    def _ensure_mobile_push_keys(self) -> dict[str, Any] | None:
        existing = self.auth_store.get_mobile_push_keys()
        if existing:
            subject = str(existing.get("subject") or "").strip()
            if not subject or "localhost" in subject:
                private_key_path = Path(str(existing["private_key_path"]))
                private_pem = private_key_path.read_text(encoding="utf-8")
                return self.auth_store.save_mobile_push_keys(
                    public_key=str(existing["public_key"]),
                    private_key_pem=private_pem,
                    subject=_default_mobile_push_subject(),
                )
            return existing
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
        except Exception:
            return None

        private_key = ec.generate_private_key(ec.SECP256R1())
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        numbers = private_key.public_key().public_numbers()
        public_bytes = b"\x04" + numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")
        public_key = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode("ascii")
        return self.auth_store.save_mobile_push_keys(
            public_key=public_key,
            private_key_pem=private_pem,
            subject=_default_mobile_push_subject(),
        )

    def get_mobile_push_config(self) -> dict[str, Any]:
        keys = self._ensure_mobile_push_keys() if self._mobile_push_supported() else None
        return {
            "supported": keys is not None,
            "public_key": keys.get("public_key") if keys else None,
            "requires_standalone": True,
        }

    def create_web_chat_login(
        self,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        ticket = f"wclogin-{secrets.token_urlsafe(18)}"
        self.auth_store.create_web_chat_login_ticket(
            ticket=ticket,
            expires_at=expires_at,
            ip=ip,
            user_agent=user_agent,
        )
        self.auth_store.append_audit(
            event_type="auth.web_chat_login_created",
            category="authentication",
            outcome="success",
            actor={"ip": ip, "user_agent": (user_agent or "")[:300] or None},
            payload={"expires_at": expires_at},
        )
        return {
            "login_ticket": ticket,
            "expires_at": expires_at,
            "qr_payload": f"softnix://web-chat-login?ticket={ticket}",
        }

    def get_web_chat_login_status(self, *, login_ticket: str) -> dict[str, Any]:
        ticket = self.auth_store.get_web_chat_login_ticket(login_ticket)
        if ticket is None:
            return {"status": "expired", "login_ticket": str(login_ticket or "").strip()}
        return {
            "status": str(ticket.get("status") or "pending"),
            "login_ticket": str(ticket.get("ticket") or "").strip(),
            "expires_at": ticket.get("expires_at"),
            "instance_id": ticket.get("instance_id"),
            "device_id": ticket.get("device_id"),
            "device_label": ticket.get("device_label"),
            "active_session_id": ticket.get("active_session_id"),
            "approved_at": ticket.get("approved_at"),
            "request_ip_masked": _mask_ip_for_display(ticket.get("ip")),
            "request_user_agent_label": _user_agent_label(ticket.get("user_agent")),
        }

    def approve_web_chat_login(
        self,
        *,
        login_ticket: str,
        device: dict[str, Any],
        active_session_id: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        instance_id = str(device.get("instance_id") or "").strip()
        device_id = str(device.get("device_id") or "").strip()
        if not instance_id or not device_id:
            raise ValueError("Mobile device context is incomplete")
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        config = self._load_target_config(target)
        if device_id not in list(config.channels.softnix_app.allow_from or []):
            raise PermissionError("This mobile device is not approved for chat access")
        updated = self.auth_store.approve_web_chat_login_ticket(
            ticket=login_ticket,
            instance_id=instance_id,
            device_id=device_id,
            device_label=str(device.get("label") or device_id),
            active_session_id=str(active_session_id or "").strip() or None,
        )
        if updated is None:
            raise ValueError("Invalid or expired web chat login ticket")
        self.auth_store.append_audit(
            event_type="auth.web_chat_login_approved",
            category="authentication",
            outcome="success",
            resource={"type": "instance", "id": instance_id},
            payload={"device_id": device_id, "active_session_id": updated.get("active_session_id")},
        )
        return {
            "status": "approved",
            "instance_id": instance_id,
            "device_id": device_id,
            "device_label": updated.get("device_label"),
            "active_session_id": updated.get("active_session_id"),
        }

    def exchange_web_chat_login(
        self,
        *,
        login_ticket: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        ticket = self.auth_store.consume_web_chat_login_ticket(login_ticket)
        if ticket is None:
            raise ValueError("Invalid or unapproved web chat login ticket")
        csrf_token = new_csrf_token()
        session = self.auth_store.create_web_chat_session(
            session_id=new_session_id(),
            instance_id=str(ticket.get("instance_id") or ""),
            device_id=str(ticket.get("device_id") or ""),
            device_label=str(ticket.get("device_label") or "") or None,
            active_session_id=str(ticket.get("active_session_id") or "") or None,
            ip=ip,
            user_agent=user_agent,
            csrf_token=csrf_token,
        )
        self.auth_store.append_audit(
            event_type="auth.web_chat_session_created",
            category="authentication",
            outcome="success",
            resource={"type": "instance", "id": session["instance_id"]},
            payload={"device_id": session["device_id"], "session_id": session["id"]},
        )
        return {
            "device": {
                "instance_id": session["instance_id"],
                "device_id": session["device_id"],
                "label": session.get("device_label") or session["device_id"],
            },
            "session": {
                "id": session["id"],
                "expires_at": session.get("expires_at"),
                "idle_expires_at": session.get("idle_expires_at"),
                "csrf_token": session.get("csrf_token"),
                "active_session_id": session.get("active_session_id"),
            },
        }

    def get_authenticated_web_chat_session(self, *, session_id: str) -> dict[str, Any] | None:
        session = self.auth_store.get_web_chat_session(session_id)
        if session is None:
            return None
        instance_id = str(session.get("instance_id") or "").strip()
        device_id = str(session.get("device_id") or "").strip()
        if not instance_id or not device_id:
            self.auth_store.revoke_web_chat_session(session_id)
            return None
        target = self._get_target(instance_id)
        device = self.auth_store.get_mobile_device(instance_id, device_id)
        if device is None:
            self.auth_store.revoke_web_chat_session(session_id)
            return None
        config = self._load_target_config(target)
        if device_id not in list(config.channels.softnix_app.allow_from or []):
            self.auth_store.revoke_web_chat_session(session_id)
            return None
        csrf_token = str(session.get("csrf_token") or "")
        if not csrf_token:
            csrf_token = new_csrf_token()
        session = self.auth_store.touch_web_chat_session(session_id, csrf_token=csrf_token) or session
        return {
            "device": {
                "instance_id": instance_id,
                "device_id": device_id,
                "label": device.get("label") or device_id,
                "approval_status": "approved",
            },
            "session": {
                "id": session.get("id"),
                "expires_at": session.get("expires_at"),
                "idle_expires_at": session.get("idle_expires_at"),
                "csrf_token": session.get("csrf_token"),
                "active_session_id": session.get("active_session_id"),
            },
        }

    def logout_web_chat_session(self, *, session_id: str) -> dict[str, Any]:
        revoked = self.auth_store.revoke_web_chat_session(session_id)
        return {"ok": revoked}

    def set_web_chat_active_session(
        self,
        *,
        session_id: str,
        active_session_id: str,
    ) -> dict[str, Any]:
        context = self.get_authenticated_web_chat_session(session_id=session_id)
        if context is None:
            raise PermissionError("Web chat session is not authenticated")
        updated = self.auth_store.touch_web_chat_session(
            session_id,
            active_session_id=str(active_session_id or "").strip() or None,
        )
        if updated is None:
            raise PermissionError("Web chat session is not authenticated")
        return {
            "ok": True,
            "active_session_id": updated.get("active_session_id"),
        }

    def get_mobile_chat_events(
        self,
        instance_id: str,
        sender_id: str,
        *,
        after_event_id: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        events = self._load_mobile_device_events(target=target, sender_id=sender_id)
        marker = str(after_event_id or "").strip()
        if not marker:
            return events
        seen_marker = False
        filtered: list[dict[str, Any]] = []
        for item in events:
            if seen_marker:
                filtered.append(item)
                continue
            if str(item.get("event_id") or "") == marker:
                seen_marker = True
        return filtered if seen_marker else events

    def get_mobile_chat_bootstrap(
        self,
        instance_id: str,
        sender_id: str,
        *,
        preferred_active_session_id: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        device = self.auth_store.get_mobile_device(instance_id, sender_id)
        if device is None:
            raise ValueError("Mobile device not found")
        events = self._load_mobile_device_events(target=target, sender_id=sender_id)
        conversations = self._build_mobile_conversation_summaries(events)
        active_session_id = str(preferred_active_session_id or "").strip()
        if active_session_id and not any(item.get("session_id") == active_session_id for item in conversations):
            active_session_id = ""
        if not active_session_id:
            active_session_id = str(conversations[0]["session_id"]) if conversations else f"mobile-{sender_id}"
        return {
            "device": {
                "device_id": sender_id,
                "instance_id": instance_id,
                "label": device.get("label") or sender_id,
                "approval_status": "approved",
                "already_allowed": True,
            },
            "active_session_id": active_session_id,
            "conversations": conversations,
            "events": events,
        }

    def create_mobile_session_transfer(
        self,
        *,
        device: dict[str, Any],
        active_session_id: str | None,
        conversations: dict[str, Any] | None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        instance_id = str(device.get("instance_id") or "").strip()
        device_id = str(device.get("device_id") or "").strip()
        if not instance_id or not device_id:
            raise ValueError("device.instance_id and device.device_id are required")
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        token = f"xfer-{secrets.token_hex(6)}"
        payload = {
            "device": device,
            "activeSessionId": str(active_session_id or device.get("current_session_id") or f"mobile-{device_id}"),
            "conversations": conversations if isinstance(conversations, dict) else {},
        }
        self.auth_store.create_mobile_transfer_token(token=token, payload=payload, expires_at=expires_at)
        self.auth_store.append_audit(
            event_type="channel.mobile_session_transfer_created",
            category="configuration",
            outcome="success",
            resource={"type": "instance", "id": instance_id},
            payload={"device_id": device_id, "expires_at": expires_at},
        )
        return {"transfer_token": token, "expires_at": expires_at}

    def consume_mobile_session_transfer(self, *, transfer_token: str) -> dict[str, Any]:
        payload = self.auth_store.consume_mobile_transfer_token(transfer_token)
        if payload is None:
            raise ValueError("Invalid or expired transfer token")
        device = payload.get("device")
        if not isinstance(device, dict):
            raise ValueError("Transfer payload is invalid")
        instance_id = str(device.get("instance_id") or "").strip()
        device_id = str(device.get("device_id") or "").strip()
        if instance_id and device_id:
            self.auth_store.update_device_last_seen(instance_id, device_id)
            self.auth_store.append_audit(
                event_type="channel.mobile_session_transfer_consumed",
                category="configuration",
                outcome="success",
                resource={"type": "instance", "id": instance_id},
                payload={"device_id": device_id},
            )
        return payload

    def subscribe_mobile_push(
        self,
        *,
        instance_id: str,
        device_id: str,
        subscription: dict[str, Any],
        user_agent: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        if not self._mobile_push_supported():
            raise ValueError("Web push is not available on this server")
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        devices = self.auth_store.list_mobile_devices(instance_id)
        if not any(item.get("device_id") == device_id for item in devices):
            raise ValueError("Mobile device not found")
        endpoint = str(subscription.get("endpoint") or "").strip()
        keys = subscription.get("keys") if isinstance(subscription, dict) else None
        if not endpoint or not isinstance(keys, dict) or not str(keys.get("p256dh") or "").strip() or not str(keys.get("auth") or "").strip():
            raise ValueError("Invalid push subscription payload")
        self.auth_store.upsert_mobile_push_subscription(
            instance_id=instance_id,
            device_id=device_id,
            subscription=subscription,
            endpoint=endpoint,
            user_agent=user_agent,
        )
        self.auth_store.append_audit(
            event_type="channel.mobile_push_subscribed",
            category="configuration",
            outcome="success",
            resource={"type": "instance", "id": instance_id},
            payload={"device_id": device_id},
        )
        return {"status": "subscribed", "device_id": device_id}

    def unsubscribe_mobile_push(
        self,
        *,
        instance_id: str,
        device_id: str,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        removed = self.auth_store.delete_mobile_push_subscription(instance_id=instance_id, device_id=device_id)
        if removed:
            self.auth_store.append_audit(
                event_type="channel.mobile_push_unsubscribed",
                category="configuration",
                outcome="success",
                resource={"type": "instance", "id": instance_id},
                payload={"device_id": device_id},
            )
        return {"status": "unsubscribed", "device_id": device_id}

    def _start_mobile_push_worker(self) -> None:
        if self._mobile_push_worker is not None:
            return
        self._mobile_push_worker = threading.Thread(
            target=self._mobile_push_loop,
            name="softnix-mobile-push",
            daemon=True,
        )
        self._mobile_push_worker.start()

    def _persist_mobile_push_offsets(self) -> None:
        try:
            self._mobile_push_offsets = self.auth_store.save_mobile_push_offsets(self._mobile_push_offsets)
        except Exception as exc:
            logger.debug("Failed to persist mobile push offsets: {}", exc)

    def _mobile_push_loop(self) -> None:
        while not self._mobile_push_stop.wait(2.0):
            try:
                self._scan_mobile_push_events()
            except Exception as exc:
                logger.debug(f"Mobile push scan failed: {exc}")

    def _scan_mobile_push_events(self) -> None:
        if not self._mobile_push_supported():
            return
        offsets_dirty = False
        try:
            for target in self._load_targets():
                outbound_file = self._mobile_relay_dir(target) / "outbound.jsonl"
                offset_key = str(outbound_file.resolve())
                if not outbound_file.exists():
                    if offset_key in self._mobile_push_offsets:
                        self._mobile_push_offsets.pop(offset_key, None)
                        offsets_dirty = True
                    continue

                size = outbound_file.stat().st_size
                stored_offset = max(int(self._mobile_push_offsets.get(offset_key, 0) or 0), 0)
                offset = stored_offset
                if size < offset:
                    offset = 0
                if offset != stored_offset:
                    self._mobile_push_offsets[offset_key] = offset
                    offsets_dirty = True
                if size == offset:
                    continue

                with outbound_file.open("r", encoding="utf-8") as handle:
                    handle.seek(offset)
                    lines = handle.readlines()
                    next_offset = handle.tell()

                if next_offset != int(self._mobile_push_offsets.get(offset_key, 0) or 0):
                    self._mobile_push_offsets[offset_key] = next_offset
                    offsets_dirty = True

                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except Exception:
                        continue
                    self._dispatch_mobile_push(target.id, payload)
        finally:
            if offsets_dirty:
                self._persist_mobile_push_offsets()

    def _dispatch_mobile_push(self, instance_id: str, payload: dict[str, Any]) -> None:
        sender_id = self._normalize_mobile_sender_identity(
            str(payload.get("sender_id") or payload.get("session_id") or "").strip()
        )
        if not sender_id or str(payload.get("type") or "answer") != "answer":
            return
        subscriptions = self.auth_store.list_mobile_push_subscriptions(instance_id, sender_id)
        if not subscriptions:
            return
        title = "Softnix Agent"
        body = _truncate_text(payload.get("text") or "You have a new reply", limit=180)
        data = {
            "title": title,
            "body": body,
            "icon": "/static/Logo_Softnix.png",
            "url": f"/mobile?session_id={payload.get('session_id') or ''}",
            "session_id": payload.get("session_id"),
            "message_id": payload.get("message_id"),
            "instance_id": instance_id,
        }
        for item in subscriptions:
            self._send_web_push_notification(instance_id=instance_id, device_id=sender_id, subscription=item, payload=data)

    def _send_web_push_notification(
        self,
        *,
        instance_id: str,
        device_id: str,
        subscription: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        keys = self._ensure_mobile_push_keys()
        if keys is None:
            return
        subscription_info = subscription.get("subscription")
        if not isinstance(subscription_info, dict):
            return
        try:
            from pywebpush import WebPushException, webpush

            webpush(
                subscription_info=subscription_info,
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=keys["private_key_path"],
                vapid_claims={"sub": keys["subject"]},
                ttl=300,
            )
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if isinstance(exc, WebPushException) and status_code in {404, 410}:
                self.auth_store.delete_mobile_push_subscription(instance_id=instance_id, device_id=device_id)
            logger.debug(f"Mobile push delivery failed for {instance_id}/{device_id}: {exc}")

    @staticmethod
    def _safe_filename(value: str) -> str:
        raw = os.path.basename(str(value or "").strip()) or "file"
        sanitized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw)
        return sanitized[:120] or "file"

    def _save_mobile_attachments(
        self,
        *,
        target: InstanceTarget,
        sender_id: str,
        attachments: list[dict[str, Any]] | None,
        max_attachment_bytes: int | None = None,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        if not attachments:
            return [], []
        saved_paths: list[str] = []
        saved_meta: list[dict[str, Any]] = []
        upload_dir = self._mobile_upload_dir(target, sender_id)
        limit_bytes = int(max_attachment_bytes or self.max_mobile_attachment_bytes)

        for index, item in enumerate(attachments):
            if not isinstance(item, dict):
                continue
            name = self._safe_filename(str(item.get("name") or f"attachment-{index + 1}"))
            stored_name = self._safe_filename(str(item.get("stored_name") or item.get("file_name") or ""))
            encoded = str(item.get("data_base64") or "").strip()
            mime = str(
                item.get("type")
                or item.get("mime_type")
                or item.get("mimeType")
                or mimetypes.guess_type(name)[0]
                or "application/octet-stream"
            )
            if stored_name and not encoded:
                dest = (upload_dir / stored_name).resolve()
                try:
                    dest.relative_to(upload_dir.resolve())
                except ValueError as exc:
                    raise ValueError(f"Attachment '{name}' references an invalid stored file") from exc
                if not dest.exists() or not dest.is_file():
                    raise ValueError(f"Attachment '{name}' was not uploaded successfully")
                size = dest.stat().st_size
                if size > limit_bytes:
                    raise ValueError(f"Attachment '{name}' exceeds the maximum size of {limit_bytes} bytes")
                saved_paths.append(str(dest))
                saved_meta.append(
                    {
                        "name": name,
                        "stored_name": stored_name,
                        "mime_type": mime,
                        "size": size,
                    }
                )
                continue
            if not encoded:
                continue
            try:
                payload = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError(f"Attachment #{index + 1} is not valid base64") from exc
            if len(payload) > limit_bytes:
                raise ValueError(f"Attachment '{name}' exceeds the maximum size of {limit_bytes} bytes")
            stem = Path(name).stem or f"attachment-{index + 1}"
            suffix = Path(name).suffix
            stored_name = f"{int(time.time() * 1000)}-{secrets.token_hex(4)}-{self._safe_filename(stem)}{suffix}"
            dest = upload_dir / stored_name
            dest.write_bytes(payload)
            saved_paths.append(str(dest))
            saved_meta.append(
                {
                    "name": name,
                    "stored_name": stored_name,
                    "mime_type": mime,
                    "size": len(payload),
                }
            )
        return saved_paths, saved_meta

    @staticmethod
    def _build_mobile_attachment_response_item(
        *,
        instance_id: str,
        sender_id: str,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            **item,
            "file_name": item.get("stored_name"),
            "sender_id": sender_id,
            "url": (
                f"/admin/mobile/media?instance_id={instance_id}"
                f"&sender_id={sender_id}&file={item['stored_name']}"
            ),
        }

    def upload_web_chat_attachments(
        self,
        *,
        web_session_id: str,
        files: list[dict[str, Any]],
    ) -> dict[str, Any]:
        context = self.get_authenticated_web_chat_session(session_id=web_session_id)
        if context is None:
            raise PermissionError("Web chat session is not authenticated")
        instance_id = str(context["device"]["instance_id"] or "").strip()
        device_id = str(context["device"]["device_id"] or "").strip()
        target = self._get_target(instance_id)

        attachments: list[dict[str, Any]] = []
        for index, item in enumerate(files):
            if not isinstance(item, dict):
                continue
            name = self._safe_filename(str(item.get("name") or f"attachment-{index + 1}"))
            payload = item.get("payload")
            if not isinstance(payload, (bytes, bytearray)):
                continue
            mime_type = str(item.get("type") or mimetypes.guess_type(name)[0] or "application/octet-stream")
            encoded = base64.b64encode(bytes(payload)).decode("ascii")
            _, saved_meta = self._save_mobile_attachments(
                target=target,
                sender_id=device_id,
                attachments=[{"name": name, "type": mime_type, "data_base64": encoded}],
                max_attachment_bytes=self.max_web_chat_attachment_bytes,
            )
            if not saved_meta:
                continue
            attachments.append(
                self._build_mobile_attachment_response_item(
                    instance_id=instance_id,
                    sender_id=device_id,
                    item=saved_meta[0],
                )
            )

        return {
            "status": "uploaded",
            "instance_id": instance_id,
            "attachment_count": len(attachments),
            "attachments": attachments,
            "max_attachment_bytes": self.max_web_chat_attachment_bytes,
        }

    @staticmethod
    def _is_audio_attachment(path: Path, mime_type: str) -> bool:
        normalized_mime = str(mime_type or "").strip().lower()
        if normalized_mime.startswith("audio/"):
            return True
        return path.suffix.lower() in {".mp3", ".m4a", ".mp4", ".aac", ".wav", ".flac", ".ogg", ".webm"}

    def _transcribe_audio_path(
        self,
        *,
        target: InstanceTarget,
        source_path: Path,
        mime_type: str,
    ) -> str:
        transcribe_path = source_path
        detected_mime = self._detect_audio_mime(source_path) or mime_type
        supported_audio_types = {
            "audio/mpeg",
            "audio/mp4",
            "audio/aac",
            "audio/wav",
            "audio/flac",
            "audio/x-m4a",
        }
        if detected_mime not in supported_audio_types:
            transcoded = self._transcode_to_mp3(source_path)
            if transcoded is not None:
                transcribe_path = transcoded

        config = self._load_target_config(target)
        groq_cfg = getattr(config.providers, "groq", None)
        api_key = str(getattr(groq_cfg, "api_key", "") or "").strip()
        if not api_key:
            raise ValueError("Groq API key is not configured for transcription")

        from nanobot.providers.transcription import GroqTranscriptionProvider

        async def _run_transcription() -> str:
            transcriber = GroqTranscriptionProvider(api_key=api_key)
            return await transcriber.transcribe(transcribe_path)

        return asyncio.run(_run_transcription()).strip()

    def _build_audio_attachment_transcripts(
        self,
        *,
        target: InstanceTarget,
        media_paths: list[str],
        attachment_meta: list[dict[str, Any]],
    ) -> list[str]:
        transcript_blocks: list[str] = []
        for path_str, meta in zip(media_paths, attachment_meta):
            path = Path(path_str)
            mime_type = str(meta.get("mime_type") or "")
            if not path.is_file() or not self._is_audio_attachment(path, mime_type):
                continue
            try:
                transcript = self._transcribe_audio_path(
                    target=target,
                    source_path=path,
                    mime_type=mime_type,
                )
            except ValueError as exc:
                logger.info(
                    "mobile.audio_transcription.skipped {}",
                    {
                        "instance_id": target.id,
                        "file": path.name,
                        "reason": str(exc),
                    },
                )
                continue
            except Exception as exc:
                logger.warning(
                    "mobile.audio_transcription.failed {}",
                    {
                        "instance_id": target.id,
                        "file": path.name,
                        "error": str(exc),
                    },
                )
                continue
            if not transcript:
                continue
            transcript_blocks.append(
                "[Extracted Audio Transcript]\n"
                f"File: {meta.get('name') or path.name}\n"
                f"{transcript}"
            )
        return transcript_blocks

    def transcribe_mobile_audio(
        self,
        instance_id: str,
        sender_id: str,
        audio: dict[str, Any],
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        target = next((t for t in self._load_targets() if t.id == instance_id), None)
        if not target:
            raise ValueError(f"Instance '{instance_id}' not found")
        self._require_target_access(target, accessible_instance_ids)

        encoded = str(audio.get("data_base64") or "").strip()
        if not encoded:
            raise ValueError("audio.data_base64 is required")

        name = self._safe_filename(str(audio.get("name") or "voice-recording"))
        mime_type = str(audio.get("type") or mimetypes.guess_type(name)[0] or "application/octet-stream")
        raw_payload = base64.b64decode(encoded, validate=True)

        transcribe_root = self._mobile_relay_dir(target) / "transcriptions" / self._safe_filename(sender_id)
        transcribe_root.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="voice-", dir=transcribe_root))
        source_suffix = Path(name).suffix or mimetypes.guess_extension(mime_type) or ".bin"
        source_path = temp_dir / f"source{source_suffix}"
        source_path.write_bytes(raw_payload)

        transcribe_path = source_path
        try:
            transcript = self._transcribe_audio_path(
                target=target,
                source_path=transcribe_path,
                mime_type=mime_type,
            )
            return {
                "transcript": transcript,
                "name": name,
                "mime_type": mime_type,
                "size": len(raw_payload),
            }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def relay_mobile_message(
        self,
        instance_id: str,
        sender_id: str,
        text: str,
        *,
        session_id: str | None = None,
        message_id: str | None = None,
        reply_to: str | None = None,
        thread_root_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
        max_attachment_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Relay a message from a mobile app to a specific instance via file-based queue."""
        target = next((t for t in self._load_targets() if t.id == instance_id), None)
        if not target:
            raise ValueError(f"Instance '{instance_id}' not found")
        self._require_target_access(target, accessible_instance_ids)

        relay_dir = self._mobile_relay_dir(target)
        inbound_file = relay_dir / "inbound.jsonl"
        media_paths, attachment_meta = self._save_mobile_attachments(
            target=target,
            sender_id=sender_id,
            attachments=attachments,
            max_attachment_bytes=max_attachment_bytes,
        )
        transcript_blocks = self._build_audio_attachment_transcripts(
            target=target,
            media_paths=media_paths,
            attachment_meta=attachment_meta,
        )
        normalized_text = (text or "").strip()
        if transcript_blocks:
            normalized_text = normalized_text or "Please use the uploaded audio attachment."
            agent_text = normalized_text + "\n\n" + "\n\n".join(transcript_blocks)
        else:
            agent_text = text
        resolved_session_id = (session_id or "").strip() or f"mobile-{sender_id}"
        resolved_message_id = (message_id or "").strip() or f"mobile-{secrets.token_hex(8)}"
        data = {
            "text": agent_text,
            "sender_id": sender_id,
            "session_id": resolved_session_id,
            "message_id": resolved_message_id,
            "reply_to": (reply_to or "").strip() or None,
            "thread_root_id": (thread_root_id or "").strip() or None,
            "media": media_paths,
            "attachments": attachment_meta,
            "timestamp": iso_now(),
        }

        with inbound_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")

        response_attachments = [
            self._build_mobile_attachment_response_item(
                instance_id=instance_id,
                sender_id=sender_id,
                item=item,
            )
            for item in attachment_meta
        ]
        self._append_mobile_chat_event(
            target=target,
            sender_id=sender_id,
            role="user",
            session_id=resolved_session_id,
            message_id=resolved_message_id,
            text=text,
            msg_type="message",
            direction="inbound",
            reply_to=(reply_to or "").strip() or None,
            thread_root_id=(thread_root_id or "").strip() or None,
            attachments=response_attachments,
            timestamp=data["timestamp"],
        )

        return {
            "status": "relayed",
            "instance_id": instance_id,
            "message_id": resolved_message_id,
            "session_id": resolved_session_id,
            "attachment_count": len(attachment_meta),
            "attachments": response_attachments,
        }

    def relay_web_chat_message(
        self,
        *,
        web_session_id: str,
        text: str,
        chat_session_id: str | None = None,
        message_id: str | None = None,
        reply_to: str | None = None,
        thread_root_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        context = self.get_authenticated_web_chat_session(session_id=web_session_id)
        if context is None:
            raise PermissionError("Web chat session is not authenticated")
        instance_id = str(context["device"]["instance_id"] or "").strip()
        device_id = str(context["device"]["device_id"] or "").strip()
        resolved_chat_session_id = str(chat_session_id or "").strip() or str(context["session"].get("active_session_id") or "") or f"mobile-{device_id}"
        self.auth_store.touch_web_chat_session(
            web_session_id,
            active_session_id=resolved_chat_session_id,
        )
        return self.relay_mobile_message(
            instance_id,
            device_id,
            text,
            session_id=resolved_chat_session_id,
            message_id=message_id,
            reply_to=reply_to,
            thread_root_id=thread_root_id,
            attachments=attachments,
            accessible_instance_ids={instance_id},
            max_attachment_bytes=self.max_web_chat_attachment_bytes,
        )

    def get_mobile_replies(
        self,
        instance_id: str,
        sender_id: str,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch agent replies for a mobile user from the outbound queue."""
        target = next((t for t in self._load_targets() if t.id == instance_id), None)
        if not target:
            return []
        if not self._is_target_accessible(target, accessible_instance_ids):
            return []

        outbound_file = target.workspace_path / "mobile_relay" / "outbound.jsonl"
        if not outbound_file.exists():
            return []

        normalized_sender_id = self._normalize_mobile_sender_identity(sender_id)
        session_prefix = f"mobile-{normalized_sender_id}" if normalized_sender_id else ""
            
        all_replies = []
        remaining_lines = []
        
        try:
            lines = outbound_file.read_text().splitlines()
            for line in lines:
                if not line.strip():
                    continue
                data = json.loads(line)
                data_sender_id = self._normalize_mobile_sender_identity(str(data.get("sender_id") or ""))
                data_session_id = str(data.get("session_id") or "").strip()
                if (
                    data_sender_id == normalized_sender_id
                    or data_session_id == sender_id
                    or (session_prefix and data_session_id.startswith(session_prefix))
                ):
                    all_replies.append(data)
                else:
                    remaining_lines.append(line)
            
            # Update file to remove fetched messages
            if all_replies:
                outbound_file.write_text("\n".join(remaining_lines) + ("\n" if remaining_lines else ""))
                
        except Exception as e:
            logger.error(f"Error fetching mobile replies: {e}")
            
        return all_replies

    @staticmethod
    def _detect_audio_mime(path: Path) -> str | None:
        """Detect actual audio MIME type from file magic bytes."""
        try:
            header = path.read_bytes()[:36]
        except Exception:
            return None
        if len(header) < 4:
            return None
        # OGG container (Vorbis / Opus) — iOS Safari cannot play these
        if header[:4] == b"OggS":
            return "audio/ogg"
        # RIFF/WAV
        if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
            return "audio/wav"
        # FLAC
        if header[:4] == b"fLaC":
            return "audio/flac"
        # MP3: sync word 0xFFE0..0xFFFF or ID3 tag
        if header[:3] == b"ID3" or (header[0] == 0xFF and (header[1] & 0xE0) == 0xE0):
            return "audio/mpeg"
        # AAC ADTS
        if header[0] == 0xFF and (header[1] & 0xF0) == 0xF0:
            return "audio/aac"
        # MP4/M4A container
        if len(header) >= 8 and header[4:8] == b"ftyp":
            return "audio/mp4"
        return None

    @staticmethod
    def _transcode_to_mp3(source: Path) -> Path | None:
        """Transcode an unsupported audio file to MP3 via pydub/ffmpeg."""
        mp3_path = source.with_suffix(".transcoded.mp3")
        if mp3_path.exists() and mp3_path.stat().st_mtime >= source.stat().st_mtime:
            return mp3_path
        # Try pydub first (wraps ffmpeg with better path discovery)
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(str(source))
            audio.export(str(mp3_path), format="mp3")
            if mp3_path.exists():
                return mp3_path
        except Exception:
            pass
        # Fall back to direct ffmpeg subprocess
        try:
            import subprocess
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", str(source), "-codec:a", "libmp3lame", "-q:a", "2", str(mp3_path)],
                capture_output=True, timeout=30,
            )
            if result.returncode == 0 and mp3_path.exists():
                return mp3_path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    @staticmethod
    def _validate_rtsp_url(value: str) -> str:
        raw = str(value or "").strip()
        parsed = urlparse(raw)
        if parsed.scheme not in {"rtsp", "rtsps"} or not parsed.netloc:
            raise ValueError("rtsp_url must use rtsp:// or rtsps://")
        return raw

    def _rtsp_sources_path(self, target: InstanceTarget) -> Path:
        return self._mobile_relay_dir(target) / "rtsp_sources.json"

    def _lookup_rtsp_source(self, target: InstanceTarget, file_name: str) -> str | None:
        path = self._rtsp_sources_path(target)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        sources = payload.get("sources")
        if not isinstance(sources, dict):
            return None
        record = sources.get(file_name)
        if isinstance(record, dict):
            return str(record.get("url") or "").strip() or None
        if isinstance(record, str):
            return str(record).strip() or None
        return None

    def _capture_rtsp_snapshot(self, *, rtsp_url: str, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-rtsp_transport",
                "tcp",
                "-i",
                rtsp_url,
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(output_path),
            ],
            capture_output=True,
            timeout=20,
        )
        if result.returncode != 0 or not output_path.exists():
            stderr = result.stderr.decode("utf-8", errors="ignore").strip()[:300]
            raise ValueError(f"Unable to capture RTSP snapshot{': ' + stderr if stderr else ''}")

    def _ensure_rtsp_snapshot_file(self, *, target: InstanceTarget, file_name: str) -> Path:
        rtsp_url = self._lookup_rtsp_source(target, file_name)
        if not rtsp_url:
            raise ValueError("RTSP snapshot source not found")
        rtsp_url = self._validate_rtsp_url(rtsp_url)
        digest = hashlib.sha256(rtsp_url.encode("utf-8")).hexdigest()[:24]
        expected_file_name = f"{digest}.jpg"
        if file_name != expected_file_name:
            raise ValueError("RTSP snapshot file is invalid")
        snapshot_dir = self._mobile_upload_dir(target, "rtsp")
        snapshot_path = snapshot_dir / expected_file_name
        if snapshot_path.exists():
            age_seconds = max(0.0, time.time() - snapshot_path.stat().st_mtime)
            if age_seconds <= float(self.rtsp_snapshot_cache_seconds):
                return snapshot_path
        self._capture_rtsp_snapshot(rtsp_url=rtsp_url, output_path=snapshot_path)
        return snapshot_path

    # MIME types that iOS Safari can play natively
    _IOS_PLAYABLE_AUDIO = {"audio/mpeg", "audio/mp4", "audio/aac", "audio/wav", "audio/flac", "audio/x-m4a"}

    def get_mobile_media_file(
        self,
        instance_id: str,
        sender_id: str,
        file_name: str,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> tuple[Path, str]:
        """Resolve one mobile relay media file for safe download."""
        target = next(
            (
                item
                for item in self._load_targets()
                if item.id == instance_id or item.workspace_path.name == instance_id
            ),
            None,
        )
        if target is None:
            raise ValueError("Instance not found")
        self._require_target_access(target, accessible_instance_ids)
        safe_sender = self._safe_filename(sender_id)
        safe_name = self._safe_filename(file_name)
        roots = [
            self._mobile_outbound_media_dir(target, safe_sender),
            self._mobile_upload_dir(target, safe_sender),
        ]
        searched_roots = [str(root) for root in roots]
        for root in roots:
            candidate = (root / safe_name).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                continue
            if candidate.exists() and candidate.is_file():
                # Detect actual format from file content, not just extension
                detected = self._detect_audio_mime(candidate)
                guessed = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
                content_type = detected or guessed
                # If the format isn't playable on iOS Safari, try transcoding
                if detected and detected not in self._IOS_PLAYABLE_AUDIO:
                    transcoded = self._transcode_to_mp3(candidate)
                    if transcoded is not None:
                        logger.info(
                            "mobile.media.lookup {}",
                            {
                                "instance_id": instance_id,
                                "sender_id": sender_id,
                                "file": file_name,
                                "resolved_path": str(transcoded),
                                "content_type": "audio/mpeg",
                                "transcoded": True,
                            },
                        )
                        return transcoded, "audio/mpeg"
                logger.info(
                    "mobile.media.lookup {}",
                    {
                        "instance_id": instance_id,
                        "sender_id": sender_id,
                        "file": file_name,
                        "resolved_path": str(candidate),
                        "content_type": content_type,
                        "transcoded": False,
                    },
                )
                return candidate, content_type
        if safe_sender == "rtsp":
            snapshot_path = self._ensure_rtsp_snapshot_file(target=target, file_name=safe_name)
            logger.info(
                "mobile.media.lookup.rtsp {}",
                {
                    "instance_id": instance_id,
                    "file": file_name,
                    "resolved_path": str(snapshot_path),
                    "content_type": "image/jpeg",
                },
            )
            return snapshot_path, "image/jpeg"
        logger.warning(
            "mobile.media.lookup.miss {}",
            {
                "instance_id": instance_id,
                "sender_id": sender_id,
                "file": file_name,
                "searched_roots": searched_roots,
            },
        )
        raise ValueError("Media file not found")

    def register_mobile_client(
        self,
        instance_id: str,
        device_id: str,
        pairing_token: str | None = None,
        label: str = "",
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Register a scanned mobile device as a pending access request."""
        if not pairing_token:
            raise PermissionError("Pairing token is required")
        if not self.auth_store.validate_and_consume_pairing_token(instance_id, pairing_token):
            raise PermissionError("Invalid or expired pairing token")

        target = next((t for t in self._load_targets() if t.id == instance_id), None)
        if not target:
            raise ValueError(f"Instance '{instance_id}' not found")
        self._require_target_access(target, accessible_instance_ids)
        config = self._load_target_config(target)
        device_token = f"mobtok-{secrets.token_urlsafe(24)}"

        if not config.channels.softnix_app.enabled:
            config.channels.softnix_app.enabled = True

        self.auth_store.upsert_mobile_device(instance_id, device_id, label or device_id, device_token=device_token)
        store = AccessRequestStore(target.workspace_path)
        pending = store.record(
            channel="softnix_app",
            sender_id=device_id,
            chat_id=f"mobile-{device_id}",
            content=f"Pair request from {label or device_id}",
            metadata={"username": label or device_id},
        )
        already_allowed = device_id in config.channels.softnix_app.allow_from
        self.auth_store.append_audit(
            event_type="channel.mobile_device_registered",
            category="configuration",
            outcome="success",
            resource={"type": "instance", "id": instance_id},
            payload={
                "device_id": device_id,
                "label": label,
                "pending_request": True,
                "already_allowed": already_allowed,
            },
        )
        return {
            "status": "pending_approval",
            "new": not already_allowed,
            "pending_request": pending,
            "already_allowed": already_allowed,
            "device_token": device_token,
        }

    # ── ngrok tunnel management ──────────────────────────────────────────

    _ngrok_process: subprocess.Popen | None = None  # class-level singleton

    @staticmethod
    def _query_ngrok_api() -> str | None:
        """Query ngrok local API and return the first HTTPS public URL, or None."""
        try:
            with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=2) as resp:
                data = json.loads(resp.read())
            tunnels = data.get("tunnels") or []
            for t in tunnels:
                if t.get("proto") == "https":
                    return str(t["public_url"])
            if tunnels:
                return str(tunnels[0].get("public_url") or "")
        except Exception:
            pass
        return None

    def start_ngrok(self, port: int) -> dict[str, Any]:
        """Start ngrok http tunnel for *port* and return the public URL.

        Kills any existing ngrok process first, then polls the ngrok local API
        for up to 10 seconds waiting for the tunnel to come up.
        """
        # Kill existing tracked process
        if AdminService._ngrok_process is not None:
            try:
                AdminService._ngrok_process.terminate()
            except Exception:
                pass
            AdminService._ngrok_process = None

        # Kill any stale ngrok processes on the machine (best-effort)
        try:
            subprocess.run(["pkill", "-f", "ngrok http"], capture_output=True, timeout=5, check=False)
        except Exception:
            pass
        time.sleep(0.3)

        # Launch ngrok
        try:
            AdminService._ngrok_process = subprocess.Popen(
                ["ngrok", "http", str(port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise ValueError(
                "ngrok is not installed or not in PATH. "
                "Install it from https://ngrok.com/download and run 'ngrok config add-authtoken <token>'."
            ) from exc

        # Poll up to 10 seconds for the tunnel URL
        url: str | None = None
        for _ in range(20):
            time.sleep(0.5)
            url = self._query_ngrok_api()
            if url:
                break

        if not url:
            raise ValueError(
                "ngrok process started but did not expose a tunnel within 10 seconds. "
                "Check that you have authenticated ngrok (ngrok config add-authtoken <token>)."
            )
        logger.info(f"ngrok tunnel started: {url} → localhost:{port}")
        return {"active": True, "url": url, "port": port}

    def get_ngrok_status(self) -> dict[str, Any]:
        """Return current ngrok tunnel status without starting anything."""
        url = self._query_ngrok_api()
        if url:
            return {"active": True, "url": url}
        return {"active": False, "url": None}

    # ── end ngrok ────────────────────────────────────────────────────────

    def list_mobile_devices(
        self,
        instance_id: str,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """List registered mobile devices for an instance."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        return self.auth_store.list_mobile_devices(instance_id)

    def get_mobile_device_status(
        self,
        instance_id: str,
        device_id: str,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Return allowlist approval status for one mobile device."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        device = self.auth_store.get_mobile_device(instance_id, device_id)
        if device is None:
            return {
                "registered": False,
                "device_id": device_id,
                "instance_id": instance_id,
                "status": "missing",
                "already_allowed": False,
            }
        config = self._load_target_config(target)
        already_allowed = device_id in list(config.channels.softnix_app.allow_from or [])
        return {
            "registered": True,
            "device_id": device_id,
            "instance_id": instance_id,
            "status": "approved" if already_allowed else "pending_approval",
            "already_allowed": already_allowed,
        }

    def _sync_relay_allow_from(self, target: InstanceTarget, config: Config) -> None:
        """Write softnix_app allow_from to relay/allow_from.json for zero-restart channel reload.

        The running SoftnixAppChannel re-reads this file on every poll cycle so newly
        registered or deleted devices take effect immediately without an agent restart.
        """
        try:
            relay_dir = target.workspace_path / "mobile_relay"
            relay_dir.mkdir(parents=True, exist_ok=True)
            allow_from = list(config.channels.softnix_app.allow_from or [])
            (relay_dir / "allow_from.json").write_text(
                json.dumps({"allow_from": allow_from}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"Failed to sync relay allow_from for instance '{target.id}': {exc}")

    def delete_mobile_device(
        self,
        instance_id: str,
        device_id: str,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Remove a mobile device from auth store and config allow_from."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        revoked_web_sessions = self.auth_store.revoke_web_chat_sessions_for_device(
            instance_id=instance_id,
            device_id=device_id,
        )
        self.auth_store.delete_mobile_device(instance_id, device_id)
        restart_result: dict[str, Any] | None = None
        config = self._load_target_config(target)
        if device_id in config.channels.softnix_app.allow_from:
            config.channels.softnix_app.allow_from.remove(device_id)
            save_config(config, target.config_path)
        # Sync relay allow_from file so the running channel stops accepting deleted device
        self._sync_relay_allow_from(target, config)
        if target.source == "registry" and target.lifecycle and self._lifecycle_command(target, "restart"):
            restart_result = self.execute_instance_action(instance_id=instance_id, action="restart", accessible_instance_ids=accessible_instance_ids)
        self.auth_store.append_audit(
            event_type="channel.mobile_device_deleted",
            category="configuration",
            outcome="success",
            resource={"type": "instance", "id": instance_id},
            payload={
                "device_id": device_id,
                "revoked_web_sessions": revoked_web_sessions,
                "instance_restarted": bool(restart_result and restart_result.get("ok")),
                "restart_returncode": restart_result.get("returncode") if restart_result else None,
            },
        )
        return {
            "status": "deleted",
            "revoked_web_sessions": revoked_web_sessions,
            "instance_restart": {
                "attempted": restart_result is not None,
                **(restart_result or {}),
            },
        }

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

    def _global_policy_store(self) -> GlobalControlPolicyStore:
        return GlobalControlPolicyStore(self.auth_store.security_dir / "content-intent-policy.json")

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

    def _audit_event_instance_ids(self, record: dict[str, Any]) -> set[str]:
        ids: set[str] = set()

        def add_value(value: Any) -> None:
            if value is None:
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    add_value(item)
                return
            text = str(value or "").strip()
            if text:
                ids.add(text)

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                resource_type = str(value.get("type") or "").strip().lower()
                if resource_type == "instance":
                    add_value(value.get("id"))
                    add_value(value.get("instance_id"))
                for key, child in value.items():
                    if key in {
                        "instance_id",
                        "instance_ids",
                        "target_instance_id",
                        "target_instance_ids",
                        "source_instance_id",
                        "source_instance_ids",
                    }:
                        add_value(child)
                    if isinstance(child, (dict, list, tuple, set)):
                        walk(child)
            elif isinstance(value, (list, tuple, set)):
                for item in value:
                    walk(item)

        walk(record)
        return ids

    def _is_auth_audit_record_visible(
        self,
        record: dict[str, Any],
        *,
        current_user_id: str | None,
        accessible_instance_ids: set[str] | None,
        scope: str,
    ) -> bool:
        normalized_scope = str(scope or "accessible").strip().lower() or "accessible"
        if normalized_scope == "all" and accessible_instance_ids is None:
            return True
        actor = record.get("actor") if isinstance(record.get("actor"), dict) else {}
        instance_ids = self._audit_event_instance_ids(record)
        if normalized_scope == "mine":
            return bool(current_user_id and str(actor.get("user_id") or "").strip() == current_user_id)
        if normalized_scope == "instances":
            if accessible_instance_ids is None:
                return bool(instance_ids)
            return bool(instance_ids.intersection(accessible_instance_ids))
        if accessible_instance_ids is None:
            return True
        return bool(instance_ids.intersection(accessible_instance_ids))

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
        scope: str = "accessible",
        current_user_id: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Read and filter the auth audit log for the Security Audit viewer."""
        normalized_scope = self._normalize_accessible_instance_ids(accessible_instance_ids)
        normalized_current_user_id = str(current_user_id or "").strip() or None
        audit_path = self.auth_store.audit_path
        events: list[dict[str, Any]] = []
        if audit_path.exists():
            try:
                raw_lines = [line for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            except Exception:
                raw_lines = []
            for raw in raw_lines:
                try:
                    record = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(record, dict):
                    continue
                events.append(record)

        events.extend(self._collect_policy_security_audit_events())
        filtered_events: list[dict[str, Any]] = []
        for record in events:
            if not self._is_auth_audit_record_visible(
                record,
                current_user_id=normalized_current_user_id,
                accessible_instance_ids=normalized_scope,
                scope=scope,
            ):
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
            filtered_events.append(record)

        filtered_events.sort(key=lambda e: str(e.get("ts") or ""), reverse=True)
        total = len(filtered_events)
        page = filtered_events[offset : offset + limit]
        return {"events": page, "total": total, "offset": offset, "limit": limit}

    def _collect_policy_security_audit_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for event in self._collect_policy_runtime_events():
            action = str(event.get("policy_action") or event.get("action") or "allow")
            outcome = "denied" if action in {"block", "escalate"} else "success"
            events.append(
                {
                    "ts": str(event.get("ts") or ""),
                    "event_type": f"security.policy_enforcement_{action}",
                    "category": "policy_runtime",
                    "outcome": outcome,
                    "actor": {
                        "user_id": None,
                        "username": "runtime",
                        "role": "system",
                    },
                    "resource": {
                        "type": "instance",
                        "id": event.get("instance_id") or "",
                        "name": event.get("instance_name") or "",
                    },
                    "detail": {
                        "scope": event.get("scope") or "",
                        "rule_ids": event.get("rule_ids") or [],
                        "action": action,
                        "policy_version": event.get("policy_version"),
                        "channel": event.get("channel") or "",
                        "session_key": event.get("session_key") or "",
                    },
                }
            )
        return events

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
                "instance_ids": None,
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

    def list_admin_users(
        self,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        scope = self._normalize_accessible_instance_ids(accessible_instance_ids)
        users = []
        for user in self.auth_store.list_users(include_disabled=True):
            user_scope = self._instance_scope_for_user(user)
            if scope is not None:
                if not user_scope or not user_scope.intersection(scope):
                    continue
            users.append(sanitize_user(user))
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
        instance_ids: list[str] | None = None,
        allowed_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        normalized_username = normalize_username(username)
        if not normalized_username:
            raise ValueError("Username is required")
        if self.auth_store.get_user_by_username(normalized_username) is not None:
            raise ValueError(f"Username '{normalized_username}' already exists")
        normalized_email = normalize_email(email)
        if normalized_email and self.auth_store.get_user_by_email(normalized_email) is not None:
            raise ValueError(f"Email '{normalized_email}' already exists")
        normalized_instance_ids = self._validate_instance_selection(instance_ids, accessible_instance_ids=allowed_instance_ids)
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
                "instance_ids": normalized_instance_ids,
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
        instance_ids: Any = ...,
        allowed_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
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
        if instance_ids is not ...:
            normalized_instance_ids = self._validate_instance_selection(instance_ids, accessible_instance_ids=allowed_instance_ids)
            if normalized_instance_ids is None:
                updated.pop("instance_ids", None)
            else:
                updated["instance_ids"] = normalized_instance_ids
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

    def get_overview(
        self,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        instances = self.list_instances(accessible_instance_ids=accessible_instance_ids)
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

    def list_instances(
        self,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        scope = self._normalize_accessible_instance_ids(accessible_instance_ids)
        targets = self._load_targets()
        if scope is not None:
            targets = [target for target in targets if target.id in scope]
        return [self._collect_instance(target) for target in targets]

    def get_instance(
        self,
        instance_id: str,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any] | None:
        for target in self._load_targets():
            if target.id == instance_id:
                self._require_target_access(target, accessible_instance_ids)
                return self._collect_instance(target)
        return None

    def list_channels(
        self,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for instance in self.list_instances(accessible_instance_ids=accessible_instance_ids):
            for channel in instance["channels"]:
                rows.append(
                    {
                        "instance_id": instance["id"],
                        "instance_name": instance["name"],
                        **channel,
                    }
                )
        return rows

    def list_access_requests(
        self,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        requests: list[dict[str, Any]] = []
        scope = self._normalize_accessible_instance_ids(accessible_instance_ids)
        for target in self._load_targets():
            if scope is not None and target.id not in scope:
                continue
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

    def list_providers(
        self,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        instances = self.list_instances(accessible_instance_ids=accessible_instance_ids)
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

    def list_mcp_servers(
        self,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        instances = self.list_instances(accessible_instance_ids=accessible_instance_ids)
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

    def list_connector_presets(self) -> dict[str, Any]:
        """List built-in connector presets available for installation."""
        return {
            "presets": [
                {
                    "name": preset.name,
                    "display_name": preset.display_name,
                    "description": preset.description,
                    "skill_name": preset.skill_name,
                    "server_name": preset.server_name,
                }
                for preset in list_built_in_connector_presets()
            ]
        }

    def set_connector_enabled(
        self,
        *,
        instance_id: str,
        connector_name: str,
        enabled: bool,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Enable or disable one built-in connector without deleting its saved config."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        preset = get_connector_preset(connector_name)
        config = self._load_target_config(target)
        server = config.tools.mcp_servers.get(preset.server_name)
        if server is None:
            raise ValueError(f"Connector '{preset.display_name}' is not installed")
        server.enabled = bool(enabled)
        runtime = self._probe_instance_runtime(target)
        server.restart_required = str(runtime.get("status") or "").lower() == "running"
        save_config(config, target.config_path)
        self._append_audit_event(
            instance_id=target.id,
            event_type="connector.enabled" if enabled else "connector.disabled",
            payload={
                "connector": preset.name,
                "server_name": preset.server_name,
                "enabled": bool(enabled),
                "restart_required": bool(server.restart_required),
            },
        )
        return {
            "instance": self._collect_instance(target),
            "connector": preset.name,
            "server_name": preset.server_name,
            "enabled": bool(enabled),
            "restart_required": bool(server.restart_required),
        }

    def install_github_connector(
        self,
        *,
        instance_id: str,
        token: str,
        default_repo: str | None = None,
        api_base: str | None = None,
        server_name: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Install the built-in GitHub connector preset into one instance."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        preset = get_connector_preset(GITHUB_CONNECTOR_PRESET.name)
        normalized_server_name = str(server_name or preset.server_name).strip() or preset.server_name
        config = self._load_target_config(target)
        existing_server = config.tools.mcp_servers.get(normalized_server_name)
        existing_env = existing_server.env if existing_server is not None else {}
        existing_enabled = existing_server.enabled if existing_server is not None else True
        token_value = str(token or "").strip() or str(existing_env.get("GITHUB_TOKEN") or "").strip()
        default_repo_value = str(default_repo or "").strip() or str(existing_env.get("GITHUB_DEFAULT_REPO") or "").strip() or None
        api_base_value = str(api_base or "").strip() or str(existing_env.get("GITHUB_API_BASE") or "").strip() or None
        if not token_value:
            raise ValueError("GitHub token is required")
        runtime_script = _ensure_github_connector_runtime_script(target)
        config.tools.mcp_servers[normalized_server_name] = MCPServerConfig.model_validate(
            build_github_stdio_server_config(
                token=token_value,
                default_repo=default_repo_value,
                api_base=api_base_value,
                script_path=str(runtime_script),
            )
        )
        config.tools.mcp_servers[normalized_server_name].enabled = existing_enabled
        config.tools.mcp_servers[normalized_server_name].connector_status = "pending"
        save_config(config, target.config_path)
        workspace_path = target.workspace_path
        if workspace_path.exists():
            sync_workspace_templates(
                workspace_path,
                silent=True,
                agent_name=target.name,
                apply_identity=True,
            )
        self._append_audit_event(
            instance_id=target.id,
            event_type="connector.installed",
            payload={
                "connector": preset.name,
                "server_name": normalized_server_name,
                "skill_name": preset.skill_name,
            },
        )
        return {
            "instance": self._collect_instance(target),
            "connector": preset.name,
            "server_name": normalized_server_name,
            "skill_name": preset.skill_name,
        }

    def validate_github_connector(
        self,
        *,
        instance_id: str,
        token: str,
        default_repo: str | None = None,
        api_base: str | None = None,
        server_name: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Validate the GitHub connector credential and optional repository scope."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        preset_server_name = str(server_name or GITHUB_CONNECTOR_PRESET.server_name).strip() or GITHUB_CONNECTOR_PRESET.server_name
        token_value = str(token or "").strip()
        config_defaults: dict[str, Any] = {}
        if not token_value:
            config = self._load_target_config(target)
            server_config = config.tools.mcp_servers.get(preset_server_name)
            if server_config is not None:
                config_defaults = server_config.env or {}
                token_value = str(config_defaults.get("GITHUB_TOKEN") or "").strip()
                if not default_repo:
                    default_repo = str(config_defaults.get("GITHUB_DEFAULT_REPO") or "").strip() or None
                if not api_base:
                    api_base = str(config_defaults.get("GITHUB_API_BASE") or "").strip() or None
        if not token_value:
            raise ValueError("GitHub token is required")

        client = GitHubClient(
            token=token_value,
            api_base=str(api_base or "https://api.github.com").strip() or "https://api.github.com",
            default_repo=default_repo,
        )
        findings: list[dict[str, Any]] = []
        try:
            user = client.whoami()
            findings.append(
                {
                    "severity": "info",
                    "code": "token_valid",
                    "detail": f"Authenticated as {user.get('login') or user.get('name') or 'GitHub user'}.",
                }
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                findings.append(
                    {
                        "severity": "error",
                        "code": "invalid_token",
                        "detail": "GitHub token was rejected by the API.",
                    }
                )
            else:
                findings.append(
                    {
                        "severity": "error",
                        "code": "github_api_error",
                        "detail": f"GitHub validation failed with HTTP {status}.",
                    }
                )
        except Exception as exc:
            findings.append(
                {
                    "severity": "error",
                    "code": "github_api_unreachable",
                    "detail": str(exc),
                }
            )

        if default_repo and not any(item["severity"] == "error" for item in findings):
            try:
                repo = client.get_repository(default_repo)
                findings.append(
                    {
                        "severity": "info",
                        "code": "repository_visible",
                        "detail": f"Repository {repo.get('full_name') or default_repo} is reachable.",
                    }
                )
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                findings.append(
                    {
                        "severity": "warning" if status == 404 else "error",
                        "code": "repository_unavailable" if status == 404 else "repository_probe_failed",
                        "detail": f"Repository probe returned HTTP {status}.",
                    }
                )
            except Exception as exc:
                findings.append(
                    {
                        "severity": "error",
                        "code": "repository_probe_failed",
                        "detail": str(exc),
                    }
                )

        status = "ok"
        if any(item["severity"] == "error" for item in findings):
            status = "error"
        elif any(item["severity"] == "warning" for item in findings):
            status = "warning"

        response = {
            "instance_id": target.id,
            "connector": GITHUB_CONNECTOR_PRESET.name,
            "status": status,
            "findings": findings,
        }
        config = self._load_target_config(target)
        server_config = config.tools.mcp_servers.get(preset_server_name)
        if server_config is not None:
            runtime_script = _ensure_github_connector_runtime_script(target)
            server_config.command = "python3"
            server_config.args = [str(runtime_script)]
            server_config.connector_status = "connected" if status == "ok" else "error"
            save_config(config, target.config_path)
        self._append_audit_event(
            instance_id=target.id,
            event_type="connector.validated",
            payload={
                "connector": GITHUB_CONNECTOR_PRESET.name,
                "status": status,
            },
        )
        return response

    def install_notion_connector(
        self,
        *,
        instance_id: str,
        token: str,
        default_page_id: str | None = None,
        api_base: str | None = None,
        notion_version: str | None = None,
        server_name: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Install the built-in Notion connector preset into one instance."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        preset = get_connector_preset(NOTION_CONNECTOR_PRESET.name)
        normalized_server_name = str(server_name or preset.server_name).strip() or preset.server_name
        config = self._load_target_config(target)
        existing_server = config.tools.mcp_servers.get(normalized_server_name)
        existing_env = existing_server.env if existing_server is not None else {}
        existing_enabled = existing_server.enabled if existing_server is not None else True
        token_value = str(token or "").strip() or str(existing_env.get("NOTION_TOKEN") or "").strip()
        default_page_value = normalize_notion_target_id(default_page_id) or normalize_notion_target_id(existing_env.get("NOTION_DEFAULT_PAGE_ID")) or None
        api_base_value = str(api_base or "").strip() or str(existing_env.get("NOTION_API_BASE") or "").strip() or NOTION_API_BASE_DEFAULT
        notion_version_value = str(notion_version or "").strip() or str(existing_env.get("NOTION_VERSION") or "").strip() or None
        if not token_value:
            raise ValueError("Notion token is required")
        runtime_script = _ensure_notion_connector_runtime_script(target)
        config.tools.mcp_servers[normalized_server_name] = MCPServerConfig.model_validate(
            build_notion_stdio_server_config(
                token=token_value,
                default_page_id=default_page_value,
                api_base=api_base_value,
                notion_version=notion_version_value,
                script_path=str(runtime_script),
            )
        )
        config.tools.mcp_servers[normalized_server_name].enabled = existing_enabled
        config.tools.mcp_servers[normalized_server_name].connector_status = "pending"
        save_config(config, target.config_path)
        workspace_path = target.workspace_path
        if workspace_path.exists():
            sync_workspace_templates(
                workspace_path,
                silent=True,
                agent_name=target.name,
                apply_identity=True,
            )
        self._append_audit_event(
            instance_id=target.id,
            event_type="connector.installed",
            payload={
                "connector": preset.name,
                "server_name": normalized_server_name,
                "skill_name": preset.skill_name,
            },
        )
        return {
            "instance": self._collect_instance(target),
            "connector": preset.name,
            "server_name": normalized_server_name,
            "skill_name": preset.skill_name,
        }

    def validate_notion_connector(
        self,
        *,
        instance_id: str,
        token: str,
        default_page_id: str | None = None,
        api_base: str | None = None,
        notion_version: str | None = None,
        server_name: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Validate the Notion connector credential and optional page scope."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        preset_server_name = str(server_name or NOTION_CONNECTOR_PRESET.server_name).strip() or NOTION_CONNECTOR_PRESET.server_name
        token_value = str(token or "").strip()
        config_defaults: dict[str, Any] = {}
        if not token_value:
            config = self._load_target_config(target)
            server_config = config.tools.mcp_servers.get(preset_server_name)
            if server_config is not None:
                config_defaults = server_config.env or {}
                token_value = str(config_defaults.get("NOTION_TOKEN") or "").strip()
                if not default_page_id:
                    default_page_id = normalize_notion_target_id(config_defaults.get("NOTION_DEFAULT_PAGE_ID")) or None
                if not api_base:
                    api_base = str(config_defaults.get("NOTION_API_BASE") or "").strip() or NOTION_API_BASE_DEFAULT
                if not notion_version:
                    notion_version = str(config_defaults.get("NOTION_VERSION") or "").strip() or None
        if not token_value:
            raise ValueError("Notion token is required")
        default_page_id = normalize_notion_target_id(default_page_id) or None

        client = NotionClient(
            token=token_value,
            api_base=str(api_base or NOTION_API_BASE_DEFAULT).strip() or NOTION_API_BASE_DEFAULT,
            notion_version=str(notion_version or "2026-03-11").strip() or "2026-03-11",
            default_page_id=normalize_notion_target_id(default_page_id),
        )
        findings: list[dict[str, Any]] = []
        try:
            user = client.whoami()
            owner = user.get("name") or user.get("bot", {}).get("owner", {}).get("type") or "Notion integration"
            findings.append(
                {
                    "severity": "info",
                    "code": "token_valid",
                    "detail": f"Authenticated as {owner}.",
                }
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403}:
                findings.append(
                    {
                        "severity": "error",
                        "code": "invalid_token",
                        "detail": "Notion token was rejected by the API.",
                    }
                )
            else:
                findings.append(
                    {
                        "severity": "error",
                        "code": "notion_api_error",
                        "detail": f"Notion validation failed with HTTP {status_code}.",
                    }
                )
        except Exception as exc:
            findings.append(
                {
                    "severity": "error",
                    "code": "notion_api_unreachable",
                    "detail": str(exc),
                }
            )

        if default_page_id and not any(item["severity"] == "error" for item in findings):
            probe_error: str | None = None
            for label, probe in (
                ("page_visible", lambda: client.get_page(default_page_id)),
                ("data_source_visible", lambda: client.get_data_source(default_page_id)),
                ("database_visible", lambda: client.get_database(default_page_id)),
            ):
                try:
                    probe_target = probe()
                    findings.append(
                        {
                            "severity": "info",
                            "code": label,
                            "detail": f"{label.replace('_', ' ').title()} {probe_target.get('id') or default_page_id} is reachable.",
                        }
                    )
                    probe_error = None
                    break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 404:
                        findings.append(
                            {
                                "severity": "error",
                                "code": f"{label.replace('_visible', '')}_probe_failed",
                                "detail": f"Target probe returned HTTP {exc.response.status_code}.",
                            }
                        )
                        probe_error = str(exc)
                        break
                    probe_error = "404"
                except Exception as exc:
                    findings.append(
                        {
                            "severity": "error",
                            "code": f"{label.replace('_visible', '')}_probe_failed",
                            "detail": str(exc),
                        }
                    )
                    probe_error = str(exc)
                    break
            if probe_error == "404":
                findings.append(
                    {
                        "severity": "warning",
                        "code": "notion_target_unavailable",
                        "detail": "Notion target was not found as a page, data source, or database. If this ID is valid, confirm it is shared with the integration.",
                    }
                )

        status = "ok"
        if any(item["severity"] == "error" for item in findings):
            status = "error"
        elif any(item["severity"] == "warning" for item in findings):
            status = "warning"

        response = {
            "instance_id": target.id,
            "connector": NOTION_CONNECTOR_PRESET.name,
            "status": status,
            "findings": findings,
        }
        config = self._load_target_config(target)
        server_config = config.tools.mcp_servers.get(preset_server_name)
        if server_config is not None:
            runtime_script = _ensure_notion_connector_runtime_script(target)
            server_config.command = "python3"
            server_config.args = [str(runtime_script)]
            if default_page_id:
                server_config.env["NOTION_DEFAULT_PAGE_ID"] = default_page_id
            server_config.connector_status = "connected" if status == "ok" else "error"
            save_config(config, target.config_path)
        self._append_audit_event(
            instance_id=target.id,
            event_type="connector.validated",
            payload={
                "connector": NOTION_CONNECTOR_PRESET.name,
                "status": status,
            },
        )
        return response

    def install_gmail_connector(
        self,
        *,
        instance_id: str,
        token: str,
        user_id: str | None = None,
        api_base: str | None = None,
        refresh_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_uri: str | None = None,
        server_name: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Install the built-in Gmail connector preset into one instance."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        preset = get_connector_preset(GMAIL_CONNECTOR_PRESET.name)
        normalized_server_name = str(server_name or preset.server_name).strip() or preset.server_name
        config = self._load_target_config(target)
        existing_server = config.tools.mcp_servers.get(normalized_server_name)
        existing_env = existing_server.env if existing_server is not None else {}
        existing_enabled = existing_server.enabled if existing_server is not None else True
        token_value = str(token or "").strip() or str(existing_env.get("GMAIL_TOKEN") or "").strip()
        user_id_value = str(user_id or "").strip() or str(existing_env.get("GMAIL_USER_ID") or "").strip() or "me"
        api_base_value = str(api_base or "").strip() or str(existing_env.get("GMAIL_API_BASE") or "").strip() or GMAIL_API_BASE_DEFAULT
        refresh_token_value = str(refresh_token or "").strip() or str(existing_env.get("GMAIL_REFRESH_TOKEN") or "").strip()
        client_id_value = str(client_id or "").strip() or str(existing_env.get("GMAIL_CLIENT_ID") or "").strip()
        client_secret_value = str(client_secret or "").strip() or str(existing_env.get("GMAIL_CLIENT_SECRET") or "").strip()
        token_uri_value = str(token_uri or "").strip() or str(existing_env.get("GMAIL_TOKEN_URI") or "").strip() or "https://oauth2.googleapis.com/token"
        if not token_value:
            raise ValueError("Gmail access token is required")
        runtime_script = _ensure_gmail_connector_runtime_script(target)
        config.tools.mcp_servers[normalized_server_name] = MCPServerConfig.model_validate(
            build_gmail_stdio_server_config(
                token=token_value,
                user_id=user_id_value,
                api_base=api_base_value,
                refresh_token=refresh_token_value or None,
                client_id=client_id_value or None,
                client_secret=client_secret_value or None,
                token_uri=token_uri_value,
                script_path=str(runtime_script),
            )
        )
        config.tools.mcp_servers[normalized_server_name].enabled = existing_enabled
        config.tools.mcp_servers[normalized_server_name].connector_status = "pending"
        save_config(config, target.config_path)
        workspace_path = target.workspace_path
        if workspace_path.exists():
            sync_workspace_templates(
                workspace_path,
                silent=True,
                agent_name=target.name,
                apply_identity=True,
            )
        self._append_audit_event(
            instance_id=target.id,
            event_type="connector.installed",
            payload={
                "connector": preset.name,
                "server_name": normalized_server_name,
                "skill_name": preset.skill_name,
            },
        )
        return {
            "instance": self._collect_instance(target),
            "connector": preset.name,
            "server_name": normalized_server_name,
            "skill_name": preset.skill_name,
        }

    def validate_gmail_connector(
        self,
        *,
        instance_id: str,
        token: str,
        user_id: str | None = None,
        api_base: str | None = None,
        refresh_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_uri: str | None = None,
        server_name: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Validate the Gmail connector credential and optional mailbox scope."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        preset_server_name = str(server_name or GMAIL_CONNECTOR_PRESET.server_name).strip() or GMAIL_CONNECTOR_PRESET.server_name
        token_value = str(token or "").strip()
        if not token_value:
            config = self._load_target_config(target)
            server_config = config.tools.mcp_servers.get(preset_server_name)
            if server_config is not None:
                config_defaults = server_config.env or {}
                token_value = str(config_defaults.get("GMAIL_TOKEN") or "").strip()
                if not user_id:
                    user_id = str(config_defaults.get("GMAIL_USER_ID") or "").strip() or "me"
                if not api_base:
                    api_base = str(config_defaults.get("GMAIL_API_BASE") or "").strip() or GMAIL_API_BASE_DEFAULT
                if not refresh_token:
                    refresh_token = str(config_defaults.get("GMAIL_REFRESH_TOKEN") or "").strip()
                if not client_id:
                    client_id = str(config_defaults.get("GMAIL_CLIENT_ID") or "").strip()
                if not client_secret:
                    client_secret = str(config_defaults.get("GMAIL_CLIENT_SECRET") or "").strip()
                if not token_uri:
                    token_uri = str(config_defaults.get("GMAIL_TOKEN_URI") or "").strip() or "https://oauth2.googleapis.com/token"
        if not token_value:
            raise ValueError("Gmail access token is required")
        user_id_value = str(user_id or "me").strip() or "me"

        client = GmailClient(
            token=token_value,
            api_base=str(api_base or GMAIL_API_BASE_DEFAULT).strip() or GMAIL_API_BASE_DEFAULT,
            user_id=user_id_value,
            refresh_token=str(refresh_token or "").strip(),
            client_id=str(client_id or "").strip(),
            client_secret=str(client_secret or "").strip(),
            token_uri=str(token_uri or "https://oauth2.googleapis.com/token").strip() or "https://oauth2.googleapis.com/token",
        )
        findings: list[dict[str, Any]] = []
        try:
            profile = client.whoami()
            findings.append(
                {
                    "severity": "info",
                    "code": "token_valid",
                    "detail": f"Authenticated as {profile.get('emailAddress') or profile.get('displayName') or 'Gmail user'}.",
                }
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                detail = "Gmail access token was rejected by the API."
                if getattr(client, "has_refresh_credentials", None) and callable(client.has_refresh_credentials):
                    if client.has_refresh_credentials():
                        detail += " The connector has refresh credentials, so try importing a fresh token.json or re-running Google quickstart if the access token is revoked."
                    else:
                        detail += " Import a token.json that includes refresh_token and client_id so the connector can renew expired access tokens."
                findings.append(
                    {
                        "severity": "error",
                        "code": "invalid_token",
                        "detail": detail,
                    }
                )
            else:
                findings.append(
                    {
                        "severity": "error",
                        "code": "gmail_api_error",
                        "detail": f"Gmail validation failed with HTTP {status}.",
                    }
                )
        except Exception as exc:
            findings.append(
                {
                    "severity": "error",
                    "code": "gmail_api_unreachable",
                    "detail": str(exc),
                }
            )

        if not any(item["severity"] == "error" for item in findings):
            try:
                labels = client.list_labels()
                label_count = len([item for item in labels.get("labels", []) if isinstance(item, dict)])
                findings.append(
                    {
                        "severity": "info",
                        "code": "mailbox_visible",
                        "detail": f"Mailbox is reachable and returned {label_count} labels.",
                    }
                )
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                findings.append(
                    {
                        "severity": "warning" if status == 404 else "error",
                        "code": "mailbox_unavailable" if status == 404 else "mailbox_probe_failed",
                        "detail": f"Mailbox probe returned HTTP {status}.",
                    }
                )
            except Exception as exc:
                findings.append(
                    {
                        "severity": "error",
                        "code": "mailbox_probe_failed",
                        "detail": str(exc),
                    }
                )

        if not any(item["severity"] == "error" for item in findings):
            try:
                scopes = client.token_scopes()
                write_scopes = sorted(scope for scope in scopes if scope in GMAIL_WRITE_SCOPES)
                if write_scopes:
                    findings.append(
                        {
                            "severity": "info",
                            "code": "write_scope_ready",
                            "detail": f"Token includes Gmail write scope(s): {', '.join(write_scopes)}.",
                        }
                    )
                else:
                    findings.append(
                        {
                            "severity": "warning",
                            "code": "write_scope_missing",
                            "detail": "Token can read Gmail but cannot draft or send mail. Regenerate it with gmail.compose or gmail.send.",
                        }
                    )
            except Exception as exc:
                findings.append(
                    {
                        "severity": "warning",
                        "code": "scope_probe_failed",
                        "detail": str(exc),
                    }
                )

        status = "ok"
        if any(item["severity"] == "error" for item in findings):
            status = "error"
        elif any(item["severity"] == "warning" for item in findings):
            status = "warning"

        response = {
            "instance_id": target.id,
            "connector": GMAIL_CONNECTOR_PRESET.name,
            "status": status,
            "findings": findings,
        }
        config = self._load_target_config(target)
        server_config = config.tools.mcp_servers.get(preset_server_name)
        if server_config is not None:
            runtime_script = _ensure_gmail_connector_runtime_script(target)
            server_config.command = "python3"
            server_config.args = [str(runtime_script)]
            server_config.connector_status = "connected" if status == "ok" else "error"
            save_config(config, target.config_path)
        self._append_audit_event(
            instance_id=target.id,
            event_type="connector.validated",
            payload={
                "connector": GMAIL_CONNECTOR_PRESET.name,
                "status": status,
            },
        )
        return response

    def install_composio_connector(
        self,
        *,
        instance_id: str,
        api_key: str,
        url: str | None = None,
        server_name: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Install the built-in Composio connector preset into one instance."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        preset = get_connector_preset(COMPOSIO_CONNECTOR_PRESET.name)
        normalized_server_name = str(server_name or preset.server_name).strip() or preset.server_name
        config = self._load_target_config(target)
        existing_server = config.tools.mcp_servers.get(normalized_server_name)
        existing_headers = existing_server.headers if existing_server is not None else {}
        existing_enabled = existing_server.enabled if existing_server is not None else True
        api_key_value = str(api_key or "").strip() or _header_value_case_insensitive(existing_headers, COMPOSIO_API_KEY_HEADER_DEFAULT)
        url_value = str(url or "").strip() or str(getattr(existing_server, "url", "") or "").strip() or COMPOSIO_MCP_URL_DEFAULT
        if not api_key_value:
            raise ValueError("Composio API key is required")
        config.tools.mcp_servers[normalized_server_name] = MCPServerConfig.model_validate(
            build_composio_mcp_server_config(
                api_key=api_key_value,
                url=url_value,
            )
        )
        config.tools.mcp_servers[normalized_server_name].enabled = existing_enabled
        config.tools.mcp_servers[normalized_server_name].connector_status = "pending"
        save_config(config, target.config_path)
        workspace_path = target.workspace_path
        if workspace_path.exists():
            sync_workspace_templates(
                workspace_path,
                silent=True,
                agent_name=target.name,
                apply_identity=True,
            )
        self._append_audit_event(
            instance_id=target.id,
            event_type="connector.installed",
            payload={
                "connector": preset.name,
                "server_name": normalized_server_name,
                "skill_name": preset.skill_name,
            },
        )
        return {
            "instance": self._collect_instance(target),
            "connector": preset.name,
            "server_name": normalized_server_name,
            "skill_name": preset.skill_name,
        }

    def validate_composio_connector(
        self,
        *,
        instance_id: str,
        api_key: str,
        url: str | None = None,
        server_name: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Validate the Composio connector API key and remote MCP availability."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        preset_server_name = str(server_name or COMPOSIO_CONNECTOR_PRESET.server_name).strip() or COMPOSIO_CONNECTOR_PRESET.server_name
        api_key_value = str(api_key or "").strip()
        config = self._load_target_config(target)
        server_config = config.tools.mcp_servers.get(preset_server_name)
        if server_config is not None:
            if not api_key_value:
                api_key_value = _header_value_case_insensitive(server_config.headers, COMPOSIO_API_KEY_HEADER_DEFAULT)
            if not url:
                url = str(server_config.url or "").strip() or COMPOSIO_MCP_URL_DEFAULT
        if not api_key_value:
            raise ValueError("Composio API key is required")

        probe_config = MCPServerConfig.model_validate(
            build_composio_mcp_server_config(
                api_key=api_key_value,
                url=str(url or COMPOSIO_MCP_URL_DEFAULT).strip() or COMPOSIO_MCP_URL_DEFAULT,
            )
        )
        findings: list[dict[str, Any]] = []
        try:
            probe_result = asyncio.run(_probe_remote_mcp_server_async(probe_config))
            findings.append(
                {
                    "severity": "info",
                    "code": "api_key_valid",
                    "detail": f"Connected to Composio MCP and discovered {probe_result.get('tool_count') or 0} tools.",
                }
            )
            if int(probe_result.get("tool_count") or 0) <= 0:
                findings.append(
                    {
                        "severity": "warning",
                        "code": "no_tools_visible",
                        "detail": "Composio MCP server connected, but no tools were exposed.",
                    }
                )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403}:
                findings.append(
                    {
                        "severity": "error",
                        "code": "invalid_api_key",
                        "detail": "Composio API key was rejected by the MCP server.",
                    }
                )
            else:
                findings.append(
                    {
                        "severity": "error",
                        "code": "composio_api_error",
                        "detail": f"Composio validation failed with HTTP {status_code}.",
                    }
                )
        except Exception as exc:
            findings.append(
                {
                    "severity": "error",
                    "code": "composio_api_unreachable",
                    "detail": str(exc),
                }
            )

        status = "ok"
        if any(item["severity"] == "error" for item in findings):
            status = "error"
        elif any(item["severity"] == "warning" for item in findings):
            status = "warning"

        response = {
            "instance_id": target.id,
            "connector": COMPOSIO_CONNECTOR_PRESET.name,
            "status": status,
            "findings": findings,
        }
        config = self._load_target_config(target)
        server_config = config.tools.mcp_servers.get(preset_server_name)
        if server_config is not None:
            server_config.type = probe_config.type
            server_config.url = probe_config.url
            server_config.headers = dict(probe_config.headers)
            server_config.connector_status = "connected" if status == "ok" else "error"
            save_config(config, target.config_path)
        self._append_audit_event(
            instance_id=target.id,
            event_type="connector.validated",
            payload={
                "connector": COMPOSIO_CONNECTOR_PRESET.name,
                "status": status,
            },
        )
        return response

    def install_insightdoc_connector(
        self,
        *,
        instance_id: str,
        token: str,
        api_base_url: str | None = None,
        external_base_url: str | None = None,
        default_job_name: str | None = None,
        default_schema_id: str | None = None,
        default_integration_name: str | None = None,
        curl_insecure: bool | None = None,
        server_name: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Install the built-in InsightDOC connector preset into one instance."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        preset = get_connector_preset(INSIGHTDOC_CONNECTOR_PRESET.name)
        normalized_server_name = str(server_name or preset.server_name).strip() or preset.server_name
        config = self._load_target_config(target)
        existing_server = config.tools.mcp_servers.get(normalized_server_name)
        existing_env = existing_server.env if existing_server is not None else {}
        existing_enabled = existing_server.enabled if existing_server is not None else True
        token_value = str(token or "").strip() or str(existing_env.get("INSIGHTOCR_API_TOKEN") or "").strip()
        api_base_value = str(api_base_url or "").strip() or str(existing_env.get("INSIGHTOCR_API_BASE_URL") or "").strip() or INSIGHTDOC_API_BASE_DEFAULT
        external_base_value = str(external_base_url or "").strip() or str(existing_env.get("INSIGHTOCR_EXTERNAL_BASE_URL") or "").strip() or INSIGHTDOC_EXTERNAL_BASE_DEFAULT
        default_job_name_value = str(default_job_name or "").strip() or str(existing_env.get("INSIGHTOCR_DEFAULT_JOB_NAME") or "").strip()
        default_schema_id_value = str(default_schema_id or "").strip() or str(existing_env.get("INSIGHTOCR_DEFAULT_SCHEMA_ID") or "").strip()
        default_integration_name_value = str(default_integration_name or "").strip() or str(existing_env.get("INSIGHTOCR_DEFAULT_INTEGRATION_NAME") or "").strip()
        curl_insecure_value = curl_insecure if curl_insecure is not None else str(existing_env.get("CURL_INSECURE") or "").strip().lower() in {"1", "true", "yes", "on"}
        if not token_value:
            raise ValueError("InsightDOC API token is required")
        runtime_script = _ensure_insightdoc_connector_runtime_script(target)
        config.tools.mcp_servers[normalized_server_name] = MCPServerConfig.model_validate(
            build_insightdoc_stdio_server_config(
                token=token_value,
                api_base_url=api_base_value,
                external_base_url=external_base_value,
                default_job_name=default_job_name_value or None,
                default_schema_id=default_schema_id_value or None,
                default_integration_name=default_integration_name_value or None,
                curl_insecure=curl_insecure_value,
                script_path=str(runtime_script),
            )
        )
        config.tools.mcp_servers[normalized_server_name].enabled = existing_enabled
        config.tools.mcp_servers[normalized_server_name].connector_status = "pending"
        save_config(config, target.config_path)
        workspace_path = target.workspace_path
        if workspace_path.exists():
            sync_workspace_templates(
                workspace_path,
                silent=True,
                agent_name=target.name,
                apply_identity=True,
            )
        self._append_audit_event(
            instance_id=target.id,
            event_type="connector.installed",
            payload={
                "connector": preset.name,
                "server_name": normalized_server_name,
                "skill_name": preset.skill_name,
            },
        )
        return {
            "instance": self._collect_instance(target),
            "connector": preset.name,
            "server_name": normalized_server_name,
            "skill_name": preset.skill_name,
        }

    def validate_insightdoc_connector(
        self,
        *,
        instance_id: str,
        token: str,
        api_base_url: str | None = None,
        external_base_url: str | None = None,
        default_job_name: str | None = None,
        default_schema_id: str | None = None,
        default_integration_name: str | None = None,
        curl_insecure: bool | None = None,
        server_name: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Validate the InsightDOC connector credential and optional defaults."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        preset_server_name = str(server_name or INSIGHTDOC_CONNECTOR_PRESET.server_name).strip() or INSIGHTDOC_CONNECTOR_PRESET.server_name
        token_value = str(token or "").strip()
        if not token_value:
            config = self._load_target_config(target)
            server_config = config.tools.mcp_servers.get(preset_server_name)
            if server_config is not None:
                config_defaults = server_config.env or {}
                token_value = str(config_defaults.get("INSIGHTOCR_API_TOKEN") or "").strip()
                if not api_base_url:
                    api_base_url = str(config_defaults.get("INSIGHTOCR_API_BASE_URL") or "").strip() or INSIGHTDOC_API_BASE_DEFAULT
                if not external_base_url:
                    external_base_url = str(config_defaults.get("INSIGHTOCR_EXTERNAL_BASE_URL") or "").strip() or INSIGHTDOC_EXTERNAL_BASE_DEFAULT
                if not default_job_name:
                    default_job_name = str(config_defaults.get("INSIGHTOCR_DEFAULT_JOB_NAME") or "").strip() or None
                if not default_schema_id:
                    default_schema_id = str(config_defaults.get("INSIGHTOCR_DEFAULT_SCHEMA_ID") or "").strip() or None
                if not default_integration_name:
                    default_integration_name = str(config_defaults.get("INSIGHTOCR_DEFAULT_INTEGRATION_NAME") or "").strip() or None
                if curl_insecure is None:
                    curl_insecure = str(config_defaults.get("CURL_INSECURE") or "").strip().lower() in {"1", "true", "yes", "on"}
        if not token_value:
            raise ValueError("InsightDOC API token is required")

        client = InsightDOCClient(
            token=token_value,
            api_base=str(api_base_url or INSIGHTDOC_API_BASE_DEFAULT).strip() or INSIGHTDOC_API_BASE_DEFAULT,
            external_base_url=str(external_base_url or INSIGHTDOC_EXTERNAL_BASE_DEFAULT).strip() or INSIGHTDOC_EXTERNAL_BASE_DEFAULT,
            default_job_name=str(default_job_name or "").strip(),
            default_schema_id=str(default_schema_id or "").strip(),
            default_integration_name=str(default_integration_name or "").strip(),
            curl_insecure=bool(curl_insecure),
        )
        findings: list[dict[str, Any]] = []
        jobs_payload: Any | None = None
        schemas_payload: Any | None = None
        integrations_payload: Any | None = None

        try:
            jobs_payload = client.list_jobs()
            job_count = len(_extract_list_items(jobs_payload, ("jobs", "items", "results")))
            findings.append(
                {
                    "severity": "info",
                    "code": "token_valid",
                    "detail": f"Authenticated and {job_count} job record(s) are reachable.",
                }
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                findings.append(
                    {
                        "severity": "error",
                        "code": "invalid_token",
                        "detail": "InsightDOC API token was rejected by the API.",
                    }
                )
            else:
                findings.append(
                    {
                        "severity": "error",
                        "code": "insightdoc_api_error",
                        "detail": f"InsightDOC validation failed with HTTP {status}.",
                    }
                )
        except Exception as exc:
            findings.append(
                {
                    "severity": "error",
                    "code": "insightdoc_api_unreachable",
                    "detail": str(exc),
                }
            )

        if not any(item["severity"] == "error" for item in findings):
            try:
                schemas_payload = client.list_schemas()
                schema_count = len(_extract_list_items(schemas_payload, ("schemas", "items", "results")))
                findings.append(
                    {
                        "severity": "info",
                        "code": "schemas_visible",
                        "detail": f"{schema_count} schema(s) are reachable.",
                    }
                )
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                findings.append(
                    {
                        "severity": "warning" if status == 404 else "error",
                        "code": "schemas_unavailable" if status == 404 else "schemas_probe_failed",
                        "detail": f"Schemas probe returned HTTP {status}.",
                    }
                )
            except Exception as exc:
                findings.append(
                    {
                        "severity": "error",
                        "code": "schemas_probe_failed",
                        "detail": str(exc),
                    }
                )

        if not any(item["severity"] == "error" for item in findings):
            try:
                integrations_payload = client.list_integrations()
                integration_count = len(_extract_list_items(integrations_payload, ("integrations", "items", "results")))
                findings.append(
                    {
                        "severity": "info",
                        "code": "integrations_visible",
                        "detail": f"{integration_count} integration(s) are reachable.",
                    }
                )
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                findings.append(
                    {
                        "severity": "warning" if status == 404 else "error",
                        "code": "integrations_unavailable" if status == 404 else "integrations_probe_failed",
                        "detail": f"Integrations probe returned HTTP {status}.",
                    }
                )
            except Exception as exc:
                findings.append(
                    {
                        "severity": "error",
                        "code": "integrations_probe_failed",
                        "detail": str(exc),
                    }
                )

        if default_schema_id and not any(item["severity"] == "error" for item in findings):
            schema_match = _find_named_item(
                schemas_payload,
                default_schema_id,
                list_keys=("schemas", "items", "results"),
                field_keys=("id", "name", "title", "code"),
            )
            if schema_match is not None:
                findings.append(
                    {
                        "severity": "info",
                        "code": "default_schema_visible",
                        "detail": f"Schema {default_schema_id} is reachable.",
                    }
                )
            else:
                findings.append(
                    {
                        "severity": "warning",
                        "code": "default_schema_unavailable",
                        "detail": f"Schema {default_schema_id} was not found in the schemas list. Confirm it is valid and accessible.",
                    }
                )

        if default_integration_name and not any(item["severity"] == "error" for item in findings):
            integration_match = _find_named_item(
                integrations_payload,
                default_integration_name,
                list_keys=("integrations", "items", "results"),
                field_keys=("id", "name", "title", "label"),
            )
            if integration_match is not None:
                findings.append(
                    {
                        "severity": "info",
                        "code": "default_integration_visible",
                        "detail": f"Integration {default_integration_name} is reachable.",
                    }
                )
            else:
                findings.append(
                    {
                        "severity": "warning",
                        "code": "default_integration_unavailable",
                        "detail": f"Integration {default_integration_name} was not found in the integrations list. Confirm it is valid and accessible.",
                    }
                )

        status = "ok"
        if any(item["severity"] == "error" for item in findings):
            status = "error"
        elif any(item["severity"] == "warning" for item in findings):
            status = "warning"

        response = {
            "instance_id": target.id,
            "connector": INSIGHTDOC_CONNECTOR_PRESET.name,
            "status": status,
            "findings": findings,
        }
        config = self._load_target_config(target)
        server_config = config.tools.mcp_servers.get(preset_server_name)
        if server_config is not None:
            runtime_script = _ensure_insightdoc_connector_runtime_script(target)
            server_config.command = "python3"
            server_config.args = [str(runtime_script)]
            if default_job_name:
                server_config.env["INSIGHTOCR_DEFAULT_JOB_NAME"] = default_job_name
            if default_schema_id:
                server_config.env["INSIGHTOCR_DEFAULT_SCHEMA_ID"] = default_schema_id
            if default_integration_name:
                server_config.env["INSIGHTOCR_DEFAULT_INTEGRATION_NAME"] = default_integration_name
            server_config.connector_status = "connected" if status == "ok" else "error"
            save_config(config, target.config_path)
        self._append_audit_event(
            instance_id=target.id,
            event_type="connector.validated",
            payload={
                "connector": INSIGHTDOC_CONNECTOR_PRESET.name,
                "status": status,
            },
        )
        return response

    def get_security(
        self,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        instances = self.list_instances(accessible_instance_ids=accessible_instance_ids)
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
            "global_policy": self.get_global_policy_summary(),
            "recent_policy_hits": self.get_global_policy_hits(limit=20, accessible_instance_ids=accessible_instance_ids)["events"],
            "detections_by_instance": self.get_global_policy_detections_by_instance(accessible_instance_ids=accessible_instance_ids)["instances"],
        }

    def get_global_policy(self) -> dict[str, Any]:
        store = self._global_policy_store()
        try:
            policy = store.load()
        except PolicyValidationError as exc:
            return {
                "policy": None,
                "summary": {
                    "path": str(store.path),
                    "exists": store.path.exists(),
                    "enabled": False,
                    "mode": "error",
                    "version": 0,
                    "updated_at": "",
                    "updated_by": {},
                    "rule_count": 0,
                    "enabled_rule_count": 0,
                    "error": "; ".join(exc.errors),
                },
                "errors": exc.errors,
                "warnings": exc.warnings,
            }
        return {
            "policy": policy,
            "summary": store.summarize(policy),
            "catalog": get_policy_catalog(),
        }

    def get_global_policy_summary(self) -> dict[str, Any]:
        store = self._global_policy_store()
        try:
            policy = store.load()
        except PolicyValidationError as exc:
            return {
                "path": str(store.path),
                "exists": store.path.exists(),
                "enabled": False,
                "mode": "error",
                "version": 0,
                "updated_at": "",
                "updated_by": {},
                "rule_count": 0,
                "enabled_rule_count": 0,
                "error": "; ".join(exc.errors),
            }
        return store.summarize(policy)

    def validate_global_policy(self, *, policy: dict[str, Any]) -> dict[str, Any]:
        store = self._global_policy_store()
        normalized, errors, warnings = store.validate(policy)
        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "policy": normalized,
            "summary": store.summarize(normalized) if not errors else None,
            "catalog": get_policy_catalog(),
        }

    def update_global_policy(self, *, policy: dict[str, Any]) -> dict[str, Any]:
        store = self._global_policy_store()
        actor = get_request_audit_context() or {}
        current = None
        if store.path.exists():
            try:
                current = store.load()
            except Exception:
                current = None
        try:
            saved = store.save(policy, actor=actor)
        except PolicyValidationError as exc:
            self.auth_store.append_audit(
                event_type="security.policy_global_validation_failed",
                category="policy_admin",
                outcome="failure",
                resource={"type": "global_policy", "id": "content-intent-policy"},
                payload={"errors": exc.errors, "warnings": exc.warnings},
            )
            raise

        current_enabled = bool((current or {}).get("enabled", True))
        if current is None:
            event_type = "security.policy_global_created"
        elif current_enabled != bool(saved.get("enabled", True)):
            event_type = "security.policy_global_enabled" if bool(saved.get("enabled", True)) else "security.policy_global_disabled"
        elif str((current or {}).get("mode") or "enforce") != str(saved.get("mode") or "enforce"):
            event_type = "security.policy_mode_changed"
        else:
            event_type = "security.policy_global_updated"

        self.auth_store.append_audit(
            event_type=event_type,
            category="policy_admin",
            outcome="success",
            resource={"type": "global_policy", "id": "content-intent-policy"},
            payload={
                "version": saved.get("version"),
                "mode": saved.get("mode"),
                "enabled": saved.get("enabled"),
                "rule_count": len(saved.get("rules") or []),
            },
        )
        return {
            "policy": saved,
            "summary": store.summarize(saved),
            "catalog": get_policy_catalog(),
        }

    def get_global_policy_hits(
        self,
        *,
        limit: int = 100,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        events = self._collect_policy_runtime_events()
        scope = self._normalize_accessible_instance_ids(accessible_instance_ids)
        if scope is not None:
            events = [event for event in events if str(event.get("instance_id") or "") in scope]
        events.sort(key=lambda item: _ts_sort_key(item.get("ts")), reverse=True)
        page = events[: max(1, min(limit, 500))]
        return {
            "events": page,
            "count": len(page),
            "total": len(events),
        }

    def get_global_policy_detections_by_instance(
        self,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        events = self._collect_policy_runtime_events()
        scope = self._normalize_accessible_instance_ids(accessible_instance_ids)
        if scope is not None:
            events = [event for event in events if str(event.get("instance_id") or "") in scope]
        grouped: dict[str, dict[str, Any]] = {}
        for event in events:
            key = str(event.get("instance_id") or "default")
            item = grouped.setdefault(
                key,
                {
                    "instance_id": key,
                    "instance_name": event.get("instance_name") or key,
                    "detection_count": 0,
                    "blocked_count": 0,
                    "masked_count": 0,
                    "warn_count": 0,
                    "latest_detected_at": None,
                    "top_rules": {},
                },
            )
            item["detection_count"] += 1
            action = str(event.get("policy_action") or event.get("action") or "")
            if action in {"block", "escalate"}:
                item["blocked_count"] += 1
            elif action == "mask":
                item["masked_count"] += 1
            elif action == "warn":
                item["warn_count"] += 1
            ts = str(event.get("ts") or "")
            if ts and (not item["latest_detected_at"] or _ts_sort_key(ts) > _ts_sort_key(item["latest_detected_at"])):
                item["latest_detected_at"] = ts
            for rule_id in event.get("rule_ids") or []:
                item["top_rules"][str(rule_id)] = int(item["top_rules"].get(str(rule_id), 0)) + 1

        instances = []
        for item in grouped.values():
            top_rules = sorted(item["top_rules"].items(), key=lambda entry: entry[1], reverse=True)[:5]
            item["top_rules"] = [{"rule_id": rule_id, "count": count} for rule_id, count in top_rules]
            instances.append(item)
        instances.sort(key=lambda item: (int(item.get("detection_count") or 0), str(item.get("latest_detected_at") or "")), reverse=True)
        return {"instances": instances, "count": len(instances)}

    def get_runtime_audit_events(
        self,
        *,
        instance_id: str,
        limit: int = 40,
        cursor: int | str | None = None,
        status: str | None = None,
        operation: str | None = None,
        search: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Return paginated runtime-audit events for one instance."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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

    def get_activity(
        self,
        *,
        limit: int = 50,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Collect recent activity across configured instances."""
        events: list[dict[str, Any]] = []
        instances = self.list_instances(accessible_instance_ids=accessible_instance_ids)
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

    @staticmethod
    def _display_activity_channel(session_key: str | None) -> str:
        key = str(session_key or "").strip()
        if not key:
            return "unknown"
        if key.startswith("mobile-"):
            return "softnix_app"
        if ":" in key:
            return key.split(":", 1)[0]
        return "unknown"

    def get_activity_debug(
        self,
        *,
        limit: int = 50,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Collect recent activity with parser diagnostics per instance."""
        events: list[dict[str, Any]] = []
        instances = self.list_instances(accessible_instance_ids=accessible_instance_ids)
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
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Generate activity heatmap data for visualization."""
        instances = self.list_instances(accessible_instance_ids=accessible_instance_ids)
        if instance_id:
            scope = self._normalize_accessible_instance_ids(accessible_instance_ids)
            if scope is not None and instance_id not in scope:
                instances = []
            else:
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
        weekday_user_questions = self._aggregate_user_questions_by_weekday(
            instances=instances,
            start_date=start_date,
            end_date=now,
        )

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
            "weekday_user_questions": weekday_user_questions,
        }

    def list_schedules(
        self,
        *,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """List schedules across instances."""
        instances = []
        scope = self._normalize_accessible_instance_ids(accessible_instance_ids)
        for target in self._load_targets():
            if scope is not None and target.id not in scope:
                continue
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

    def get_instance_memory_files(
        self,
        *,
        instance_id: str,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Read editable memory/prompt markdown files from instance workspace."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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

    def update_instance_memory_file(
        self,
        *,
        instance_id: str,
        relative_path: str,
        content: str,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Update one allowed memory/prompt markdown file."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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

    def list_instance_skills(
        self,
        *,
        instance_id: str,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """List skills in workspace/skills/, parsing SKILL.md frontmatter."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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

    def list_skill_bank(
        self,
        *,
        instance_id: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """List the curated skills bank grouped by category."""
        installed_names: set[str] | None = None
        if instance_id:
            target = self._get_target(instance_id)
            self._require_target_access(target, accessible_instance_ids)
            installed_names = {
                str(item.get("skill_name") or "").strip()
                for item in self.list_instance_skills(
                    instance_id=instance_id,
                    accessible_instance_ids=accessible_instance_ids,
                ).get("skills", [])
                if str(item.get("skill_name") or "").strip()
            }
        catalog = list_skill_bank_catalog(installed_skill_names=installed_names)
        return {
            "instance_id": instance_id,
            "source_root": catalog["source_root"],
            "total": catalog["total"],
            "categories": catalog["categories"],
        }

    def import_skill_bank_entry(
        self,
        *,
        instance_id: str,
        bank_skill_id: str,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Import one curated skills bank entry into an instance."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        entry = resolve_skill_bank_entry(bank_skill_id)
        archive_name, archive_base64 = build_skill_bank_archive(entry)
        result = self.import_instance_skill_archive(
            instance_id=instance_id,
            archive_name=archive_name,
            archive_base64=archive_base64,
            skill_name=entry.import_skill_name,
            accessible_instance_ids=accessible_instance_ids,
        )
        self._append_audit_event(
            instance_id=target.id,
            event_type="instance.skill_bank_imported",
            payload={
                "skill_name": result["skill_name"],
                "bank_skill_id": entry.bank_id,
                "source_path": entry.source_relative_path,
                "category": entry.category,
            },
        )
        return {
            **result,
            "bank_skill_id": entry.bank_id,
            "display_name": entry.display_name,
            "description": entry.description,
            "category": entry.category,
            "category_label": entry.category_label,
            "source_path": entry.source_relative_path,
        }

    def get_instance_skill(
        self,
        *,
        instance_id: str,
        skill_name: str,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Return all files in workspace/skills/{skill_name}/ with content."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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

    def export_instance_skill_archive(
        self,
        *,
        instance_id: str,
        skill_name: str,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Package workspace/skills/{skill_name} as a zip archive."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        safe_name = _safe_skill_name(skill_name)
        skill_dir = target.workspace_path / "skills" / safe_name
        if not skill_dir.is_dir():
            raise ValueError(f"Skill '{skill_name}' not found")
        export_dir = target.workspace_path / ".nanobot" / "skill-exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / f"{safe_name}-{secrets.token_hex(8)}.zip"
        try:
            with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for file_path in sorted(skill_dir.rglob("*")):
                    if not file_path.is_file():
                        continue
                    archive.write(file_path, arcname=f"{safe_name}/{file_path.relative_to(skill_dir).as_posix()}")
        except Exception as exc:
            raise ValueError(f"Unable to export skill '{skill_name}': {exc}") from exc
        self._append_audit_event(
            instance_id=target.id,
            event_type="instance.skill_exported",
            payload={"skill_name": skill_name, "file": str(export_path)},
        )
        return {
            "instance_id": target.id,
            "skill_name": skill_name,
            "_file_path": str(export_path),
            "_content_type": "application/zip",
            "_download_name": f"{safe_name}.zip",
        }

    def import_instance_skill_archive(
        self,
        *,
        instance_id: str,
        archive_name: str,
        archive_base64: str,
        skill_name: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Import a skill package from a zip archive into workspace/skills/."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        encoded = str(archive_base64 or "").strip()
        if not encoded:
            raise ValueError("archive_base64 is required")
        try:
            archive_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("archive_base64 is not valid base64") from exc

        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if not members:
                raise ValueError("Skill archive is empty")
            normalized_members: list[tuple[zipfile.ZipInfo, Path]] = []
            ignored_leaf_names = {".DS_Store", "Thumbs.db", "desktop.ini"}
            for member in members:
                relative = _normalize_zip_entry_path(member.filename)
                if any(part == "__MACOSX" for part in relative.parts):
                    continue
                if relative.name in ignored_leaf_names:
                    continue
                normalized_members.append((member, relative))
            if not normalized_members:
                raise ValueError("Skill archive contains no usable files")

            first_segments = {item[1].parts[0] for item in normalized_members if len(item[1].parts) > 1}
            root_files = [item for item in normalized_members if len(item[1].parts) == 1]
            if len(first_segments) > 1 or (first_segments and root_files):
                raise ValueError("Skill archive must use a single top-level folder or place files at the archive root")
            archive_skill_name = skill_name or (next(iter(first_segments)) if len(first_segments) == 1 and not root_files else Path(archive_name or "skill.zip").stem)
            safe_name = _safe_skill_name(archive_skill_name)
            skill_dir = target.workspace_path / "skills" / safe_name
            import_root = skill_dir.parent / f".{safe_name}-{secrets.token_hex(8)}.importing"
            if import_root.exists():
                shutil.rmtree(import_root, ignore_errors=True)
            import_root.mkdir(parents=True, exist_ok=True)
            extracted_skill_dir = import_root / safe_name
            extracted_skill_dir.mkdir(parents=True, exist_ok=True)

            strip_prefix = None
            if len(first_segments) == 1 and not root_files:
                strip_prefix = next(iter(first_segments))

            skill_md_found = False
            for member, relative in normalized_members:
                if strip_prefix and relative.parts and relative.parts[0] == strip_prefix:
                    relative = Path(*relative.parts[1:])
                if not relative.parts:
                    continue
                output_path = (extracted_skill_dir / relative).resolve()
                if not str(output_path).startswith(str(extracted_skill_dir.resolve())):
                    raise ValueError(f"Archive entry '{member.filename}' escapes the skill directory")
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, open(output_path, "wb") as destination:
                    shutil.copyfileobj(source, destination)
                if relative.as_posix() == "SKILL.md":
                    skill_md_found = True

            if not skill_md_found:
                raise ValueError("Skill archive must include SKILL.md")
            if skill_dir.exists():
                shutil.rmtree(skill_dir)
            extracted_skill_dir.rename(skill_dir)
            shutil.rmtree(import_root, ignore_errors=True)

        file_count = sum(1 for _ in skill_dir.rglob("*") if _.is_file())
        self._append_audit_event(
            instance_id=target.id,
            event_type="instance.skill_imported",
            payload={"skill_name": safe_name, "file_count": file_count},
        )
        return {
            "instance_id": target.id,
            "skill_name": safe_name,
            "file_count": file_count,
            "imported": True,
        }

    def update_instance_skill_file(
        self,
        *,
        instance_id: str,
        skill_name: str,
        relative_path: str,
        content: str,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Update one file within workspace/skills/{skill_name}/."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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

    def delete_instance_skill(
        self,
        *,
        instance_id: str,
        skill_name: str,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Delete an entire skill directory from workspace/skills/."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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

    def get_instance_config(
        self,
        *,
        instance_id: str,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Return one instance config as JSON-ready data."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        config = self._load_target_config(target)
        return {
            "instance_id": target.id,
            "config_path": str(target.config_path),
            "config": config.model_dump(by_alias=True),
        }

    def update_instance_config(
        self,
        *,
        instance_id: str,
        config_data: dict[str, Any],
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Validate and save one instance config from raw JSON data."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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
        current_user_id: str | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
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
        mobile_state_cleanup = self.auth_store.clear_mobile_state_for_instance(instance_id)
        instance = self.get_instance(instance_id)
        created_payload = {
            "runtime_mode": instance["runtime_config"]["mode"],
            "gateway_port": instance["gateway_port"],
            "config_path": instance["config_path"],
        }
        if any(mobile_state_cleanup.values()):
            created_payload["mobile_state_cleanup"] = mobile_state_cleanup
        self._append_audit_event(
            instance_id=instance_id,
            event_type="instance.created",
            payload=created_payload,
        )
        creator_user_id = str(current_user_id or "").strip()
        if creator_user_id:
            creator = self.auth_store.get_user_by_id(creator_user_id)
            creator_scope = normalize_instance_ids((creator or {}).get("instance_ids"))
            if creator is not None and creator_scope is not None and instance_id not in creator_scope:
                creator_scope.append(instance_id)
                updated_creator = dict(creator)
                updated_creator["instance_ids"] = creator_scope
                updated_creator["updated_at"] = iso_now()
                self.auth_store.upsert_user(updated_creator)
                self.auth_store.append_audit(
                    event_type="user.instance_access_granted",
                    category="user_management",
                    outcome="success",
                    resource={"type": "user", "id": creator.get("id"), "name": creator.get("username")},
                    payload={"instance_id": instance_id},
                )
        return {
            "instance": instance,
            "registry_entry": result["registry_entry"],
        }

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
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Update one registry-backed instance."""
        registry_path = self._require_registry_path()
        self._require_target_access(self._get_target(instance_id), accessible_instance_ids)
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

    def delete_instance(
        self,
        *,
        instance_id: str,
        purge_files: bool = False,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Delete one instance from the registry."""
        registry_path = self._require_registry_path()
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        result = delete_softnix_instance(
            registry_path=registry_path,
            instance_id=instance_id,
            purge_files=purge_files,
        )
        mobile_state_cleanup = self.auth_store.clear_mobile_state_for_instance(instance_id)
        deleted_payload = {
            "purge_files": bool(purge_files),
            "config_path": str(target.config_path),
        }
        if any(mobile_state_cleanup.values()):
            deleted_payload["mobile_state_cleanup"] = mobile_state_cleanup
        self._append_audit_event(
            instance_id=instance_id,
            event_type="instance.deleted",
            severity="warning",
            payload=deleted_payload,
        )
        self.auth_store.append_audit(
            event_type="instance.deleted",
            category="configuration",
            outcome="success",
            resource={"type": "instance", "id": target.id, "name": getattr(target, "name", target.id)},
            payload=deleted_payload,
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
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Update one channel config safely."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Approve one denied sender and add it to allow_from."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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
        if channel_name == "softnix_app":
            self._sync_relay_allow_from(target, config)
            restart_result = None
            if target.source == "registry" and target.lifecycle and self._lifecycle_command(target, "restart"):
                restart_result = self.execute_instance_action(
                    instance_id=target.id,
                    action="restart",
                    accessible_instance_ids=accessible_instance_ids,
                )
                runtime = {
                    "applied": bool(restart_result.get("ok")),
                    "method": "instance-restart",
                    "detail": (restart_result.get("stderr") or restart_result.get("stdout") or "").strip()[:500],
                    **restart_result,
                }
            else:
                runtime = self._apply_runtime_channel_reload(target)
        else:
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
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Reject one denied sender and remove it from pending list."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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

    def update_workspace_restriction(
        self,
        *,
        instance_id: str,
        restrict_to_workspace: bool,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Update workspace restriction for one instance."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Update default model/provider selection."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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
        restart_result = self._restart_instance_if_supported(target)
        return {
            "instance": self._collect_instance(target),
            "instance_restart": restart_result,
        }

    def update_provider_config(
        self,
        *,
        instance_id: str,
        provider_name: str,
        api_key: str | None = None,
        api_base: str | None = None,
        extra_headers: dict[str, str] | None = None,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Update one provider configuration."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Create or update one MCP server definition."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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

    def delete_mcp_server(
        self,
        *,
        instance_id: str,
        server_name: str,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Delete one MCP server definition."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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

    def validate_provider(
        self,
        *,
        instance_id: str,
        provider_name: str,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Validate provider configuration and, when possible, verify the upstream endpoint live."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
        restart_result = self._restart_instance_if_supported(target)
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

        if not any(item["severity"] == "error" for item in findings):
            live_finding = self._validate_provider_live(
                provider_name=provider_name,
                provider_cfg=provider_cfg,
                model=model,
                selected_provider=selected_provider,
                spec=spec,
            )
            if live_finding is not None:
                findings.append(live_finding)

        status = "ok" if not any(item["severity"] == "error" for item in findings) else "error"
        if status == "ok" and any(item["severity"] == "warning" for item in findings):
            status = "warning"
        
        # Build response with restart info
        response = {
            "instance_id": target.id,
            "provider_name": provider_name,
            "status": status,
            "findings": findings,
        }
        
        # Include restart result if attempted, but don't fail validation if restart failed
        if restart_result.get("attempted"):
            if restart_result.get("error"):
                # Restart was attempted but failed - add as warning, not error
                response["instance_restart_warning"] = restart_result.get("error")
            elif restart_result.get("ok"):
                response["instance_restart"] = restart_result
        
        return response

    def _validate_provider_live(
        self,
        *,
        provider_name: str,
        provider_cfg: Any,
        model: str,
        selected_provider: str,
        spec: Any,
    ) -> dict[str, Any] | None:
        api_base = str(provider_cfg.api_base or spec.default_api_base or "").strip()
        api_key = str(provider_cfg.api_key or "").strip()
        extra_headers = dict(provider_cfg.extra_headers or {})
        if not api_base:
            return None
        if not spec.is_direct and provider_name not in {"softnix_gen_ai", "custom"}:
            return None

        headers = {"Content-Type": "application/json", **extra_headers}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        if selected_provider in {"auto", provider_name} and model:
            if spec.is_direct or provider_name in {"softnix_gen_ai", "custom"}:
                return self._validate_provider_live_sdk_chat(
                    api_base=api_base,
                    api_key=api_key,
                    extra_headers=extra_headers,
                    model=model,
                )
            return self._validate_provider_live_chat(
                api_base=api_base,
                headers=headers,
                model=model,
            )
        return self._validate_provider_live_models(api_base=api_base, headers=headers, model=model)

    def _validate_provider_live_sdk_chat(
        self,
        *,
        api_base: str,
        api_key: str,
        extra_headers: dict[str, str],
        model: str,
    ) -> dict[str, Any]:
        async def _probe() -> dict[str, Any]:
            client = AsyncOpenAI(
                api_key=api_key or "no-key",
                base_url=api_base,
                http_client=httpx.AsyncClient(
                    headers={"User-Agent": SOFTNIX_GENAI_USER_AGENT},
                ),
                default_headers={
                    **extra_headers,
                    "x-session-affinity": uuid.uuid4().hex,
                    "User-Agent": SOFTNIX_GENAI_USER_AGENT,
                },
            )
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                    temperature=0.1,
                )
                choices = getattr(response, "choices", None) or []
                if not choices:
                    return {
                        "severity": "warning",
                        "code": "live_chat_empty",
                        "detail": "Chat completion endpoint responded but returned no choices.",
                    }

                tool_response = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                    temperature=0.1,
                    tools=self._provider_validation_tool_schemas(),
                    tool_choice="auto",
                )
                tool_choices = getattr(tool_response, "choices", None) or []
                if not tool_choices:
                    return {
                        "severity": "warning",
                        "code": "live_tools_empty",
                        "detail": f"Endpoint accepted agent tool definitions for model '{model}' but returned no choices.",
                    }
                return {
                    "severity": "info",
                    "code": "live_tools_ok",
                    "detail": f"Endpoint accepted representative agent tool definitions for model '{model}'.",
                }
            except Exception as exc:
                tool_names = ", ".join(
                    tool.get("function", {}).get("name", "unknown")
                    for tool in self._provider_validation_tool_schemas()
                )
                return {
                    "severity": "error",
                    "code": "live_tools_failed",
                    "detail": (
                        f"Endpoint rejected representative agent tool requests for model '{model}' "
                        f"({tool_names}): {self._provider_live_error(exc)}"
                    ),
                }
            finally:
                await client.close()

        return asyncio.run(_probe())

    def _validate_provider_live_models(
        self,
        *,
        api_base: str,
        headers: dict[str, str],
        model: str,
    ) -> dict[str, Any]:
        url = f"{api_base.rstrip('/')}/models"
        try:
            response = httpx.get(url, headers=headers, timeout=10.0)
            response.raise_for_status()
            payload = response.json() if response.content else {}
            models = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(models, list) and model and not any(str(item.get("id") or "") == model for item in models if isinstance(item, dict)):
                return {
                    "severity": "warning",
                    "code": "live_model_not_listed",
                    "detail": f"Endpoint responded, but model '{model}' was not listed in /models.",
                }
            return {
                "severity": "info",
                "code": "live_models_ok",
                "detail": "Endpoint responded successfully to /models.",
            }
        except Exception as exc:
            return {
                "severity": "error",
                "code": "live_models_failed",
                "detail": f"Live endpoint check failed: {self._provider_live_error(exc)}",
            }

    def _validate_provider_live_chat(
        self,
        *,
        api_base: str,
        headers: dict[str, str],
        model: str,
    ) -> dict[str, Any]:
        url = f"{api_base.rstrip('/')}/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=15.0)
            response.raise_for_status()
            data = response.json() if response.content else {}
            choices = data.get("choices") if isinstance(data, dict) else None
            if not isinstance(choices, list) or not choices:
                return {
                    "severity": "warning",
                    "code": "live_chat_empty",
                    "detail": "Chat completion endpoint responded but returned no choices.",
                }
            tool_check = self._validate_provider_live_tools(
                api_base=api_base,
                headers=headers,
                model=model,
            )
            if tool_check is not None:
                return tool_check
            return {
                "severity": "info",
                "code": "live_chat_ok",
                "detail": f"Endpoint accepted a lightweight chat completion for model '{model}'.",
            }
        except Exception as exc:
            return {
                "severity": "error",
                "code": "live_chat_failed",
                "detail": f"Live chat completion check failed for model '{model}': {self._provider_live_error(exc)}",
            }

    def _validate_provider_live_tools(
        self,
        *,
        api_base: str,
        headers: dict[str, str],
        model: str,
    ) -> dict[str, Any] | None:
        url = f"{api_base.rstrip('/')}/chat/completions"
        tool_schemas = self._provider_validation_tool_schemas()
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "tools": tool_schemas,
            "tool_choice": "auto",
        }
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=15.0)
            response.raise_for_status()
            return {
                "severity": "info",
                "code": "live_tools_ok",
                "detail": f"Endpoint accepted representative agent tool definitions for model '{model}'.",
            }
        except Exception as exc:
            tool_names = ", ".join(
                tool.get("function", {}).get("name", "unknown")
                for tool in tool_schemas
            )
            return {
                "severity": "error",
                "code": "live_tools_failed",
                "detail": (
                    f"Endpoint rejected representative agent tool requests for model '{model}' "
                    f"({tool_names}): {self._provider_live_error(exc)}"
                ),
            }

    @staticmethod
    def _provider_validation_tool_schemas() -> list[dict[str, Any]]:
        try:
            from nanobot.agent.loop import AgentLoop
            from nanobot.bus.queue import MessageBus

            class _ProbeProvider:
                def get_default_model(self) -> str:
                    return "probe"

            probe = AgentLoop(
                bus=MessageBus(),
                provider=_ProbeProvider(),
                workspace=Path("/tmp"),
                model="probe",
            )
            definitions = probe.tools.get_definitions()
            preferred = {"read_file", "exec", "message"}
            selected = [
                tool for tool in definitions
                if tool.get("function", {}).get("name") in preferred
            ]
            if selected:
                return selected
        except Exception:
            pass
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read the contents of a file at the given path.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The file path to read",
                            }
                        },
                        "required": ["path"],
                    },
                },
            }
        ]

    @staticmethod
    def _provider_live_error(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            detail = exc.response.text.strip()
            return f"HTTP {exc.response.status_code}{f' - {detail[:200]}' if detail else ''}"
        return str(exc)

    def _restart_instance_if_supported(self, target: InstanceTarget) -> dict[str, Any]:
        restart_result: dict[str, Any] | None = None
        restart_error: str | None = None
        if target.source == "registry" and target.lifecycle and self._lifecycle_command(target, "restart"):
            try:
                restart_result = self.execute_instance_action(instance_id=target.id, action="restart")
            except Exception as exc:
                # Capture restart error but don't fail the entire operation
                # This can happen if Docker permissions are not set up correctly
                restart_error = str(exc)
        return {
            "attempted": restart_result is not None or restart_error is not None,
            "success": restart_result is not None and restart_result.get("ok", False),
            "error": restart_error,
            **(restart_result or {}),
        }

    def validate_mcp_server(
        self,
        *,
        instance_id: str,
        server_name: str,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Validate MCP server configuration without connecting to it."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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

    def execute_instance_action(
        self,
        *,
        instance_id: str,
        action: str,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Run one lifecycle action for an instance through its configured supervisor command."""
        if action not in {"start", "stop", "restart"}:
            raise ValueError(f"Unsupported action '{action}'")
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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
        
        # Detect common configuration errors and provide user-friendly messages
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        error_output = stderr + "\n" + stdout
        
        if result.returncode != 0:
            if "No API key configured" in error_output or "No API key" in error_output:
                raise ValueError(
                    f"Missing provider API key. Please configure your LLM provider in the Providers tab "
                    f"or edit the instance config at '{target.config_path}'. "
                    f"Error: {stderr or stdout}"
                )
            elif "API key" in error_output and "not configured" in error_output:
                raise ValueError(
                    f"Provider API key is missing. Go to Providers tab to add your API key. "
                    f"Error: {stderr or stdout}"
                )
        
        payload = {
            "instance": self._collect_instance(target),
            "action": action,
            "command": command,
            "returncode": result.returncode,
            "stdout": stdout[:2000],
            "stderr": stderr[:2000],
            "ok": result.returncode == 0,
        }
        if result.returncode == 0 and action in {"start", "restart", "stop"}:
            try:
                config = self._load_target_config(target)
                changed = False
                for server in config.tools.mcp_servers.values():
                    if getattr(server, "restart_required", False):
                        server.restart_required = False
                        changed = True
                if changed:
                    save_config(config, target.config_path)
                    payload["instance"] = self._collect_instance(target)
            except Exception:
                pass
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
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Create one cron job in the target workspace."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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

    def set_schedule_enabled(
        self,
        *,
        instance_id: str,
        job_id: str,
        enabled: bool,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Enable or disable a cron job."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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

    def run_schedule(
        self,
        *,
        instance_id: str,
        job_id: str,
        force: bool = True,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Run a cron job immediately."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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

    def delete_schedule(
        self,
        *,
        instance_id: str,
        job_id: str,
        accessible_instance_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Delete one cron job."""
        target = self._get_target(instance_id)
        self._require_target_access(target, accessible_instance_ids)
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
            configured_api_base = str(provider_cfg.api_base or "").strip()
            effective_api_base = configured_api_base or str(spec.default_api_base or "").strip()
            providers.append(
                {
                    "name": spec.name,
                    "label": spec.label,
                    "configured": bool(spec.is_oauth or provider_cfg.api_key or provider_cfg.api_base),
                    "api_key_masked": "" if spec.is_oauth else _mask_secret(provider_cfg.api_key),
                    "api_base": configured_api_base,
                    "api_base_effective": effective_api_base,
                    "api_base_default": str(spec.default_api_base or "").strip(),
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
                "scope": _truncate_text(payload.get("scope"), limit=64),
                "policy_action": _truncate_text(payload.get("action"), limit=32),
                "policy_severity": _truncate_text(payload.get("severity"), limit=32),
                "policy_mode": _truncate_text(payload.get("policy_mode"), limit=32),
                "policy_version": payload.get("policy_version"),
                "rule_ids": payload.get("rule_ids") if isinstance(payload.get("rule_ids"), list) else [],
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

    def _collect_policy_runtime_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for target in self._load_targets():
            runtime_events = self._read_runtime_audit_events(
                workspace_path=target.workspace_path,
                instance_id=target.id,
                instance_name=target.name,
            )
            for event in runtime_events:
                if str(event.get("operation") or "") != "policy_detection":
                    continue
                events.append(event)
        events.sort(key=lambda item: _ts_sort_key(item.get("ts")), reverse=True)
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
            session_key = session_stem.replace("_", ":", 1)
            channel = self._display_activity_channel(session_key)
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
        policy_detection_count = 0
        last_event_at: str | None = None
        for event in events:
            operation = str(event.get("operation") or "")
            if operation in {"command", "package_install"}:
                exec_count += 1
            if operation.startswith("file_"):
                file_op_count += 1
            if operation == "package_install":
                package_install_count += 1
            if operation == "policy_detection":
                policy_detection_count += 1
            if "blocked by safety guard" in str(event.get("result_preview") or "").lower():
                blocked_count += 1
            if str(event.get("policy_action") or "") in {"block", "escalate"}:
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
            "policy_detection_count": policy_detection_count,
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
                            "channel": self._display_activity_channel(session_info["key"]),
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

    def _aggregate_user_questions_by_weekday(
        self,
        *,
        instances: list[dict[str, Any]],
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, Any]:
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        buckets: dict[str, list[int]] = {
            str(instance.get("id") or ""): [0] * 7
            for instance in instances
            if str(instance.get("id") or "").strip()
        }
        names = {
            str(instance.get("id") or ""): str(instance.get("name") or instance.get("id") or "").strip()
            for instance in instances
            if str(instance.get("id") or "").strip()
        }
        total_questions = 0

        for instance in instances:
            instance_id = str(instance.get("id") or "").strip()
            if not instance_id:
                continue
            sessions_dir = Path(str(instance.get("workspace_path") or "")) / "sessions"
            if not sessions_dir.exists() or not sessions_dir.is_dir():
                continue
            try:
                session_files = sorted(
                    sessions_dir.glob("*.jsonl"),
                    key=lambda item: item.stat().st_mtime,
                    reverse=True,
                )
            except Exception:
                continue

            for session_path in session_files:
                try:
                    stat = session_path.stat()
                except Exception:
                    continue
                file_mtime = datetime.fromtimestamp(stat.st_mtime, tz=local_tz)
                if file_mtime < start_date.astimezone(local_tz):
                    continue
                try:
                    raw_lines = session_path.read_text(encoding="utf-8").splitlines()
                except Exception:
                    continue

                fallback_ts = file_mtime.isoformat()
                for raw in raw_lines:
                    text = raw.strip()
                    if not text:
                        continue
                    try:
                        data = json.loads(text)
                    except Exception:
                        continue
                    if not isinstance(data, dict) or data.get("_type") == "metadata":
                        continue
                    role = self._extract_event_role(data)
                    if role != "user":
                        continue
                    content = self._extract_event_content(data, role=role)
                    if not content or content == "(empty)":
                        continue
                    ts = self._extract_event_timestamp(data, fallback=fallback_ts)
                    dt = _parse_iso_datetime(ts, default_tz=local_tz)
                    if dt is None:
                        continue
                    dt_local = dt.astimezone(local_tz)
                    if dt_local < start_date.astimezone(local_tz) or dt_local > end_date.astimezone(local_tz):
                        continue
                    buckets[instance_id][dt_local.weekday()] += 1
                    total_questions += 1

        series = []
        for instance in instances:
            instance_id = str(instance.get("id") or "").strip()
            if not instance_id:
                continue
            counts = buckets.get(instance_id, [0] * 7)
            series.append(
                {
                    "instance_id": instance_id,
                    "instance_name": names.get(instance_id) or instance_id,
                    "counts": counts,
                    "total": sum(counts),
                }
            )

        return {
            "days": day_names,
            "series": series,
            "total_questions": total_questions,
        }

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
            "softnix_app",
            "telegram",
            "whatsapp",
            "discord",
            "email",
            "slack",
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
                    "enabled": bool(server.enabled),
                    "restart_required": bool(server.restart_required),
                    "status": server.connector_status,
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
        has_online_mcp = any(getattr(server, "enabled", True) for server in config.tools.mcp_servers.values())
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
