"""Minimal HTTP server for the nanobot admin API."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from nanobot.admin.auth import ADMIN_SESSION_COOKIE, clear_request_audit_context, has_permission, normalize_role, set_request_audit_context
from nanobot.admin.service import AdminService

STATIC_DIR = Path(__file__).with_name("static")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOFTNIX_WHITE_LOGO = PROJECT_ROOT / "softnix-logo-white.png"
SOFTNIX_LOGIN_LOGO = STATIC_DIR / "Logo_Softnix.png"


def resolve_admin_get(service: AdminService, raw_path: str) -> tuple[HTTPStatus, Any]:
    """Resolve one GET request path into a JSON payload."""
    parsed = urlparse(raw_path)
    path = parsed.path.rstrip("/") or "/"

    if path == "/":
        return HTTPStatus.OK, {
            "service": "nanobot-admin",
            "mode": "safe-config",
            "endpoints": [
                "/admin/health",
                "/admin/overview",
                "/admin/instances",
                "/admin/instances/:id",
                "/admin/instances/:id/config",
                "/admin/instances/:id/memory-files",
                "/admin/instances/:id/start",
                "/admin/instances/:id/stop",
                "/admin/instances/:id/restart",
                "/admin/activity",
                "/admin/activity/debug",
                "/admin/analytics/activity-heatmap",
                "/admin/access-requests",
                "/admin/schedules",
                "/admin/channels",
                "/admin/providers",
                "/admin/mcp/servers",
                "/admin/security",
                "/admin/security/policies/global",
                "/admin/security/policies/global/hits",
                "/admin/security/policies/global/detections-by-instance",
                "/admin/runtime-audit",
                "/admin/users",
            ],
        }

    if path == "/admin/health":
        return HTTPStatus.OK, service.get_health()
    if path == "/admin/auth/bootstrap-status":
        return HTTPStatus.OK, {"bootstrap_required": not service.has_admin_users()}
    if path == "/admin/overview":
        return HTTPStatus.OK, service.get_overview()
    if path == "/admin/instances":
        return HTTPStatus.OK, {"instances": service.list_instances()}
    if path == "/admin/activity":
        return HTTPStatus.OK, service.get_activity()
    if path == "/admin/activity/debug":
        return HTTPStatus.OK, service.get_activity_debug()
    if path == "/admin/analytics/activity-heatmap":
        query = parse_qs(parsed.query)
        instance_id = (query.get("instance_id") or [None])[0]
        period = (query.get("period") or ["week"])[0]
        days = int((query.get("days") or [30])[0])
        return HTTPStatus.OK, service.get_activity_heatmap(
            instance_id=instance_id,
            period=period,
            days=days,
        )
    if path == "/admin/access-requests":
        return HTTPStatus.OK, service.list_access_requests()
    if path == "/admin/schedules":
        return HTTPStatus.OK, service.list_schedules()
    if path == "/admin/users":
        return HTTPStatus.OK, service.list_admin_users()
    if path.startswith("/admin/instances/") and path.endswith("/memory-files"):
        instance_id = path.split("/")[-2]
        try:
            return HTTPStatus.OK, service.get_instance_memory_files(instance_id=instance_id)
        except ValueError as exc:
            return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
    if path.startswith("/admin/instances/") and "/skills/" in path:
        parts = path.split("/")
        try:
            idx = parts.index("skills")
            instance_id = parts[idx - 1]
            skill_name = parts[idx + 1] if idx + 1 < len(parts) else ""
        except (ValueError, IndexError):
            return HTTPStatus.BAD_REQUEST, {"error": "Invalid skills path"}
        try:
            return HTTPStatus.OK, service.get_instance_skill(instance_id=instance_id, skill_name=skill_name)
        except ValueError as exc:
            return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
    if path.startswith("/admin/instances/") and path.endswith("/skills"):
        instance_id = path.split("/")[-2]
        try:
            return HTTPStatus.OK, service.list_instance_skills(instance_id=instance_id)
        except ValueError as exc:
            return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
    if path.startswith("/admin/instances/") and path.endswith("/config"):
        instance_id = path.split("/")[-2]
        return HTTPStatus.OK, service.get_instance_config(instance_id=instance_id)
    if path.startswith("/admin/instances/"):
        instance_id = path.rsplit("/", 1)[-1]
        instance = service.get_instance(instance_id)
        if instance is None:
            return HTTPStatus.NOT_FOUND, {"error": "Instance not found"}
        return HTTPStatus.OK, instance
    if path == "/admin/channels":
        return HTTPStatus.OK, {"channels": service.list_channels()}
    if path == "/admin/providers":
        return HTTPStatus.OK, service.list_providers()
    if path == "/admin/mcp/servers":
        return HTTPStatus.OK, service.list_mcp_servers()
    if path == "/admin/security":
        return HTTPStatus.OK, service.get_security()
    if path == "/admin/security/policies/global":
        return HTTPStatus.OK, service.get_global_policy()
    if path == "/admin/security/policies/global/hits":
        query = parse_qs(parsed.query)
        limit = int((query.get("limit") or [100])[0])
        return HTTPStatus.OK, service.get_global_policy_hits(limit=min(limit, 500))
    if path == "/admin/security/policies/global/detections-by-instance":
        return HTTPStatus.OK, service.get_global_policy_detections_by_instance()
    if path == "/admin/auth-audit":
        query = parse_qs(parsed.query)
        limit = int((query.get("limit") or [100])[0])
        offset = int((query.get("offset") or [0])[0])
        category = (query.get("category") or ["all"])[0]
        outcome = (query.get("outcome") or ["all"])[0]
        search = (query.get("search") or [""])[0]
        return HTTPStatus.OK, service.get_auth_audit_log(
            limit=min(limit, 200),
            offset=max(offset, 0),
            category=category,
            outcome=outcome,
            search=search,
        )
    if path == "/admin/runtime-audit":
        query = parse_qs(parsed.query)
        instance_id = (query.get("instance_id") or ["default"])[0]
        limit = (query.get("limit") or [40])[0]
        cursor = (query.get("cursor") or [None])[0]
        status_filter = (query.get("status") or ["all"])[0]
        operation = (query.get("operation") or ["all"])[0]
        search = (query.get("search") or [""])[0]
        try:
            payload = service.get_runtime_audit_events(
                instance_id=instance_id,
                limit=limit,
                cursor=cursor,
                status=status_filter,
                operation=operation,
                search=search,
            )
        except ValueError as exc:
            return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
        return HTTPStatus.OK, payload

    # Mobile API (Polling agent replies)
    if path == "/admin/mobile/poll":
        query = parse_qs(parsed.query)
        instance_id = (query.get("instance_id") or [None])[0]
        sender_id = (query.get("sender_id") or [None])[0]
        if not instance_id or not sender_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing instance_id or sender_id"}
        return HTTPStatus.OK, {"replies": service.get_mobile_replies(instance_id, sender_id)}

    if path == "/admin/mobile/devices":
        query = parse_qs(parsed.query)
        instance_id = (query.get("instance_id") or [None])[0]
        if not instance_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing instance_id"}
        return HTTPStatus.OK, {"devices": service.list_mobile_devices(instance_id)}

    if path == "/admin/mobile/push/config":
        return HTTPStatus.OK, service.get_mobile_push_config()

    if path == "/admin/mobile/media":
        query = parse_qs(parsed.query)
        instance_id = (query.get("instance_id") or [None])[0]
        sender_id = (query.get("sender_id") or [None])[0]
        file_name = (query.get("file") or [None])[0]
        if not instance_id or not sender_id or not file_name:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing instance_id, sender_id, or file"}
        try:
            media_path, content_type = service.get_mobile_media_file(instance_id, sender_id, file_name)
        except ValueError as exc:
            return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
        return HTTPStatus.OK, {"_file_path": str(media_path), "_content_type": content_type}

    if path == "/admin/mobile/ngrok/status":
        return HTTPStatus.OK, service.get_ngrok_status()

    if path == "/admin/mobile/network-info":
        import socket
        port = service.admin_port if hasattr(service, "admin_port") else 18880
        addrs: list[dict] = []
        try:
            import netifaces
            for iface in netifaces.interfaces():
                for af, entries in netifaces.ifaddresses(iface).items():
                    if af == netifaces.AF_INET:
                        for entry in entries:
                            ip = entry.get("addr", "")
                            if ip and ip != "127.0.0.1":
                                addrs.append({"iface": iface, "ip": ip, "url": f"http://{ip}:{port}"})
        except ImportError:
            # fallback: hostname-based resolution
            try:
                hostname = socket.gethostname()
                local_ips = socket.getaddrinfo(hostname, None, socket.AF_INET)
                seen: set[str] = set()
                for item in local_ips:
                    ip = item[4][0]
                    if ip and ip != "127.0.0.1" and ip not in seen:
                        seen.add(ip)
                        addrs.append({"iface": "eth", "ip": ip, "url": f"http://{ip}:{port}"})
            except Exception:
                pass
        ngrok = service.get_ngrok_status()
        return HTTPStatus.OK, {"addresses": addrs, "port": port, "ngrok": ngrok}

    return HTTPStatus.NOT_FOUND, {"error": "Not found"}


def resolve_admin_patch(
    service: AdminService, raw_path: str, payload: dict[str, Any]
) -> tuple[HTTPStatus, Any]:
    """Resolve one PATCH request path into a JSON payload."""
    parsed = urlparse(raw_path)
    path = parsed.path.rstrip("/") or "/"

    try:
        if path == "/admin/auth/change-password":
            return HTTPStatus.BAD_REQUEST, {"error": "Missing authenticated user"}
        if path.startswith("/admin/users/"):
            user_id = path.rsplit("/", 1)[-1]
            return HTTPStatus.OK, service.update_admin_user(
                user_id=user_id,
                display_name=payload.get("display_name"),
                email=payload.get("email"),
                role=payload.get("role"),
                status=payload.get("status"),
            )
        if path.startswith("/admin/instances/") and path.endswith("/memory-files"):
            instance_id = path.split("/")[-2]
            relative_path = payload.get("path")
            content = payload.get("content")
            if not isinstance(relative_path, str):
                return HTTPStatus.BAD_REQUEST, {"error": "Missing memory file path"}
            if not isinstance(content, str):
                return HTTPStatus.BAD_REQUEST, {"error": "Missing memory file content"}
            result = service.update_instance_memory_file(
                instance_id=instance_id,
                relative_path=relative_path,
                content=content,
            )
            return HTTPStatus.OK, result
        if path.startswith("/admin/instances/") and "/skills/" in path:
            parts = path.split("/")
            try:
                idx = parts.index("skills")
                instance_id = parts[idx - 1]
                skill_name = parts[idx + 1] if idx + 1 < len(parts) else ""
            except (ValueError, IndexError):
                return HTTPStatus.BAD_REQUEST, {"error": "Invalid skills path"}
            relative_path = payload.get("path")
            content = payload.get("content")
            if not isinstance(relative_path, str) or not relative_path:
                return HTTPStatus.BAD_REQUEST, {"error": "Missing skill file path"}
            if not isinstance(content, str):
                return HTTPStatus.BAD_REQUEST, {"error": "Missing skill file content"}
            try:
                result = service.update_instance_skill_file(
                    instance_id=instance_id,
                    skill_name=skill_name,
                    relative_path=relative_path,
                    content=content,
                )
                return HTTPStatus.OK, result
            except ValueError as exc:
                return HTTPStatus.BAD_REQUEST, {"error": str(exc)}

        if path.startswith("/admin/instances/") and path.endswith("/config"):
            instance_id = path.split("/")[-2]
            config_data = payload.get("config")
            if not isinstance(config_data, dict):
                return HTTPStatus.BAD_REQUEST, {"error": "Missing config payload"}
            result = service.update_instance_config(instance_id=instance_id, config_data=config_data)
            return HTTPStatus.OK, result

        if path.startswith("/admin/instances/"):
            instance_id = path.rsplit("/", 1)[-1]
            result = service.update_instance(
                instance_id=instance_id,
                name=payload.get("name"),
                owner=payload.get("owner"),
                env=payload.get("env"),
                repo_root=payload.get("repo_root"),
                nanobot_bin=payload.get("nanobot_bin"),
                gateway_port=payload.get("gateway_port"),
                runtime_mode=payload.get("runtime_mode"),
                sandbox_profile=payload.get("sandbox_profile"),
                sandbox_image=payload.get("sandbox_image"),
                sandbox_execution_strategy=payload.get("sandbox_execution_strategy"),
                sandbox_cpu_limit=payload.get("sandbox_cpu_limit"),
                sandbox_memory_limit=payload.get("sandbox_memory_limit"),
                sandbox_pids_limit=payload.get("sandbox_pids_limit"),
                sandbox_tmpfs_size_mb=payload.get("sandbox_tmpfs_size_mb"),
                sandbox_network_policy=payload.get("sandbox_network_policy"),
                sandbox_timeout_seconds=payload.get("sandbox_timeout_seconds"),
            )
            return HTTPStatus.OK, result

        if path == "/admin/providers/default":
            instance_id = payload.get("instance_id") or "default"
            instance = service.update_provider_defaults(
                instance_id=instance_id,
                model=payload.get("model"),
                provider=payload.get("provider"),
            )
            return HTTPStatus.OK, {"instance": instance}

        if path.startswith("/admin/providers/"):
            provider_name = path.rsplit("/", 1)[-1]
            instance_id = payload.get("instance_id") or "default"
            instance = service.update_provider_config(
                instance_id=instance_id,
                provider_name=provider_name,
                api_key=payload.get("api_key"),
                api_base=payload.get("api_base"),
                extra_headers=payload.get("extra_headers"),
            )
            return HTTPStatus.OK, {"instance": instance}

        if path.startswith("/admin/channels/"):
            channel_name = path.rsplit("/", 1)[-1]
            instance_id = payload.get("instance_id") or "default"
            instance = service.update_channel(
                instance_id=instance_id,
                channel_name=channel_name,
                enabled=payload.get("enabled"),
                allow_from=payload.get("allow_from"),
                settings=payload.get("settings"),
            )
            return HTTPStatus.OK, {"instance": instance}

        if path == "/admin/security/workspace-restriction":
            instance_id = payload.get("instance_id") or "default"
            if "restrict_to_workspace" not in payload:
                return HTTPStatus.BAD_REQUEST, {"error": "Missing 'restrict_to_workspace'"}
            instance = service.update_workspace_restriction(
                instance_id=instance_id,
                restrict_to_workspace=bool(payload.get("restrict_to_workspace")),
            )
            return HTTPStatus.OK, {"instance": instance}
        if path == "/admin/security/policies/global":
            policy = payload.get("policy")
            if not isinstance(policy, dict):
                return HTTPStatus.BAD_REQUEST, {"error": "Missing policy payload"}
            return HTTPStatus.OK, service.update_global_policy(policy=policy)

        if path == "/admin/mcp/servers":
            instance_id = payload.get("instance_id") or "default"
            server_name = payload.get("server_name")
            server_data = payload.get("server")
            if not server_name or not isinstance(server_data, dict):
                return HTTPStatus.BAD_REQUEST, {"error": "Missing MCP server payload"}
            instance = service.upsert_mcp_server(
                instance_id=instance_id,
                server_name=server_name,
                server_data=server_data,
            )
            return HTTPStatus.OK, {"instance": instance}

        if path.startswith("/admin/schedules/") and path.endswith("/enabled"):
            job_id = path.split("/")[-2]
            instance_id = payload.get("instance_id") or "default"
            if "enabled" not in payload:
                return HTTPStatus.BAD_REQUEST, {"error": "Missing 'enabled'"}
            result = service.set_schedule_enabled(
                instance_id=instance_id,
                job_id=job_id,
                enabled=bool(payload.get("enabled")),
            )
            return HTTPStatus.OK, result
    except ValueError as exc:
        return HTTPStatus.BAD_REQUEST, {"error": str(exc)}

    return HTTPStatus.NOT_FOUND, {"error": "Not found"}


def resolve_admin_delete(
    service: AdminService, raw_path: str, payload: dict[str, Any]
) -> tuple[HTTPStatus, Any]:
    """Resolve one DELETE request path into a JSON payload."""
    parsed = urlparse(raw_path)
    path = parsed.path.rstrip("/") or "/"
    try:
        if path.startswith("/admin/instances/") and "/skills/" in path:
            parts = path.split("/")
            try:
                idx = parts.index("skills")
                instance_id = parts[idx - 1]
                skill_name = parts[idx + 1] if idx + 1 < len(parts) else ""
            except (ValueError, IndexError):
                return HTTPStatus.BAD_REQUEST, {"error": "Invalid skills path"}
            try:
                result = service.delete_instance_skill(instance_id=instance_id, skill_name=skill_name)
                return HTTPStatus.OK, result
            except ValueError as exc:
                return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
        if path.startswith("/admin/instances/"):
            instance_id = path.rsplit("/", 1)[-1]
            result = service.delete_instance(
                instance_id=instance_id,
                purge_files=bool(payload.get("purge_files")),
            )
            return HTTPStatus.OK, result
        if path.startswith("/admin/schedules/"):
            job_id = path.rsplit("/", 1)[-1]
            instance_id = payload.get("instance_id") or "default"
            result = service.delete_schedule(instance_id=instance_id, job_id=job_id)
            return HTTPStatus.OK, result
        if path.startswith("/admin/mcp/servers/"):
            server_name = path.rsplit("/", 1)[-1]
            instance_id = payload.get("instance_id") or "default"
            instance = service.delete_mcp_server(instance_id=instance_id, server_name=server_name)
            return HTTPStatus.OK, {"instance": instance}
        if path.startswith("/admin/mobile/devices/"):
            device_id = path.rsplit("/", 1)[-1]
            instance_id = payload.get("instance_id")
            if not instance_id:
                return HTTPStatus.BAD_REQUEST, {"error": "instance_id required"}
            return HTTPStatus.OK, service.delete_mobile_device(instance_id, device_id)
    except ValueError as exc:
        return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
    return HTTPStatus.NOT_FOUND, {"error": "Not found"}


def resolve_admin_post(
    service: AdminService, raw_path: str, payload: dict[str, Any]
) -> tuple[HTTPStatus, Any]:
    """Resolve one POST request path into a JSON payload."""
    parsed = urlparse(raw_path)
    path = parsed.path.rstrip("/") or "/"
    try:
        # Mobile API (No heavy auth check here for initial pairing/registration)
        if path == "/admin/mobile/pair":
            instance_id = payload.get("instance_id")
            if not instance_id:
                return HTTPStatus.BAD_REQUEST, {"error": "instance_id is required"}
            return HTTPStatus.OK, service.get_mobile_pairing_data(instance_id)
            
        if path == "/admin/mobile/register":
            instance_id = payload.get("instance_id")
            device_id = payload.get("device_id")
            if not instance_id or not device_id:
                return HTTPStatus.BAD_REQUEST, {"error": "instance_id and device_id are required"}
            pairing_token = payload.get("pairing_token")
            label = payload.get("label", "")
            return HTTPStatus.OK, service.register_mobile_client(instance_id, device_id, pairing_token, label)
            
        if path == "/admin/mobile/message":
            instance_id = payload.get("instance_id")
            sender_id = payload.get("sender_id")
            text = payload.get("text")
            if not all([instance_id, sender_id, text]):
                return HTTPStatus.BAD_REQUEST, {"error": "instance_id, sender_id, and text are required"}
            attachments = payload.get("attachments")
            if attachments is not None and not isinstance(attachments, list):
                return HTTPStatus.BAD_REQUEST, {"error": "attachments must be a list"}
            return HTTPStatus.OK, service.relay_mobile_message(
                instance_id,
                sender_id,
                text,
                session_id=payload.get("session_id"),
                message_id=payload.get("message_id"),
                reply_to=payload.get("reply_to"),
                thread_root_id=payload.get("thread_root_id"),
                attachments=attachments,
            )

        if path == "/admin/mobile/transfer-session/create":
            device = payload.get("device")
            if not isinstance(device, dict):
                return HTTPStatus.BAD_REQUEST, {"error": "device is required"}
            return HTTPStatus.OK, service.create_mobile_session_transfer(
                device=device,
                active_session_id=payload.get("active_session_id"),
                conversations=payload.get("conversations"),
            )

        if path == "/admin/mobile/transfer-session/consume":
            transfer_token = str(payload.get("transfer_token") or "").strip()
            if not transfer_token:
                return HTTPStatus.BAD_REQUEST, {"error": "transfer_token is required"}
            return HTTPStatus.OK, service.consume_mobile_session_transfer(transfer_token=transfer_token)

        if path == "/admin/mobile/push/subscribe":
            instance_id = payload.get("instance_id")
            device_id = payload.get("device_id")
            subscription = payload.get("subscription")
            if not instance_id or not device_id or not isinstance(subscription, dict):
                return HTTPStatus.BAD_REQUEST, {"error": "instance_id, device_id, and subscription are required"}
            return HTTPStatus.OK, service.subscribe_mobile_push(
                instance_id=instance_id,
                device_id=device_id,
                subscription=subscription,
            )

        if path == "/admin/mobile/push/unsubscribe":
            instance_id = payload.get("instance_id")
            device_id = payload.get("device_id")
            if not instance_id or not device_id:
                return HTTPStatus.BAD_REQUEST, {"error": "instance_id and device_id are required"}
            return HTTPStatus.OK, service.unsubscribe_mobile_push(instance_id=instance_id, device_id=device_id)

        # ngrok management (requires admin session — NOT in mobile unauthenticated block)
        if path == "/admin/mobile/ngrok/start":
            port = int(payload.get("port") or (service.admin_port if hasattr(service, "admin_port") else 18880))
            return HTTPStatus.OK, service.start_ngrok(port)
        if path == "/admin/security/policies/global/validate":
            policy = payload.get("policy")
            if not isinstance(policy, dict):
                return HTTPStatus.BAD_REQUEST, {"error": "Missing policy payload"}
            return HTTPStatus.OK, service.validate_global_policy(policy=policy)

        if path == "/admin/auth/bootstrap":
            user = service.bootstrap_admin_user(
                username=str(payload.get("username") or ""),
                password=str(payload.get("password") or ""),
                display_name=payload.get("display_name"),
                email=payload.get("email"),
            )
            return HTTPStatus.OK, {"user": user}
        if path == "/admin/auth/login":
            result = service.authenticate_admin_user(
                login=str(payload.get("login") or ""),
                password=str(payload.get("password") or ""),
            )
            return HTTPStatus.OK, result
        if path == "/admin/users":
            result = service.create_admin_user(
                username=str(payload.get("username") or ""),
                password=str(payload.get("password") or ""),
                display_name=payload.get("display_name"),
                email=payload.get("email"),
                role=str(payload.get("role") or "viewer"),
                status=str(payload.get("status") or "active"),
            )
            return HTTPStatus.OK, result
        if path.startswith("/admin/users/") and path.endswith("/reset-password"):
            user_id = path.split("/")[-2]
            result = service.reset_admin_user_password(
                user_id=user_id,
                new_password=str(payload.get("new_password") or ""),
            )
            return HTTPStatus.OK, result
        if path == "/admin/instances":
            result = service.create_instance(
                instance_id=payload.get("instance_id") or "",
                name=payload.get("name") or "",
                owner=payload.get("owner") or "",
                env=payload.get("env") or "prod",
                repo_root=payload.get("repo_root") or ".",
                nanobot_bin=payload.get("nanobot_bin") or "/opt/anaconda3/bin/nanobot",
                source_config=payload.get("source_config"),
                gateway_port=payload.get("gateway_port"),
                runtime_mode=payload.get("runtime_mode"),
                sandbox_profile=payload.get("sandbox_profile") or "balanced",
                sandbox_image=payload.get("sandbox_image"),
                sandbox_execution_strategy=payload.get("sandbox_execution_strategy"),
                sandbox_cpu_limit=payload.get("sandbox_cpu_limit"),
                sandbox_memory_limit=payload.get("sandbox_memory_limit"),
                sandbox_pids_limit=payload.get("sandbox_pids_limit"),
                sandbox_tmpfs_size_mb=payload.get("sandbox_tmpfs_size_mb"),
                sandbox_network_policy=payload.get("sandbox_network_policy"),
                sandbox_timeout_seconds=payload.get("sandbox_timeout_seconds"),
                force=bool(payload.get("force")),
            )
            return HTTPStatus.OK, result
        if path.startswith("/admin/instances/") and path.endswith(("/start", "/stop", "/restart")):
            parts = path.split("/")
            instance_id = parts[-2]
            action = parts[-1]
            result = service.execute_instance_action(instance_id=instance_id, action=action)
            status = HTTPStatus.OK if result["ok"] else HTTPStatus.BAD_GATEWAY
            return status, result
        if path.startswith("/admin/providers/") and path.endswith("/validate"):
            provider_name = path.split("/")[-2]
            instance_id = payload.get("instance_id") or "default"
            result = service.validate_provider(instance_id=instance_id, provider_name=provider_name)
            return HTTPStatus.OK, result
        if path.startswith("/admin/mcp/servers/") and path.endswith("/validate"):
            server_name = path.split("/")[-2]
            instance_id = payload.get("instance_id") or "default"
            result = service.validate_mcp_server(instance_id=instance_id, server_name=server_name)
            return HTTPStatus.OK, result
        if path == "/admin/schedules":
            instance_id = payload.get("instance_id") or "default"
            schedule = payload.get("schedule")
            if not isinstance(schedule, dict):
                return HTTPStatus.BAD_REQUEST, {"error": "Missing schedule payload"}
            result = service.create_schedule(
                instance_id=instance_id,
                name=payload.get("name", ""),
                schedule_data=schedule,
                message=payload.get("message", ""),
                deliver=bool(payload.get("deliver")),
                channel=payload.get("channel"),
                to=payload.get("to"),
                delete_after_run=bool(payload.get("delete_after_run")),
            )
            return HTTPStatus.OK, result
        if path.startswith("/admin/schedules/") and path.endswith("/run"):
            job_id = path.split("/")[-2]
            instance_id = payload.get("instance_id") or "default"
            result = service.run_schedule(instance_id=instance_id, job_id=job_id, force=True)
            return HTTPStatus.OK, result
        if path == "/admin/access-requests/approve":
            result = service.approve_access_request(
                instance_id=payload.get("instance_id") or "default",
                channel_name=payload.get("channel_name") or "",
                sender_id=payload.get("sender_id") or "",
            )
            return HTTPStatus.OK, result
        if path == "/admin/access-requests/reject":
            result = service.reject_access_request(
                instance_id=payload.get("instance_id") or "default",
                channel_name=payload.get("channel_name") or "",
                sender_id=payload.get("sender_id") or "",
            )
            return HTTPStatus.OK, result
    except PermissionError as exc:
        return HTTPStatus.FORBIDDEN, {"error": str(exc)}
    except ValueError as exc:
        return HTTPStatus.BAD_REQUEST, {"error": str(exc)}
    return HTTPStatus.NOT_FOUND, {"error": "Not found"}


def resolve_static_asset(raw_path: str) -> tuple[Path | None, str]:
    """Resolve a static UI asset path."""
    parsed = urlparse(raw_path)
    path = parsed.path.rstrip("/") or "/"

    if path == "/":
        return STATIC_DIR / "index.html", "text/html; charset=utf-8"
    if path == "/static/styles.css":
        return STATIC_DIR / "styles.css", "text/css; charset=utf-8"
    if path == "/static/app.js":
        return STATIC_DIR / "app.js", "application/javascript; charset=utf-8"
    if path == "/static/Logo_Softnix.png":
        return SOFTNIX_LOGIN_LOGO, "image/png"
    if path == "/static/softnix-logo-white.png":
        return SOFTNIX_WHITE_LOGO, "image/png"
    if path == "/favicon.ico":
        return SOFTNIX_WHITE_LOGO, "image/png"
    # Mobile web app
    if path == "/mobile" or path.startswith("/mobile/"):
        subpath = path[len("/mobile"):].lstrip("/") or "index.html"
        asset = STATIC_DIR / "mobile" / subpath
        if asset.exists() and asset.is_file():
            ext = asset.suffix.lstrip(".")
            ct = {"html": "text/html", "js": "application/javascript", "css": "text/css",
                  "json": "application/json", "png": "image/png"}.get(ext, "application/octet-stream")
            return asset, f"{ct}; charset=utf-8" if ext != "png" else ct
    return None, ""


def _match_permission(method: str, path: str) -> str | None:
    if not path.startswith("/admin/"):
        return None
    if path in {"/admin/auth/me", "/admin/auth/login", "/admin/auth/logout", "/admin/auth/bootstrap", "/admin/auth/bootstrap-status"}:
        return None
    if method == "GET":
        if path in {"/admin/health", "/admin/overview"}:
            return "overview.read"
        if path in {"/admin/activity", "/admin/activity/debug", "/admin/analytics/activity-heatmap"}:
            return "activity.read"
        if path == "/admin/access-requests":
            return "access_request.review"
        if path == "/admin/schedules":
            return "schedule.read"
        if path == "/admin/channels":
            return "channel.read"
        if path == "/admin/providers":
            return "provider.read"
        if path == "/admin/mcp/servers":
            return "mcp.read"
        if path == "/admin/security":
            return "security.read"
        if path.startswith("/admin/security/policies/global"):
            return "security.read"
        if path == "/admin/auth-audit":
            return "security.read"
        if path == "/admin/runtime-audit":
            return "runtime_audit.read"
        if path == "/admin/users":
            return "user.read"
        if path == "/admin/instances" or path.startswith("/admin/instances/"):
            if path.endswith("/config"):
                return "config.read"
            if path.endswith("/memory-files"):
                return "memory.read"
            if path.endswith("/skills") or "/skills/" in path:
                return "skills.read"
            return "instance.read"
    if method == "PATCH":
        if path.startswith("/admin/users/"):
            return "user.update"
        if path.endswith("/memory-files"):
            return "memory.update"
        if "/skills/" in path:
            return "skills.update"
        if path.endswith("/config"):
            return "config.update"
        if path.startswith("/admin/instances/"):
            return "instance.update"
        if path.startswith("/admin/providers/"):
            return "provider.update"
        if path.startswith("/admin/channels/"):
            return "channel.update"
        if path == "/admin/security/workspace-restriction":
            return "config.update"
        if path == "/admin/security/policies/global":
            return "security.update"
        if path == "/admin/mcp/servers":
            return "mcp.update"
        if path.startswith("/admin/schedules/") and path.endswith("/enabled"):
            return "schedule.update"
        if path == "/admin/auth/change-password":
            return "__self__"
    if method == "DELETE":
        if "/skills/" in path:
            return "skills.delete"
    if method == "POST":
        if path == "/admin/mobile/ngrok/start":
            return "config.update"
        if path == "/admin/users":
            return "user.create"
        if path.startswith("/admin/users/") and path.endswith("/reset-password"):
            return "user.update"
        if path == "/admin/instances":
            return "instance.create"
        if path.startswith("/admin/instances/") and path.endswith(("/start", "/stop", "/restart")):
            return "instance.control"
        if path.startswith("/admin/providers/") and path.endswith("/validate"):
            return "provider.read"
        if path.startswith("/admin/mcp/servers/") and path.endswith("/validate"):
            return "mcp.read"
        if path == "/admin/schedules":
            return "schedule.update"
        if path.startswith("/admin/schedules/") and path.endswith("/run"):
            return "schedule.run"
        if path == "/admin/security/policies/global/validate":
            return "security.update"
        if path in {"/admin/access-requests/approve", "/admin/access-requests/reject"}:
            return "access_request.review"
    if method == "DELETE":
        if path.startswith("/admin/instances/"):
            return "instance.delete"
        if path.startswith("/admin/schedules/"):
            return "schedule.update"
        if path.startswith("/admin/mcp/servers/"):
            return "mcp.update"
    return None


def create_admin_server(host: str, port: int, service: AdminService) -> ThreadingHTTPServer:
    """Create a threaded HTTP server for the admin API."""

    class AdminHandler(BaseHTTPRequestHandler):
        server_version = "nanobot-admin/1.0"

        def do_GET(self) -> None:  # noqa: N802
            try:
                asset_path, content_type = resolve_static_asset(self.path)
                if asset_path is not None:
                    return self._send_file(asset_path, content_type)
                if self._handle_auth_get():
                    return
                context = self._require_access("GET")
                if context is None:
                    return
                self._set_audit_context(context)
                status, payload = resolve_admin_get(service, self.path)
                if isinstance(payload, dict) and "_file_path" in payload:
                    return self._send_file(Path(str(payload["_file_path"])), str(payload.get("_content_type") or "application/octet-stream"))
                self._send_json(payload, status=status)
            finally:
                clear_request_audit_context()

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self._write_headers("application/json")
            self.end_headers()

        def do_PATCH(self) -> None:  # noqa: N802
            try:
                payload = self._read_json_body()
                if payload is None:
                    return self._send_json({"error": "Invalid JSON body"}, status=HTTPStatus.BAD_REQUEST)
                if self._handle_change_password(payload):
                    return
                context = self._require_access("PATCH", payload=payload)
                if context is None:
                    return
                self._set_audit_context(context)
                if not self._authorize_user_mutation(method="PATCH", path=self.path, payload=payload, context=context):
                    return
                status, response = resolve_admin_patch(service, self.path, payload)
                self._send_json(response, status=status)
            finally:
                clear_request_audit_context()

        def do_DELETE(self) -> None:  # noqa: N802
            try:
                payload = self._read_json_body()
                if payload is None:
                    return self._send_json({"error": "Invalid JSON body"}, status=HTTPStatus.BAD_REQUEST)
                context = self._require_access("DELETE", payload=payload)
                if context is None:
                    return
                self._set_audit_context(context)
                status, response = resolve_admin_delete(service, self.path, payload)
                self._send_json(response, status=status)
            finally:
                clear_request_audit_context()

        def do_POST(self) -> None:  # noqa: N802
            try:
                payload = self._read_json_body()
                if payload is None:
                    return self._send_json({"error": "Invalid JSON body"}, status=HTTPStatus.BAD_REQUEST)
                if self._handle_auth_post(payload):
                    return
                context = self._require_access("POST", payload=payload)
                if context is None:
                    return
                self._set_audit_context(context)
                _mobile_unauthenticated = self.path.startswith("/admin/mobile/") and not self.path.rstrip("/").endswith("/pair")
                if not _mobile_unauthenticated and not self._authorize_user_mutation(method="POST", path=self.path, payload=payload, context=context):
                    return
                status, response = resolve_admin_post(service, self.path, payload)
                self._send_json(response, status=status)
            finally:
                clear_request_audit_context()

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _handle_auth_get(self) -> bool:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path != "/admin/auth/me":
                return False
            context = self._auth_context()
            payload = {
                "authenticated": context is not None,
                "bootstrap_required": not service.has_admin_users(),
                "user": context["user"] if context else None,
                "session": context["session"] if context else None,
            }
            self._send_json(payload, status=HTTPStatus.OK)
            return True

        def _handle_auth_post(self, payload: dict[str, Any]) -> bool:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/admin/auth/bootstrap":
                status, response = resolve_admin_post(service, self.path, payload)
                if status != HTTPStatus.OK:
                    return self._send_json(response, status=status) is None
                login_result = service.authenticate_admin_user(
                    login=str(payload.get("username") or ""),
                    password=str(payload.get("password") or ""),
                    ip=self.client_address[0] if self.client_address else None,
                    user_agent=self.headers.get("User-Agent"),
                )
                self._send_json(
                    {
                        "bootstrap_required": False,
                        "authenticated": True,
                        "user": login_result["user"],
                        "session": login_result["session"],
                    },
                    status=HTTPStatus.OK,
                    extra_headers={"Set-Cookie": self._build_session_cookie(str(login_result["session"]["id"]))},
                )
                return True
            if path == "/admin/auth/login":
                try:
                    result = service.authenticate_admin_user(
                        login=str(payload.get("login") or ""),
                        password=str(payload.get("password") or ""),
                        ip=self.client_address[0] if self.client_address else None,
                        user_agent=self.headers.get("User-Agent"),
                    )
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return True
                self._send_json(
                    {
                        "authenticated": True,
                        "user": result["user"],
                        "session": result["session"],
                    },
                    status=HTTPStatus.OK,
                    extra_headers={"Set-Cookie": self._build_session_cookie(str(result["session"]["id"]))},
                )
                return True
            if path == "/admin/auth/logout":
                context = self._auth_context()
                if context is not None:
                    expected = str(context["session"].get("csrf_token") or "")
                    provided = str(self.headers.get("X-CSRF-Token") or "")
                    if expected and expected != provided:
                        self._send_json({"error": "Invalid CSRF token"}, status=HTTPStatus.FORBIDDEN)
                        return True
                    service.logout_admin_session(session_id=str(context["session"]["id"]))
                self._send_json(
                    {"ok": True},
                    status=HTTPStatus.OK,
                    extra_headers={"Set-Cookie": self._clear_session_cookie()},
                )
                return True
            return False

        def _handle_change_password(self, payload: dict[str, Any]) -> bool:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path != "/admin/auth/change-password":
                return False
            context = self._require_access("PATCH", payload=payload)
            if context is None:
                return True
            try:
                result = service.change_admin_password(
                    user_id=str(context["user"]["id"]),
                    current_password=str(payload.get("current_password") or ""),
                    new_password=str(payload.get("new_password") or ""),
                )
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return True
            self._send_json(
                result,
                status=HTTPStatus.OK,
                extra_headers={"Set-Cookie": self._clear_session_cookie()},
            )
            return True

        def _authorize_user_mutation(
            self,
            *,
            method: str,
            path: str,
            payload: dict[str, Any],
            context: dict[str, Any],
        ) -> bool:
            parsed = urlparse(path)
            normalized_path = parsed.path.rstrip("/") or "/"
            current_role = normalize_role(str(context["user"].get("role") or "viewer"))
            if normalized_path == "/admin/users":
                target_role = normalize_role(str(payload.get("role") or "viewer"))
                if target_role == "owner" and current_role != "owner":
                    service.auth_store.append_audit(
                        event_type="auth.forbidden",
                        outcome="denied",
                        category="authorization",
                        payload={"reason": "only_owners_can_create_owners", "target_role": target_role},
                    )
                    self._send_json({"error": "Only owners can create owners"}, status=HTTPStatus.FORBIDDEN)
                    return False
                return True
            if normalized_path.startswith("/admin/users/"):
                user_id = normalized_path.split("/")[-2] if normalized_path.endswith("/reset-password") else normalized_path.rsplit("/", 1)[-1]
                target = service.auth_store.get_user_by_id(user_id)
                target_role = normalize_role(str((target or {}).get("role") or "viewer"))
                next_role = normalize_role(str(payload.get("role") or target_role))
                next_status = str(payload.get("status") or "").strip().lower()
                if (target_role == "owner" or next_role == "owner") and current_role != "owner":
                    service.auth_store.append_audit(
                        event_type="auth.forbidden",
                        outcome="denied",
                        category="authorization",
                        payload={"reason": "only_owners_can_manage_owners", "target_user_id": user_id},
                    )
                    self._send_json({"error": "Only owners can manage owner accounts"}, status=HTTPStatus.FORBIDDEN)
                    return False
                if next_status == "disabled" and not has_permission(current_role, "user.disable"):
                    service.auth_store.append_audit(
                        event_type="auth.forbidden",
                        outcome="denied",
                        category="authorization",
                        payload={"reason": "insufficient_permission", "required": "user.disable", "target_user_id": user_id},
                    )
                    self._send_json({"error": "You do not have permission to disable users"}, status=HTTPStatus.FORBIDDEN)
                    return False
            return True

        def _require_access(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            required_permission = _match_permission(method, path)
            if required_permission is None and not path.startswith("/admin/"):
                return {}
            context = self._auth_context()
            if required_permission is None:
                return context or {}
            ip = self.client_address[0] if self.client_address else None
            user_agent = self.headers.get("User-Agent")
            if context is None:
                service.auth_store.append_audit(
                    event_type="auth.unauthorized",
                    outcome="denied",
                    category="authentication",
                    actor={"ip": ip, "user_agent": (user_agent or "")[:300] or None},
                    payload={"method": method, "path": path},
                )
                self._send_json({"error": "Authentication required"}, status=HTTPStatus.UNAUTHORIZED)
                return None
            if method in {"PATCH", "POST", "DELETE"} and path not in {"/admin/auth/login", "/admin/auth/bootstrap"}:
                expected = str(context["session"].get("csrf_token") or "")
                provided = str(self.headers.get("X-CSRF-Token") or "")
                if not expected or expected != provided:
                    service.auth_store.append_audit(
                        event_type="auth.csrf_failed",
                        outcome="denied",
                        category="authentication",
                        actor={
                            "user_id": context["user"].get("id"),
                            "username": context["user"].get("username"),
                            "role": context["user"].get("role"),
                            "ip": ip,
                            "user_agent": (user_agent or "")[:300] or None,
                        },
                        payload={"method": method, "path": path},
                    )
                    self._send_json({"error": "Invalid CSRF token"}, status=HTTPStatus.FORBIDDEN)
                    return None
            if required_permission != "__self__" and not has_permission(str(context["user"].get("role") or ""), required_permission):
                service.auth_store.append_audit(
                    event_type="auth.forbidden",
                    outcome="denied",
                    category="authorization",
                    actor={
                        "user_id": context["user"].get("id"),
                        "username": context["user"].get("username"),
                        "role": context["user"].get("role"),
                        "ip": ip,
                        "user_agent": (user_agent or "")[:300] or None,
                    },
                    payload={"method": method, "path": path, "required_permission": required_permission},
                )
                self._send_json({"error": "Forbidden"}, status=HTTPStatus.FORBIDDEN)
                return None
            return context

        def _auth_context(self) -> dict[str, Any] | None:
            session_id = self._read_session_cookie()
            if not session_id:
                return None
            return service.get_authenticated_user(session_id=session_id)

        def _set_audit_context(self, context: dict[str, Any]) -> None:
            user = context.get("user") or {}
            ip = self.client_address[0] if self.client_address else None
            set_request_audit_context(
                user_id=str(user.get("id") or ""),
                username=str(user.get("username") or ""),
                role=str(user.get("role") or ""),
                ip=ip,
                user_agent=self.headers.get("User-Agent"),
            )

        def _read_session_cookie(self) -> str | None:
            raw_cookie = self.headers.get("Cookie") or ""
            if not raw_cookie:
                return None
            cookie = SimpleCookie()
            try:
                cookie.load(raw_cookie)
            except Exception:
                return None
            morsel = cookie.get(ADMIN_SESSION_COOKIE)
            if morsel is None:
                return None
            value = str(morsel.value or "").strip()
            return value or None

        def _build_session_cookie(self, session_id: str) -> str:
            return f"{ADMIN_SESSION_COOKIE}={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800"

        def _clear_session_cookie(self) -> str:
            return f"{ADMIN_SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"

        def _send_json(
            self,
            payload: Any,
            status: HTTPStatus = HTTPStatus.OK,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._write_headers("application/json; charset=utf-8", len(body))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path, content_type: str) -> None:
            if not path.exists():
                return self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self._write_headers(content_type, len(body))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return None
            try:
                raw = self.rfile.read(length) if length > 0 else b"{}"
                data = json.loads(raw.decode("utf-8"))
                return data if isinstance(data, dict) else None
            except Exception:
                return None

        def _write_headers(self, content_type: str, content_length: int | None = None) -> None:
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-CSRF-Token")
            if content_length is not None:
                self.send_header("Content-Length", str(content_length))

    return ThreadingHTTPServer((host, port), AdminHandler)
