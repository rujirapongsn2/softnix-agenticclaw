from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
from datetime import datetime, timedelta, timezone

_audit_request_ctx: threading.local = threading.local()


def set_request_audit_context(
    *,
    user_id: str | None = None,
    username: str | None = None,
    role: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    _audit_request_ctx.actor = {
        "user_id": str(user_id or "").strip() or None,
        "username": str(username or "").strip() or None,
        "role": str(role or "").strip() or None,
        "ip": str(ip or "").strip() or None,
        "user_agent": str(user_agent or "").strip()[:300] or None,
    }


def get_request_audit_context() -> dict | None:
    return getattr(_audit_request_ctx, "actor", None)


def clear_request_audit_context() -> None:
    _audit_request_ctx.actor = None

ADMIN_SESSION_COOKIE = "softnix_admin_session"
ADMIN_ROLE_ORDER = ("viewer", "operator", "admin", "owner")
ADMIN_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "viewer": {
        "overview.read",
        "activity.read",
        "runtime_audit.read",
        "instance.read",
        "config.read",
        "memory.read",
        "skills.read",
        "channel.read",
        "provider.read",
        "mcp.read",
        "schedule.read",
        "security.read",
    },
    "operator": {
        "overview.read",
        "activity.read",
        "runtime_audit.read",
        "instance.read",
        "instance.control",
        "config.read",
        "memory.read",
        "skills.read",
        "channel.read",
        "provider.read",
        "mcp.read",
        "schedule.read",
        "schedule.run",
        "access_request.review",
        "security.read",
    },
    "admin": {
        "overview.read",
        "activity.read",
        "runtime_audit.read",
        "instance.read",
        "instance.create",
        "instance.update",
        "instance.control",
        "config.read",
        "config.update",
        "memory.read",
        "memory.update",
        "skills.read",
        "skills.update",
        "skills.delete",
        "channel.read",
        "channel.update",
        "provider.read",
        "provider.update",
        "mcp.read",
        "mcp.update",
        "schedule.read",
        "schedule.update",
        "schedule.run",
        "access_request.review",
        "security.read",
        "user.read",
        "user.create",
        "user.update",
    },
    "owner": {
        "overview.read",
        "activity.read",
        "runtime_audit.read",
        "instance.read",
        "instance.create",
        "instance.update",
        "instance.delete",
        "instance.control",
        "config.read",
        "config.update",
        "memory.read",
        "memory.update",
        "skills.read",
        "skills.update",
        "skills.delete",
        "channel.read",
        "channel.update",
        "provider.read",
        "provider.update",
        "mcp.read",
        "mcp.update",
        "schedule.read",
        "schedule.update",
        "schedule.run",
        "access_request.review",
        "security.read",
        "security.update",
        "user.read",
        "user.create",
        "user.update",
        "user.disable",
        "auth.manage",
    },
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def iso_in(*, hours: int = 0, days: int = 0) -> str:
    return (utc_now() + timedelta(hours=hours, days=days)).isoformat()


def normalize_role(value: str | None) -> str:
    role = str(value or "viewer").strip().lower()
    return role if role in ADMIN_ROLE_PERMISSIONS else "viewer"


def permissions_for_role(role: str | None) -> list[str]:
    return sorted(ADMIN_ROLE_PERMISSIONS[normalize_role(role)])


def has_permission(role: str | None, permission: str) -> bool:
    return permission in ADMIN_ROLE_PERMISSIONS[normalize_role(role)]


def normalize_username(value: str) -> str:
    return str(value or "").strip().lower()


def normalize_email(value: str | None) -> str | None:
    email = str(value or "").strip().lower()
    return email or None


def new_user_id() -> str:
    return secrets.token_hex(12)


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def hash_password(password: str) -> str:
    if len(password or "") < 8:
        raise ValueError("Password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(derived).decode("ascii")
    return f"scrypt$16384$8$1${salt_b64}${hash_b64}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, n_value, r_value, p_value, salt_b64, hash_b64 = str(stored_hash).split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(hash_b64.encode("ascii"))
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_value),
            r=int(r_value),
            p=int(p_value),
            dklen=len(expected),
        )
        return hmac.compare_digest(derived, expected)
    except Exception:
        return False


def parse_iso_datetime(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_session_expired(expires_at: str | None) -> bool:
    dt = parse_iso_datetime(expires_at)
    if dt is None:
        return True
    return dt <= utc_now()


def sanitize_user(user: dict[str, object]) -> dict[str, object]:
    role = normalize_role(str(user.get("role") or "viewer"))
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "display_name": user.get("display_name") or user.get("username"),
        "email": user.get("email"),
        "role": role,
        "status": user.get("status") or "active",
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
        "last_login_at": user.get("last_login_at"),
        "permissions": permissions_for_role(role),
    }
