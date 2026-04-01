import base64
import io
import json
import signal
import subprocess
import threading
import zipfile
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import AsyncMock, patch

from http import HTTPStatus

from nanobot.admin.server import (
    create_admin_server,
    _read_file_response,
    resolve_admin_delete,
    resolve_admin_get,
    resolve_admin_patch,
    resolve_admin_post,
    resolve_static_asset,
)
from nanobot.admin.auth import has_permission, permissions_for_role, hash_password
from nanobot.admin.service import AdminService
from nanobot.channels.access_requests import AccessRequestStore
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config, MCPServerConfig
from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule
from nanobot.runtime.audit import RuntimeAuditLogger
from nanobot.session.manager import SessionManager


def _request_json(
    port: int,
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict, dict[str, str]]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    body = None if payload is None else json.dumps(payload)
    request_headers = dict(headers or {})
    if body is not None:
        request_headers.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    raw = response.read().decode("utf-8")
    data = json.loads(raw) if raw else {}
    response_headers = {key: value for key, value in response.getheaders()}
    connection.close()
    return response.status, data, response_headers


def _request_raw(
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes, dict[str, str]]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    request_headers = dict(headers or {})
    connection.request(method, path, headers=request_headers)
    response = connection.getresponse()
    body = response.read()
    response_headers = {key: value for key, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, body, response_headers


def _start_admin_server(service: AdminService) -> tuple[object, threading.Thread, int]:
    server = create_admin_server("127.0.0.1", 0, service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, int(server.server_address[1])


def test_admin_service_overview_collects_workspace_state(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.channels.telegram.enabled = True
    config.channels.telegram.allow_from = ["*"]
    config.tools.restrict_to_workspace = True
    config.tools.mcp_servers["docs"] = MCPServerConfig(
        type="streamableHttp",
        url="https://example.com/mcp",
        tool_timeout=15,
    )
    save_config(config, config_path)

    session = SessionManager(workspace).get_or_create("telegram:owner")
    session.add_message("user", "hello")
    SessionManager(workspace).save(session)

    cron = CronService(workspace / "cron" / "jobs.json")
    cron.add_job(
        name="heartbeat check",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="run",
    )

    service = AdminService(config_path=config_path)
    overview = service.get_overview()

    assert overview["summary"]["instance_count"] == 1
    assert overview["summary"]["enabled_channels"] == ["telegram"]
    assert overview["summary"]["session_count"] == 1
    assert overview["summary"]["cron_job_count"] == 1
    assert overview["summary"]["mcp_server_count"] == 1

    instance = overview["instances"][0]
    assert instance["selected_provider"] is None
    assert instance["runtime"]["status"] == "unmanaged"
    assert instance["sessions"]["count"] == 1


def test_admin_service_health_includes_git_commit(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    health = service.get_health()

    expected_commit = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
    ).strip()

    assert health["commit"] == expected_commit
    assert health["service"] == "nanobot-admin"


def test_admin_service_health_prefers_build_commit_env(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    monkeypatch.setenv("SOFTNIX_BUILD_COMMIT", "deadbee")
    service = AdminService(config_path=config_path)
    health = service.get_health()

    assert health["commit"] == "deadbee"


def test_admin_role_can_create_instances() -> None:
    permissions = permissions_for_role("admin")
    assert "instance.create" in permissions
    assert has_permission("admin", "instance.create") is True


def test_operator_role_can_manage_assigned_instances() -> None:
    permissions = set(permissions_for_role("operator"))
    assert {
        "instance.update",
        "config.update",
        "memory.update",
        "skills.update",
        "skills.delete",
        "channel.update",
        "provider.update",
        "mcp.update",
        "schedule.update",
    }.issubset(permissions)
    assert "instance.create" not in permissions
    assert "instance.delete" not in permissions
    assert "user.create" not in permissions


def test_admin_server_supports_http_range_requests_for_static_files() -> None:
    path, _ = resolve_static_asset("/favicon.ico")
    assert path is not None

    status, body, headers = _read_file_response(path, "bytes=0-3")

    assert status == HTTPStatus.PARTIAL_CONTENT
    assert headers["Content-Range"].startswith("bytes 0-3/")
    assert headers["Accept-Ranges"] == "bytes"
    assert len(body) == 4


def test_admin_server_returns_forbidden_for_inaccessible_mobile_media() -> None:
    class DummyService:
        def get_mobile_media_file(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise PermissionError("Instance 'bigbike2-prod' is not accessible")

    status, payload = resolve_admin_get(
        DummyService(),
        "/admin/mobile/media?instance_id=bigbike2-prod&sender_id=mob-1&file=out.mp3",
    )

    assert status == HTTPStatus.FORBIDDEN
    assert payload["error"] == "Instance 'bigbike2-prod' is not accessible"


def test_admin_service_transcribes_mobile_audio(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.providers.groq.api_key = "groq-test-key"
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    audio = {
        "name": "voice.webm",
        "type": "audio/webm",
        "data_base64": base64.b64encode(b"fake-audio-bytes").decode("ascii"),
    }

    with patch("nanobot.providers.transcription.GroqTranscriptionProvider.transcribe", new=AsyncMock(return_value="Hello mobile STT")):
        result = service.transcribe_mobile_audio(
            instance_id=service.list_instances()[0]["id"],
            sender_id="mob-1",
            audio=audio,
        )

    assert result["transcript"] == "Hello mobile STT"
    assert result["mime_type"] == "audio/webm"
    assert result["name"] == "voice.webm"


def test_admin_server_resolves_mobile_transcribe_route(tmp_path) -> None:
    class DummyService:
        def transcribe_mobile_audio(self, **kwargs):  # noqa: ANN003
            return {
                "transcript": "hello",
                "name": kwargs["audio"]["name"],
                "mime_type": kwargs["audio"]["type"],
                "size": 123,
            }

    status, payload = resolve_admin_post(
        DummyService(),
        "/admin/mobile/transcribe",
        {
            "instance_id": "bigbike2-prod",
            "sender_id": "mob-1",
            "audio": {"name": "voice.webm", "type": "audio/webm", "data_base64": "ZmFrZQ=="},
        },
        accessible_instance_ids={"bigbike2-prod"},
    )

    assert status == HTTPStatus.OK
    assert payload["transcript"] == "hello"


def test_admin_server_forwards_current_user_id_for_instance_create() -> None:
    class DummyService:
        def create_instance(self, **kwargs):  # noqa: ANN003
            return kwargs

    status, payload = resolve_admin_post(
        DummyService(),
        "/admin/instances",
        {
            "instance_id": "acme-prod",
            "name": "Acme Production",
            "owner": "acme",
            "env": "prod",
            "repo_root": "/tmp",
            "nanobot_bin": "/opt/anaconda3/bin/nanobot",
        },
        current_user_id="user-creator",
        accessible_instance_ids={"default-prod"},
    )

    assert status == HTTPStatus.OK
    assert payload["current_user_id"] == "user-creator"


def test_admin_server_marks_missing_groq_key_on_mobile_transcribe() -> None:
    class DummyService:
        def transcribe_mobile_audio(self, **kwargs):  # noqa: ANN003
            raise ValueError("Groq API key is not configured for transcription")

    status, payload = resolve_admin_post(
        DummyService(),
        "/admin/mobile/transcribe",
        {
            "instance_id": "bigbike2-prod",
            "sender_id": "mob-1",
            "audio": {"name": "voice.webm", "type": "audio/webm", "data_base64": "ZmFrZQ=="},
        },
        accessible_instance_ids={"bigbike2-prod"},
    )

    assert status == HTTPStatus.BAD_REQUEST
    assert payload["error_code"] == "groq_key_missing"


def test_admin_service_exports_skill_archive_as_zip(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "crm-notion"
    (skill_dir / "assets").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: crm-notion\n---\n# CRM\n", encoding="utf-8")
    (skill_dir / "assets" / "helper.txt").write_text("hello", encoding="utf-8")
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    instance_id = service.list_instances()[0]["id"]

    result = service.export_instance_skill_archive(instance_id=instance_id, skill_name="crm-notion")
    archive_path = Path(result["_file_path"])

    assert result["skill_name"] == "crm-notion"
    assert result["_download_name"] == "crm-notion.zip"
    assert archive_path.exists()

    with zipfile.ZipFile(archive_path) as archive:
        names = sorted(archive.namelist())
        assert names == ["crm-notion/SKILL.md", "crm-notion/assets/helper.txt"]


def test_admin_service_imports_skill_archive_from_zip(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    instance_id = service.list_instances()[0]["id"]

    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("demo/SKILL.md", "---\nname: demo\n---\n# Demo\n")
        archive.writestr("demo/resources/snippet.txt", "hello")

    result = service.import_instance_skill_archive(
        instance_id=instance_id,
        archive_name="demo.zip",
        archive_base64=base64.b64encode(archive_bytes.getvalue()).decode("ascii"),
    )

    skill_dir = workspace / "skills" / "demo"
    assert result["skill_name"] == "demo"
    assert result["file_count"] == 2
    assert (skill_dir / "SKILL.md").exists()
    assert (skill_dir / "resources" / "snippet.txt").read_text(encoding="utf-8") == "hello"


def test_admin_server_resolves_skill_download_route(tmp_path) -> None:
    class DummyService:
        def export_instance_skill_archive(self, **kwargs):  # noqa: ANN003
            return {
                "instance_id": kwargs["instance_id"],
                "skill_name": kwargs["skill_name"],
                "_file_path": str(tmp_path / "demo.zip"),
                "_content_type": "application/zip",
                "_download_name": "demo.zip",
            }

    status, payload = resolve_admin_get(DummyService(), "/admin/instances/prod/skills/demo/download")

    assert status == HTTPStatus.OK
    assert payload["_download_name"] == "demo.zip"


def test_admin_server_resolves_skill_import_route(tmp_path) -> None:
    class DummyService:
        def import_instance_skill_archive(self, **kwargs):  # noqa: ANN003
            return kwargs

    status, payload = resolve_admin_post(
        DummyService(),
        "/admin/instances/prod/skills/import",
        {"archive_name": "demo.zip", "archive_base64": base64.b64encode(b"zip").decode("ascii")},
    )

    assert status == HTTPStatus.OK
    assert payload["instance_id"] == "prod"
    assert payload["archive_name"] == "demo.zip"


def test_admin_server_resolves_skill_bank_routes(tmp_path) -> None:
    class DummyService:
        def list_skill_bank(self, **kwargs):  # noqa: ANN003
            return kwargs

        def import_skill_bank_entry(self, **kwargs):  # noqa: ANN003
            return kwargs

    status, payload = resolve_admin_get(DummyService(), "/admin/skills-bank?instance_id=prod")

    assert status == HTTPStatus.OK
    assert payload["instance_id"] == "prod"

    status, payload = resolve_admin_post(
        DummyService(),
        "/admin/instances/prod/skills/bank/import",
        {"bank_skill_id": "engineering-frontend-developer"},
    )

    assert status == HTTPStatus.OK
    assert payload["instance_id"] == "prod"
    assert payload["bank_skill_id"] == "engineering-frontend-developer"


def test_admin_service_reports_security_findings(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.channels.whatsapp.enabled = True
    config.tools.restrict_to_workspace = False
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    security = service.get_security()
    codes = {item["code"] for item in security["findings"]}

    assert "workspace_restriction_disabled" in codes
    assert "missing_whatsapp_bridge_token" in codes
    assert "whatsapp_deny_all" in codes
    assert "runtime_not_sandboxed" in codes
    assert "audit_log_not_initialized" in codes


def test_admin_service_loads_registry_instances(tmp_path) -> None:
    first_workspace = tmp_path / "workspace-1"
    second_workspace = tmp_path / "workspace-2"
    first_workspace.mkdir()
    second_workspace.mkdir()
    first_config = tmp_path / "first.json"
    second_config = tmp_path / "second.json"
    registry_path = tmp_path / "instances.json"

    config1 = Config()
    config1.agents.defaults.workspace = str(first_workspace)
    config1.channels.telegram.enabled = True
    config1.channels.telegram.allow_from = ["user-1"]
    save_config(config1, first_config)

    config2 = Config()
    config2.agents.defaults.workspace = str(second_workspace)
    config2.channels.slack.enabled = True
    config2.channels.slack.allow_from = ["user-2"]
    save_config(config2, second_config)

    registry_path.write_text(
        json.dumps(
            {
                "instances": [
                    {"id": "prod", "name": "Production", "config": str(first_config)},
                    {"id": "staging", "name": "Staging", "config": str(second_config)},
                ]
            }
        ),
        encoding="utf-8",
    )

    service = AdminService(registry_path=registry_path)
    instances = service.list_instances()

    assert [item["id"] for item in instances] == ["prod", "staging"]
    assert instances[0]["channels_enabled"] == ["telegram"]
    assert instances[1]["channels_enabled"] == ["slack"]


def test_admin_service_syncs_workspace_identity_on_init(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"
    registry_path = tmp_path / "instances.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    soul_path = workspace / "SOUL.md"
    soul_path.write_text("I am nanobot 🐈, a personal AI assistant.\n", encoding="utf-8")

    registry_path.write_text(
        json.dumps(
            {
                "instances": [
                    {
                        "id": "prod",
                        "name": "AgenticClaw",
                        "config": str(config_path),
                        "workspace": str(workspace),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    AdminService(registry_path=registry_path)
    assert "I am AgenticClaw 🐈" in soul_path.read_text(encoding="utf-8")


def test_admin_service_manages_registry_instances(tmp_path) -> None:
    registry_path = tmp_path / "admin" / "instances.json"
    service = AdminService(registry_path=registry_path)

    created = service.create_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        repo_root=str(tmp_path),
        nanobot_bin="/opt/anaconda3/bin/nanobot",
    )
    assert created["instance"]["id"] == "acme-prod"
    assert created["instance"]["owner"] == "acme"
    assert created["instance"]["gateway_port"] == 18790
    assert created["instance"]["runtime_config"]["mode"] == "sandbox"

    updated = service.update_instance(
        instance_id="acme-prod",
        name="Acme Production Updated",
        owner="acme-team",
        env="uat",
        runtime_mode="host",
    )
    assert updated["instance"]["name"] == "Acme Production Updated"
    assert updated["instance"]["owner"] == "acme-team"
    assert updated["instance"]["env"] == "uat"
    assert updated["instance"]["runtime_config"]["mode"] == "host"

    deleted = service.delete_instance(instance_id="acme-prod", purge_files=False)
    assert deleted["instance_id"] == "acme-prod"
    audit_log = service.get_auth_audit_log(limit=20)
    assert any(
        event.get("event_type") == "instance.deleted"
        and (event.get("resource") or {}).get("id") == "acme-prod"
        for event in audit_log["events"]
    )


def test_instance_reuse_starts_with_clean_mobile_state(tmp_path) -> None:
    registry_path = tmp_path / "admin" / "instances.json"
    service = AdminService(registry_path=registry_path)

    service.create_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        repo_root=str(tmp_path),
        nanobot_bin="/opt/anaconda3/bin/nanobot",
    )
    pairing = service.get_mobile_pairing_data("acme-prod")
    service.register_mobile_client("acme-prod", "mob-1", pairing.get("pairing_token"), "Tester")
    assert len(service.list_mobile_devices("acme-prod")) == 1

    service.delete_instance(instance_id="acme-prod", purge_files=True)

    recreated = service.create_instance(
        instance_id="acme-prod",
        name="Acme Production Recreated",
        owner="acme",
        env="prod",
        repo_root=str(tmp_path),
        nanobot_bin="/opt/anaconda3/bin/nanobot",
    )

    assert recreated["instance"]["id"] == "acme-prod"
    assert service.list_mobile_devices("acme-prod") == []


def test_admin_service_grants_creator_access_on_instance_create(tmp_path) -> None:
    registry_path = tmp_path / "admin" / "instances.json"
    service = AdminService(registry_path=registry_path)

    creator = service.auth_store.upsert_user(
        {
            "id": "user-creator",
            "username": "datateam",
            "display_name": "Data Team",
            "email": None,
            "role": "admin",
            "status": "active",
            "password_hash": hash_password("password123"),
            "created_at": "2026-03-25T00:00:00+07:00",
            "updated_at": "2026-03-25T00:00:00+07:00",
            "last_login_at": None,
            "instance_ids": ["default-prod"],
        }
    )

    created = service.create_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        repo_root=str(tmp_path),
        nanobot_bin="/opt/anaconda3/bin/nanobot",
        current_user_id=creator["id"],
    )

    assert created["instance"]["id"] == "acme-prod"
    updated_creator = service.auth_store.get_user_by_id(creator["id"])
    assert updated_creator is not None
    assert updated_creator["instance_ids"] == ["default-prod", "acme-prod"]


def test_admin_service_create_instance_applies_fast_profile(tmp_path) -> None:
    registry_path = tmp_path / "admin" / "instances.json"
    service = AdminService(registry_path=registry_path)

    created = service.create_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        repo_root=str(tmp_path),
        nanobot_bin="/opt/anaconda3/bin/nanobot",
        sandbox_profile="fast",
    )

    runtime = created["instance"]["runtime_config"]
    assert runtime["mode"] == "host"
    assert runtime["sandbox"]["profile"] == "fast"
    assert runtime["sandbox"]["executionStrategy"] == "tool_ephemeral"


def test_admin_service_scopes_auth_audit_log_to_user_and_instances(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    service.auth_store.append_audit(
        event_type="auth.login",
        category="authentication",
        outcome="success",
        actor={"user_id": "user-1", "username": "rujirapong", "role": "admin"},
        resource={"type": "session", "id": "session-1"},
    )
    service.auth_store.append_audit(
        event_type="instance.updated",
        category="instance_management",
        outcome="success",
        actor={"user_id": "user-2", "username": "operator", "role": "operator"},
        resource={"type": "instance", "id": "inst-a"},
    )
    service.auth_store.append_audit(
        event_type="security.policy_updated",
        category="configuration",
        outcome="success",
        actor={"user_id": "user-3", "username": "owner", "role": "owner"},
        resource={"type": "session", "id": "session-2"},
        payload={"instance_id": "inst-a", "rule_ids": ["rule-1"]},
    )
    service.auth_store.append_audit(
        event_type="instance.deleted",
        category="instance_management",
        outcome="success",
        actor={"user_id": "user-4", "username": "other", "role": "admin"},
        resource={"type": "instance", "id": "inst-b"},
    )

    restricted = service.get_auth_audit_log(
        limit=20,
        current_user_id="user-1",
        accessible_instance_ids={"inst-a"},
    )
    restricted_event_types = {event["event_type"] for event in restricted["events"]}
    assert "instance.updated" in restricted_event_types
    assert "security.policy_updated" in restricted_event_types
    assert "auth.login" not in restricted_event_types
    assert "instance.deleted" not in restricted_event_types

    mine_only = service.get_auth_audit_log(
        limit=20,
        scope="mine",
        current_user_id="user-1",
        accessible_instance_ids={"inst-a"},
    )
    assert {event["event_type"] for event in mine_only["events"]} == {"auth.login"}

    instance_only = service.get_auth_audit_log(
        limit=20,
        scope="instances",
        current_user_id="user-1",
        accessible_instance_ids={"inst-a"},
    )
    assert {event["event_type"] for event in instance_only["events"]} == {"instance.updated", "security.policy_updated"}

    all_visible = service.get_auth_audit_log(
        limit=20,
        scope="all",
        current_user_id="user-1",
        accessible_instance_ids=None,
    )
    assert {"auth.login", "instance.updated", "security.policy_updated", "instance.deleted"}.issubset(
        {event["event_type"] for event in all_visible["events"]}
    )

    unrestricted = service.get_auth_audit_log(limit=20)
    unrestricted_event_types = {event["event_type"] for event in unrestricted["events"]}
    assert {"auth.login", "instance.updated", "security.policy_updated", "instance.deleted"}.issubset(
        unrestricted_event_types
    )


def test_admin_service_update_instance_reconciles_stale_runtime_artifacts(tmp_path) -> None:
    registry_path = tmp_path / "admin" / "instances.json"
    service = AdminService(registry_path=registry_path)

    created = service.create_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        repo_root=str(tmp_path),
        nanobot_bin="/opt/anaconda3/bin/nanobot",
        sandbox_profile="balanced",
    )
    instance_home = Path(created["instance"]["instance_home"])
    run_dir = instance_home / "run"
    cid_path = run_dir / "gateway.cid"
    cid_path.write_text("stale-container-id", encoding="utf-8")

    updated = service.update_instance(
        instance_id="acme-prod",
        sandbox_profile="fast",
    )
    assert updated["instance"]["runtime_config"]["mode"] == "host"
    assert not cid_path.exists()


def test_admin_service_writes_audit_log_for_instance_changes(tmp_path) -> None:
    registry_path = tmp_path / "admin" / "instances.json"
    service = AdminService(registry_path=registry_path)

    service.create_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        repo_root=str(tmp_path),
        nanobot_bin="/opt/anaconda3/bin/nanobot",
    )
    service.update_instance(
        instance_id="acme-prod",
        name="Acme Production Updated",
        runtime_mode="host",
    )

    audit_path = tmp_path / "admin" / "audit" / "acme-prod.jsonl"
    lines = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [line["event_type"] for line in lines] == ["instance.created", "instance.updated"]


def test_admin_service_reports_network_none_for_connected_workload(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.channels.telegram.enabled = True
    config.channels.telegram.token = "token"
    config.runtime.mode = "sandbox"
    config.runtime.sandbox.network_policy = "none"
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    instance = service.get_instance("default")
    assert instance is not None
    codes = {item["code"] for item in instance["security"]["findings"]}
    assert "sandbox_network_disabled_for_connected_workload" in codes
    assert "sandbox_cpu_limit_missing" in codes
    assert "sandbox_memory_limit_missing" in codes


def test_admin_service_surfaces_runtime_audit_summary_and_findings(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.runtime.mode = "host"
    save_config(config, config_path)

    audit = RuntimeAuditLogger(workspace)
    audit.log_tool_call("exec", {"command": "python -m pip install rich"}, "ok")
    audit.log_tool_call("exec", {"command": "rm -rf /tmp/x"}, "Error: Command blocked by safety guard (dangerous pattern detected)")

    service = AdminService(config_path=config_path)
    instance = service.get_instance("default")
    assert instance is not None

    runtime_audit = instance["runtime_audit"]
    assert runtime_audit["exists"] is True
    assert runtime_audit["exec_count"] == 2
    assert runtime_audit["package_install_count"] == 1
    assert runtime_audit["blocked_count"] == 1

    codes = {item["code"] for item in instance["security"]["findings"]}
    assert "host_mode_exec_activity_detected" in codes
    assert "runtime_package_installs_detected" in codes
    assert "runtime_guard_blocks_detected" in codes


def test_admin_service_runtime_audit_explorer_filters_and_pagination(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    audit = RuntimeAuditLogger(workspace)
    audit.log_tool_call("exec", {"command": "ls -la"}, "ok")
    audit.log_tool_call("read_file", {"path": str(workspace / "SOUL.md")}, "ok")
    audit.log_tool_call("exec", {"command": "rm -rf /tmp/x"}, "Error: Command blocked by safety guard (dangerous pattern detected)")

    service = AdminService(config_path=config_path)
    page1 = service.get_runtime_audit_events(instance_id="default", limit=2)
    assert page1["count"] == 2
    assert page1["filtered_count"] == 3
    assert page1["next_cursor"] is not None
    assert page1["summary"]["event_count"] == 3
    assert page1["summary"]["blocked_count"] == 1

    page2 = service.get_runtime_audit_events(
        instance_id="default",
        limit=2,
        cursor=page1["next_cursor"],
    )
    assert page2["count"] == 1
    assert page2["next_cursor"] is None

    blocked_only = service.get_runtime_audit_events(
        instance_id="default",
        status="error",
    )
    assert blocked_only["filtered_count"] == 1
    assert blocked_only["events"][0]["status"] == "error"

    read_only = service.get_runtime_audit_events(
        instance_id="default",
        operation="file_read",
    )
    assert read_only["filtered_count"] == 1
    assert read_only["events"][0]["operation"] == "file_read"

    search_only = service.get_runtime_audit_events(
        instance_id="default",
        search="soul.md",
    )
    assert search_only["filtered_count"] == 1
    assert "soul.md" in (search_only["events"][0]["path"] or "").lower()


def test_admin_service_assigns_next_available_gateway_port_on_create(tmp_path) -> None:
    registry_path = tmp_path / "admin" / "instances.json"
    service = AdminService(registry_path=registry_path)

    orphan_instance_home = tmp_path / "instances" / "orphan-prod"
    orphan_instance_home.mkdir(parents=True)
    orphan_config_path = orphan_instance_home / "config.json"
    orphan_config = Config()
    orphan_config.gateway.port = 19090
    save_config(orphan_config, orphan_config_path)

    service.create_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        repo_root=str(tmp_path),
        nanobot_bin="/opt/anaconda3/bin/nanobot",
        gateway_port=19089,
    )

    created = service.create_instance(
        instance_id="acme-uat",
        name="Acme UAT",
        owner="acme",
        env="uat",
        repo_root=str(tmp_path),
        nanobot_bin="/opt/anaconda3/bin/nanobot",
        gateway_port=19090,
    )

    assert created["instance"]["gateway_port"] == 19091


def test_admin_service_reads_and_updates_instance_config(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    config_payload = service.get_instance_config(instance_id="default")
    assert config_payload["config"]["agents"]["defaults"]["workspace"] == str(workspace)

    updated = service.update_instance_config(
        instance_id="default",
        config_data={
            **config_payload["config"],
            "agents": {
                **config_payload["config"]["agents"],
                "defaults": {
                    **config_payload["config"]["agents"]["defaults"],
                    "model": "openai/gpt-4.1-mini",
                },
            },
        },
    )
    assert updated["config"]["agents"]["defaults"]["model"] == "openai/gpt-4.1-mini"


def test_admin_service_updates_channel_config(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.channels.telegram.enabled = True
    config.channels.telegram.token = "old-token"
    config.channels.telegram.allow_from = ["user-1"]
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    instance = service.update_channel(
        instance_id="default",
        channel_name="telegram",
        enabled=False,
        allow_from=["user-2", "*"],
        settings={"token": "new-token"},
    )

    telegram = next(item for item in instance["channels"] if item["name"] == "telegram")
    assert telegram["enabled"] is False
    assert telegram["allow_from"] == ["user-2", "*"]
    assert telegram["allow_from_mode"] == "allow_all"
    assert telegram["settings"]["token"] == "new-token"


def test_admin_service_lists_and_approves_access_requests(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.channels.telegram.enabled = True
    config.channels.telegram.allow_from = []
    save_config(config, config_path)

    store = AccessRequestStore(workspace)
    store.record(
        channel="telegram",
        sender_id="8388377631|rujirapongsnsn_bot",
        chat_id="8388377631",
        content="hello",
        metadata={"username": "rujirapongsnsn_bot"},
    )

    service = AdminService(config_path=config_path)
    listed = service.list_access_requests()
    assert listed["count"] == 1
    assert listed["requests"][0]["sender_id"] == "8388377631|rujirapongsnsn_bot"

    result = service.approve_access_request(
        instance_id="default",
        channel_name="telegram",
        sender_id="8388377631|rujirapongsnsn_bot",
    )
    assert result["approved"]["allow_item"] == "8388377631"
    assert result["runtime"]["applied"] is False

    updated = load_config(config_path)
    assert "8388377631" in updated.channels.telegram.allow_from

    listed_after = service.list_access_requests()
    assert listed_after["count"] == 0


def test_admin_service_approve_access_request_applies_runtime_signal(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"
    instance_home = tmp_path / "instance-home"
    (instance_home / "run").mkdir(parents=True)
    (instance_home / "run" / "gateway.pid").write_text("4242\n", encoding="utf-8")
    registry_path = tmp_path / "instances.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.channels.telegram.enabled = True
    config.channels.telegram.allow_from = []
    save_config(config, config_path)

    registry_path.write_text(
        json.dumps(
            {
                "instances": [
                    {
                        "id": "prod",
                        "name": "Production",
                        "config": str(config_path),
                        "workspace": str(workspace),
                        "instance_home": str(instance_home),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    store = AccessRequestStore(workspace)
    store.record(
        channel="telegram",
        sender_id="8388377631",
        chat_id="8388377631",
        content="hello",
        metadata={},
    )

    service = AdminService(registry_path=registry_path)
    with patch("nanobot.admin.service.os.kill") as kill_mock:
        kill_mock.side_effect = [None, None]
        result = service.approve_access_request(
            instance_id="prod",
            channel_name="telegram",
            sender_id="8388377631",
        )

    assert result["runtime"]["applied"] is True
    assert result["runtime"]["method"] == "sighup"
    assert kill_mock.call_count == 2
    assert kill_mock.call_args_list[0].args == (4242, 0)
    assert kill_mock.call_args_list[1].args == (4242, signal.SIGHUP)


def test_admin_service_approve_access_request_applies_runtime_signal_for_sandbox(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"
    registry_path = tmp_path / "instances.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.channels.telegram.enabled = True
    config.channels.telegram.allow_from = []
    config.runtime.mode = "sandbox"
    save_config(config, config_path)

    registry_path.write_text(
        json.dumps(
            {
                "instances": [
                    {
                        "id": "prod",
                        "name": "Production",
                        "config": str(config_path),
                        "workspace": str(workspace),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    store = AccessRequestStore(workspace)
    store.record(
        channel="telegram",
        sender_id="8388377631",
        chat_id="8388377631",
        content="hello",
        metadata={},
    )

    service = AdminService(registry_path=registry_path)
    with patch("nanobot.admin.service.subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=["docker", "kill", "--signal", "HUP", "softnix-prod-gateway"],
            returncode=0,
            stdout="softnix-prod-gateway\n",
            stderr="",
        )
        result = service.approve_access_request(
            instance_id="prod",
            channel_name="telegram",
            sender_id="8388377631",
        )

    assert result["runtime"]["applied"] is True
    assert result["runtime"]["method"] == "docker-signal"


def test_admin_service_updates_instance_config_syncs_runtime_scripts(tmp_path) -> None:
    registry_path = tmp_path / "admin" / "instances.json"
    service = AdminService(registry_path=registry_path)
    created = service.create_instance(
        instance_id="acme-prod",
        name="Acme Production",
        owner="acme",
        env="prod",
        repo_root=str(tmp_path),
        nanobot_bin="/opt/anaconda3/bin/nanobot",
    )

    raw = service.get_instance_config(instance_id="acme-prod")["config"]
    raw["runtime"]["mode"] = "host"
    updated = service.update_instance_config(instance_id="acme-prod", config_data=raw)

    start_script = (
        tmp_path / "instances" / "acme-prod" / "scripts" / "start.sh"
    ).read_text(encoding="utf-8")
    assert updated["instance"]["runtime_config"]["mode"] == "host"
    assert 'nohup "$NANOBOT" gateway' in start_script


def test_admin_service_updates_workspace_restriction(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.tools.restrict_to_workspace = False
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    instance = service.update_workspace_restriction(
        instance_id="default",
        restrict_to_workspace=True,
    )

    assert all(
        finding["code"] != "workspace_restriction_disabled"
        for finding in instance["security"]["findings"]
    )


def test_admin_service_updates_provider_defaults_and_config(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    defaults_result = service.update_provider_defaults(
        instance_id="default",
        model="openai/gpt-4.1-mini",
        provider="openai",
    )
    instance = defaults_result["instance"]
    assert instance["model"] == "openai/gpt-4.1-mini"
    assert defaults_result["instance_restart"]["attempted"] is False

    instance = service.update_provider_config(
        instance_id="default",
        provider_name="openai",
        api_key="sk-test-1234",
        api_base="https://api.example.com/v1",
        extra_headers={"X-App": "softnix"},
    )
    openai = next(item for item in instance["providers"] if item["name"] == "openai")
    assert openai["configured"] is True
    assert openai["api_base"] == "https://api.example.com/v1"
    assert openai["extra_headers"] == {"X-App": "softnix"}


def test_admin_service_exposes_effective_api_base_for_providers(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    instance = service.list_instances()[0]
    openrouter = next(item for item in instance["providers"] if item["name"] == "openrouter")

    assert openrouter["api_base"] == ""
    assert openrouter["api_base_effective"] == "https://openrouter.ai/api/v1"
    assert openrouter["api_base_default"] == "https://openrouter.ai/api/v1"


def test_admin_service_upserts_and_deletes_mcp_server(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    instance = service.upsert_mcp_server(
        instance_id="default",
        server_name="docs",
        server_data={
            "type": "streamableHttp",
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer secret"},
            "tool_timeout": 20,
        },
    )
    assert instance["mcp"]["server_count"] == 1
    assert instance["mcp"]["servers"][0]["headers"] == {"Authorization": "Bearer secret"}

    instance = service.delete_mcp_server(instance_id="default", server_name="docs")
    assert instance["mcp"]["server_count"] == 0


def test_admin_service_validates_provider_and_mcp(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.agents.defaults.model = "openai/gpt-4.1-mini"
    config.agents.defaults.provider = "openai"
    config.providers.openai.api_key = "sk-test-123"
    config.tools.mcp_servers["docs"] = MCPServerConfig(
        type="streamableHttp",
        url="https://example.com/mcp",
        tool_timeout=15,
    )
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    provider_result = service.validate_provider(instance_id="default", provider_name="openai")
    mcp_result = service.validate_mcp_server(instance_id="default", server_name="docs")

    assert provider_result["status"] == "ok"
    assert provider_result["instance_restart"]["attempted"] is False
    assert mcp_result["status"] == "ok"


def test_admin_service_executes_instance_lifecycle_from_registry(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"
    registry_path = tmp_path / "instances.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)
    registry_path.write_text(
        json.dumps(
            {
                "instances": [
                    {
                        "id": "prod",
                        "name": "Production",
                        "config": str(config_path),
                        "cwd": str(tmp_path),
                        "lifecycle": {
                            "start": ["echo", "start-prod"],
                            "stop": ["echo", "stop-prod"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    service = AdminService(registry_path=registry_path)
    with (
        patch("nanobot.admin.service.subprocess.run") as mock_run,
        patch.object(AdminService, "_is_tcp_port_available", return_value=True),
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["echo", "start-prod"],
            returncode=0,
            stdout="started",
            stderr="",
        )
        result = service.execute_instance_action(instance_id="prod", action="start")

    assert result["ok"] is True
    assert result["action"] == "start"
    assert result["stdout"] == "started"
    mock_run.assert_called_once()


def test_admin_service_start_fails_when_gateway_port_is_unavailable(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"
    registry_path = tmp_path / "instances.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.gateway.port = 19990
    save_config(config, config_path)
    registry_path.write_text(
        json.dumps(
            {
                "instances": [
                    {
                        "id": "prod",
                        "name": "Production",
                        "config": str(config_path),
                        "cwd": str(tmp_path),
                        "lifecycle": {
                            "start": ["echo", "start-prod"],
                            "status": ["false"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    service = AdminService(registry_path=registry_path)
    with patch.object(AdminService, "_is_tcp_port_available", return_value=False):
        try:
            service.execute_instance_action(instance_id="prod", action="start")
        except ValueError as exc:
            assert "already in use" in str(exc)
        else:
            raise AssertionError("Expected start to fail when gateway port is unavailable")


def test_admin_service_reports_instance_runtime_status_from_probe(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"
    registry_path = tmp_path / "instances.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)
    registry_path.write_text(
        json.dumps(
            {
                "instances": [
                    {
                        "id": "prod",
                        "name": "Production",
                        "config": str(config_path),
                        "cwd": str(tmp_path),
                        "lifecycle": {
                            "start": ["echo", "start-prod"],
                            "stop": ["echo", "stop-prod"],
                            "status": ["echo", "running"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    service = AdminService(registry_path=registry_path)
    with patch("nanobot.admin.service.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["echo", "running"],
            returncode=0,
            stdout="running",
            stderr="",
        )
        instance = service.get_instance("prod")

    assert instance is not None
    assert instance["runtime"]["status"] == "running"
    assert instance["runtime"]["management"] == "externally_managed"
    assert instance["runtime"]["probe"]["available"] is True
    assert instance["runtime"]["probe"]["detail"] == "running"


def test_admin_service_manages_schedules(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    created = service.create_schedule(
        instance_id="default",
        name="heartbeat",
        schedule_data={"kind": "every", "every_ms": 60000},
        message="check status",
    )
    job_id = created["job"]["id"]
    assert created["job"]["name"] == "heartbeat"

    disabled = service.set_schedule_enabled(instance_id="default", job_id=job_id, enabled=False)
    assert disabled["job"]["enabled"] is False

    rerun = service.run_schedule(instance_id="default", job_id=job_id, force=True)
    assert rerun["ok"] is True

    deleted = service.delete_schedule(instance_id="default", job_id=job_id)
    assert deleted["ok"] is True


def test_admin_service_collects_recent_activity(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    manager = SessionManager(workspace)
    session = manager.get_or_create("telegram:owner")
    session.add_message("user", "hello admin")
    session.add_message("assistant", "system is ready")
    manager.save(session)

    service = AdminService(config_path=config_path)
    activity = service.get_activity(limit=10)

    assert activity["count"] >= 2
    assert activity["events"][0]["type"] in {"inbound", "outbound"}
    assert any(event["summary"] == "hello admin" for event in activity["events"])
    assert any(event.get("detail") == "hello admin" for event in activity["events"])


def test_admin_service_labels_mobile_activity_as_softnix_app(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    manager = SessionManager(workspace)
    session = manager.get_or_create("mobile-mob-1")
    session.add_message("user", "mobile hello")
    manager.save(session)

    service = AdminService(config_path=config_path)
    activity = service.get_activity(limit=10)

    assert any(event.get("channel") == "softnix_app" for event in activity["events"])


def test_admin_service_activity_falls_back_to_runtime_snapshot(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    activity = service.get_activity(limit=10)

    assert activity["count"] >= 1
    assert activity["events"][0]["type"] == "runtime"
    assert "Gateway status" in activity["events"][0]["summary"]


def test_admin_service_activity_parser_is_robust_and_reports_debug(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    sessions_dir = workspace / "sessions"
    sessions_dir.mkdir(parents=True)
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    session_path = sessions_dir / "telegram_owner.jsonl"
    session_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_type": "metadata",
                        "key": "telegram:owner",
                        "created_at": "2026-03-08T10:00:00+07:00",
                        "updated_at": "2026-03-08T10:00:10+07:00",
                    }
                ),
                '{"role":"user","message":"hello from alt schema","ts":"2026-03-08T10:00:11+07:00"}',
                '{"role":"assistant","content":[{"text":"ok from array content"}],"timestamp":"2026-03-08T10:00:12+07:00"}',
                '{"role":"assistant","tool_calls":[{"name":"search"}],"timestamp":"2026-03-08T10:00:13+07:00"}',
                '{"role":"user",bad-json}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    service = AdminService(config_path=config_path)
    activity = service.get_activity(limit=10)
    debug = service.get_activity_debug(limit=10)

    summaries = [event["summary"] for event in activity["events"]]
    assert "hello from alt schema" in summaries
    assert "ok from array content" in summaries
    assert "Tool invocation" in summaries
    assert debug["instances"][0]["session_json_parse_errors"] >= 1


def test_admin_server_resolves_health_endpoint(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    status, payload = resolve_admin_get(service, "/admin/health")

    assert status == HTTPStatus.OK
    assert payload["status"] == "ok"
    assert payload["service"] == "nanobot-admin"
    assert payload["mode"] == "safe-config"


def test_admin_server_resolves_static_index() -> None:
    path, content_type = resolve_static_asset("/")

    assert path is not None
    assert path.name == "index.html"
    assert content_type == "text/html; charset=utf-8"


def test_admin_server_resolves_softnix_logo_asset() -> None:
    path, content_type = resolve_static_asset("/static/softnix-logo-white.png")

    assert path is not None
    assert path.name == "Logo Softnix White.png"
    assert content_type == "image/png"


def test_admin_server_resolves_favicon_asset() -> None:
    path, content_type = resolve_static_asset("/favicon.ico")

    assert path is not None
    assert path.name == "Logo Softnix White.png"
    assert content_type == "image/png"


def test_admin_server_resolves_patch_channel_update(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.channels.telegram.enabled = True
    config.channels.telegram.allow_from = ["user-1"]
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    status, payload = resolve_admin_patch(
        service,
        "/admin/channels/telegram",
        {
            "instance_id": "default",
            "enabled": False,
            "allow_from": ["user-9"],
        },
    )

    assert status == HTTPStatus.OK
    telegram = next(item for item in payload["instance"]["channels"] if item["name"] == "telegram")
    assert telegram["enabled"] is False
    assert telegram["allow_from"] == ["user-9"]


def test_admin_server_resolves_access_request_routes(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.channels.telegram.enabled = True
    config.channels.telegram.allow_from = []
    save_config(config, config_path)

    store = AccessRequestStore(workspace)
    store.record(
        channel="telegram",
        sender_id="8388377631",
        chat_id="8388377631",
        content="hello",
        metadata={},
    )

    service = AdminService(config_path=config_path)
    get_status, get_payload = resolve_admin_get(service, "/admin/access-requests")
    assert get_status == HTTPStatus.OK
    assert get_payload["count"] == 1

    approve_status, approve_payload = resolve_admin_post(
        service,
        "/admin/access-requests/approve",
        {
            "instance_id": "default",
            "channel_name": "telegram",
            "sender_id": "8388377631",
        },
    )
    assert approve_status == HTTPStatus.OK
    assert approve_payload["approved"]["allow_item"] == "8388377631"

    reject_status, reject_payload = resolve_admin_post(
        service,
        "/admin/access-requests/reject",
        {
            "instance_id": "default",
            "channel_name": "telegram",
            "sender_id": "8388377631",
        },
    )
    assert reject_status == HTTPStatus.OK
    assert reject_payload["ok"] is True


def test_admin_server_resolves_patch_provider_update(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    status, payload = resolve_admin_patch(
        service,
        "/admin/providers/openai",
        {
            "instance_id": "default",
            "api_key": "sk-live",
            "api_base": "https://api.example.com/v1",
            "extra_headers": {"X-Test": "1"},
        },
    )

    assert status == HTTPStatus.OK
    openai = next(item for item in payload["instance"]["providers"] if item["name"] == "openai")
    assert openai["configured"] is True
    assert openai["extra_headers"] == {"X-Test": "1"}


def test_admin_server_resolves_delete_mcp_server(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.tools.mcp_servers["docs"] = MCPServerConfig(type="streamableHttp", url="https://example.com/mcp")
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    status, payload = resolve_admin_delete(
        service,
        "/admin/mcp/servers/docs",
        {"instance_id": "default"},
    )

    assert status == HTTPStatus.OK
    assert payload["instance"]["mcp"]["server_count"] == 0


def test_admin_server_resolves_delete_mcp_server_with_url_name(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    server_name = "https://connect.composio.dev/mcp"
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.tools.mcp_servers[server_name] = MCPServerConfig(type="streamableHttp", url=server_name)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    status, payload = resolve_admin_delete(
        service,
        "/admin/mcp/servers/https%3A%2F%2Fconnect.composio.dev%2Fmcp",
        {"instance_id": "default"},
    )

    assert status == HTTPStatus.OK
    assert payload["instance"]["mcp"]["server_count"] == 0


def test_admin_server_resolves_post_validation_routes(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.agents.defaults.model = "openai/gpt-4.1-mini"
    config.agents.defaults.provider = "openai"
    config.providers.openai.api_key = "sk-live"
    config.tools.mcp_servers["docs"] = MCPServerConfig(
        type="streamableHttp",
        url="https://example.com/mcp",
    )
    save_config(config, config_path)

    service = AdminService(config_path=config_path)

    provider_status, provider_payload = resolve_admin_post(
        service,
        "/admin/providers/openai/validate",
        {"instance_id": "default"},
    )
    mcp_status, mcp_payload = resolve_admin_post(
        service,
        "/admin/mcp/servers/docs/validate",
        {"instance_id": "default"},
    )

    assert provider_status == HTTPStatus.OK
    assert provider_payload["status"] == "ok"
    assert mcp_status == HTTPStatus.OK
    assert mcp_payload["status"] == "ok"


def test_admin_server_resolves_mcp_validation_with_url_name(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    server_name = "https://connect.composio.dev/mcp"
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.tools.mcp_servers[server_name] = MCPServerConfig(
        type="streamableHttp",
        url=server_name,
    )
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    mcp_status, mcp_payload = resolve_admin_post(
        service,
        "/admin/mcp/servers/https%3A%2F%2Fconnect.composio.dev%2Fmcp/validate",
        {"instance_id": "default"},
    )

    assert mcp_status == HTTPStatus.OK
    assert mcp_payload["status"] == "ok"


def test_admin_server_forwards_gmail_refresh_credentials() -> None:
    calls: list[tuple[str, dict]] = []

    class StubService:
        def install_gmail_connector(self, **kwargs):
            calls.append(("install", kwargs))
            return {"ok": True}

        def validate_gmail_connector(self, **kwargs):
            calls.append(("validate", kwargs))
            return {"ok": True}

    status, payload = resolve_admin_post(
        StubService(),
        "/admin/connectors/gmail/install",
        {
            "instance_id": "default",
            "token": "ya29_access",
            "user_id": "me",
            "api_base": "https://gmail.googleapis.com/gmail/v1",
            "refresh_token": "refresh-token",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
    )
    assert status == HTTPStatus.OK
    assert payload == {"ok": True}

    status, payload = resolve_admin_post(
        StubService(),
        "/admin/connectors/gmail/validate",
        {
            "instance_id": "default",
            "token": "ya29_access",
            "user_id": "me",
            "api_base": "https://gmail.googleapis.com/gmail/v1",
            "refresh_token": "refresh-token",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
    )
    assert status == HTTPStatus.OK
    assert payload == {"ok": True}

    install_call = next(call for call in calls if call[0] == "install")[1]
    validate_call = next(call for call in calls if call[0] == "validate")[1]
    for call in (install_call, validate_call):
        assert call["refresh_token"] == "refresh-token"
        assert call["client_id"] == "client-id"
        assert call["client_secret"] == "client-secret"
        assert call["token_uri"] == "https://oauth2.googleapis.com/token"


def test_admin_server_forwards_composio_connector_settings() -> None:
    calls: list[tuple[str, dict]] = []

    class StubService:
        def install_composio_connector(self, **kwargs):
            calls.append(("install", kwargs))
            return {"ok": True}

        def validate_composio_connector(self, **kwargs):
            calls.append(("validate", kwargs))
            return {"ok": True}

    status, payload = resolve_admin_post(
        StubService(),
        "/admin/connectors/composio/install",
        {
            "instance_id": "default",
            "api_key": "ck_example",
        },
    )
    assert status == HTTPStatus.OK
    assert payload == {"ok": True}

    status, payload = resolve_admin_post(
        StubService(),
        "/admin/connectors/composio/validate",
        {
            "instance_id": "default",
            "api_key": "ck_example",
        },
    )
    assert status == HTTPStatus.OK
    assert payload == {"ok": True}

    install_call = next(call for call in calls if call[0] == "install")[1]
    validate_call = next(call for call in calls if call[0] == "validate")[1]
    for call in (install_call, validate_call):
        assert call["api_key"] == "ck_example"


def test_admin_server_forwards_connector_enable_disable() -> None:
    calls: list[dict] = []

    class StubService:
        def set_connector_enabled(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "enabled": kwargs["enabled"]}

    status, payload = resolve_admin_post(
        StubService(),
        "/admin/connectors/composio/disable",
        {
            "instance_id": "default",
        },
    )
    assert status == HTTPStatus.OK
    assert payload == {"ok": True, "enabled": False}

    status, payload = resolve_admin_post(
        StubService(),
        "/admin/connectors/composio/enable",
        {
            "instance_id": "default",
        },
    )
    assert status == HTTPStatus.OK
    assert payload == {"ok": True, "enabled": True}

    assert calls[0]["connector_name"] == "composio"
    assert calls[0]["enabled"] is False
    assert calls[1]["connector_name"] == "composio"
    assert calls[1]["enabled"] is True


def test_admin_server_forwards_insightdoc_connector_settings() -> None:
    calls: list[tuple[str, dict]] = []

    class StubService:
        def install_insightdoc_connector(self, **kwargs):
            calls.append(("install", kwargs))
            return {"ok": True}

        def validate_insightdoc_connector(self, **kwargs):
            calls.append(("validate", kwargs))
            return {"ok": True}

    status, payload = resolve_admin_post(
        StubService(),
        "/admin/connectors/insightdoc/install",
        {
            "instance_id": "default",
            "token": "sid_pat_access",
            "api_base_url": "https://127.0.0.1/api/v1",
            "external_base_url": "https://127.0.0.1/api/v1/external",
            "default_job_name": "Invoice Batch",
            "default_schema_id": "schema-1",
            "default_integration_name": "Comply TOR",
            "curl_insecure": True,
        },
    )
    assert status == HTTPStatus.OK
    assert payload == {"ok": True}

    status, payload = resolve_admin_post(
        StubService(),
        "/admin/connectors/insightdoc/validate",
        {
            "instance_id": "default",
            "token": "sid_pat_access",
            "api_base_url": "https://127.0.0.1/api/v1",
            "external_base_url": "https://127.0.0.1/api/v1/external",
            "default_job_name": "Invoice Batch",
            "default_schema_id": "schema-1",
            "default_integration_name": "Comply TOR",
            "curl_insecure": True,
        },
    )
    assert status == HTTPStatus.OK
    assert payload == {"ok": True}

    install_call = next(call for call in calls if call[0] == "install")[1]
    validate_call = next(call for call in calls if call[0] == "validate")[1]
    for call in (install_call, validate_call):
        assert call["api_base_url"] == "https://127.0.0.1/api/v1"
        assert call["external_base_url"] == "https://127.0.0.1/api/v1/external"
        assert call["default_job_name"] == "Invoice Batch"
        assert call["default_schema_id"] == "schema-1"
        assert call["default_integration_name"] == "Comply TOR"
        assert call["curl_insecure"] is True


def test_admin_server_resolves_post_instance_lifecycle(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"
    registry_path = tmp_path / "instances.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)
    registry_path.write_text(
        json.dumps(
            {
                "instances": [
                    {
                        "id": "prod",
                        "config": str(config_path),
                        "lifecycle": {
                            "restart": ["echo", "restart-prod"],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    service = AdminService(registry_path=registry_path)
    with patch("nanobot.admin.service.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["echo", "restart-prod"],
            returncode=0,
            stdout="restarted",
            stderr="",
        )
        status, payload = resolve_admin_post(
            service,
            "/admin/instances/prod/restart",
            {},
        )

    assert status == HTTPStatus.OK
    assert payload["ok"] is True
    assert payload["action"] == "restart"


def test_admin_server_resolves_instance_registry_routes(tmp_path) -> None:
    registry_path = tmp_path / "admin" / "instances.json"
    service = AdminService(registry_path=registry_path)

    create_status, create_payload = resolve_admin_post(
        service,
        "/admin/instances",
        {
            "instance_id": "acme-prod",
            "name": "Acme Production",
            "owner": "acme",
            "env": "prod",
            "repo_root": str(tmp_path),
            "nanobot_bin": "/opt/anaconda3/bin/nanobot",
        },
    )
    patch_status, patch_payload = resolve_admin_patch(
        service,
        "/admin/instances/acme-prod",
        {
            "name": "Acme Production Updated",
            "owner": "acme-team",
            "env": "uat",
        },
    )
    delete_status, delete_payload = resolve_admin_delete(
        service,
        "/admin/instances/acme-prod",
        {"purge_files": False},
    )

    assert create_status == HTTPStatus.OK
    assert create_payload["instance"]["id"] == "acme-prod"
    assert patch_status == HTTPStatus.OK
    assert patch_payload["instance"]["name"] == "Acme Production Updated"
    assert patch_payload["instance"]["owner"] == "acme-team"
    assert delete_status == HTTPStatus.OK
    assert delete_payload["instance_id"] == "acme-prod"


def test_admin_server_resolves_instance_config_routes(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    get_status, get_payload = resolve_admin_get(service, "/admin/instances/default/config")
    patch_status, patch_payload = resolve_admin_patch(
        service,
        "/admin/instances/default/config",
        {
            "config": {
                **get_payload["config"],
                "agents": {
                    **get_payload["config"]["agents"],
                    "defaults": {
                        **get_payload["config"]["agents"]["defaults"],
                        "model": "openai/gpt-4.1-mini",
                    },
                },
            }
        },
    )

    assert get_status == HTTPStatus.OK
    assert patch_status == HTTPStatus.OK
    assert patch_payload["config"]["agents"]["defaults"]["model"] == "openai/gpt-4.1-mini"


def test_admin_server_resolves_schedule_routes(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    create_status, create_payload = resolve_admin_post(
        service,
        "/admin/schedules",
        {
            "instance_id": "default",
            "name": "poll",
            "schedule": {"kind": "every", "every_ms": 30000},
            "message": "poll status",
        },
    )
    job_id = create_payload["job"]["id"]

    toggle_status, toggle_payload = resolve_admin_patch(
        service,
        f"/admin/schedules/{job_id}/enabled",
        {"instance_id": "default", "enabled": False},
    )
    run_status, run_payload = resolve_admin_post(
        service,
        f"/admin/schedules/{job_id}/run",
        {"instance_id": "default"},
    )
    delete_status, delete_payload = resolve_admin_delete(
        service,
        f"/admin/schedules/{job_id}",
        {"instance_id": "default"},
    )

    assert create_status == HTTPStatus.OK
    assert toggle_status == HTTPStatus.OK
    assert toggle_payload["job"]["enabled"] is False
    assert run_status == HTTPStatus.OK
    assert run_payload["ok"] is True
    assert delete_status == HTTPStatus.OK
    assert delete_payload["ok"] is True


def test_admin_server_resolves_activity_endpoint(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    manager = SessionManager(workspace)
    session = manager.get_or_create("telegram:owner")
    session.add_message("user", "ping")
    manager.save(session)

    service = AdminService(config_path=config_path)
    status, payload = resolve_admin_get(service, "/admin/activity")

    assert status == HTTPStatus.OK
    assert payload["count"] >= 1
    assert payload["events"][0]["session_key"] == "telegram:owner"


def test_admin_server_resolves_activity_debug_endpoint(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    manager = SessionManager(workspace)
    session = manager.get_or_create("telegram:owner")
    session.add_message("user", "debug me")
    manager.save(session)

    service = AdminService(config_path=config_path)
    status, payload = resolve_admin_get(service, "/admin/activity/debug")

    assert status == HTTPStatus.OK
    assert payload["count"] >= 1
    assert isinstance(payload["instances"], list)
    assert payload["instances"][0]["session_files_seen"] >= 1


def test_admin_server_resolves_runtime_audit_endpoint(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    audit = RuntimeAuditLogger(workspace)
    audit.log_tool_call("exec", {"command": "ls -la"}, "ok")

    service = AdminService(config_path=config_path)
    status, payload = resolve_admin_get(
        service,
        "/admin/runtime-audit?instance_id=default&limit=5&status=all",
    )
    assert status == HTTPStatus.OK
    assert payload["instance_id"] == "default"
    assert payload["count"] >= 1
    assert payload["summary"]["event_count"] >= 1

    bad_status, bad_payload = resolve_admin_get(
        service,
        "/admin/runtime-audit?instance_id=default&status=bad",
    )
    assert bad_status == HTTPStatus.BAD_REQUEST
    assert "status must be one of" in bad_payload["error"]


def test_admin_server_auth_bootstrap_login_logout_flow(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    server, thread, port = _start_admin_server(service)
    try:
        status, payload, _ = _request_json(port, "GET", "/admin/auth/me")
        assert status == HTTPStatus.OK
        assert payload["authenticated"] is False
        assert payload["bootstrap_required"] is True

        status, payload, headers = _request_json(
            port,
            "POST",
            "/admin/auth/bootstrap",
            payload={
                "username": "owner",
                "display_name": "Team Owner",
                "email": "owner@example.com",
                "password": "owner-pass-123",
            },
        )
        assert status == HTTPStatus.OK
        assert payload["authenticated"] is True
        assert payload["user"]["role"] == "owner"
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        csrf_token = payload["session"]["csrf_token"]

        me_status, me_payload, _ = _request_json(port, "GET", "/admin/auth/me", headers={"Cookie": cookie})
        assert me_status == HTTPStatus.OK
        assert me_payload["authenticated"] is True
        assert me_payload["user"]["username"] == "owner"

        bad_logout_status, bad_logout_payload, _ = _request_json(
            port,
            "POST",
            "/admin/auth/logout",
            payload={},
            headers={"Cookie": cookie},
        )
        assert bad_logout_status == HTTPStatus.FORBIDDEN
        assert bad_logout_payload["error"] == "Invalid CSRF token"

        logout_status, logout_payload, _ = _request_json(
            port,
            "POST",
            "/admin/auth/logout",
            payload={},
            headers={"Cookie": cookie, "X-CSRF-Token": csrf_token},
        )
        assert logout_status == HTTPStatus.OK
        assert logout_payload["ok"] is True

        login_status, login_payload, login_headers = _request_json(
            port,
            "POST",
            "/admin/auth/login",
            payload={"login": "owner", "password": "owner-pass-123"},
        )
        assert login_status == HTTPStatus.OK
        assert login_payload["authenticated"] is True
        assert "Set-Cookie" in login_headers
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_admin_server_enforces_rbac_and_csrf_for_viewer(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    service.bootstrap_admin_user(username="owner", password="owner-pass-123", display_name="Owner")
    service.create_admin_user(
        username="viewer1",
        password="viewer-pass-123",
        display_name="Viewer One",
        role="viewer",
    )

    server, thread, port = _start_admin_server(service)
    try:
        login_status, login_payload, login_headers = _request_json(
            port,
            "POST",
            "/admin/auth/login",
            payload={"login": "viewer1", "password": "viewer-pass-123"},
        )
        assert login_status == HTTPStatus.OK
        cookie = login_headers["Set-Cookie"].split(";", 1)[0]
        csrf_token = login_payload["session"]["csrf_token"]

        get_status, get_payload, _ = _request_json(port, "GET", "/admin/overview", headers={"Cookie": cookie})
        assert get_status == HTTPStatus.OK
        assert "summary" in get_payload

        no_csrf_status, no_csrf_payload, _ = _request_json(
            port,
            "PATCH",
            "/admin/security/workspace-restriction",
            payload={"instance_id": "default", "restrict_to_workspace": True},
            headers={"Cookie": cookie},
        )
        assert no_csrf_status == HTTPStatus.FORBIDDEN
        assert no_csrf_payload["error"] == "Invalid CSRF token"

        forbidden_status, forbidden_payload, _ = _request_json(
            port,
            "PATCH",
            "/admin/security/workspace-restriction",
            payload={"instance_id": "default", "restrict_to_workspace": True},
            headers={"Cookie": cookie, "X-CSRF-Token": csrf_token},
        )
        assert forbidden_status == HTTPStatus.FORBIDDEN
        assert forbidden_payload["error"] == "Forbidden"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_admin_server_owner_can_manage_users_and_reset_password(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    service.bootstrap_admin_user(username="owner", password="owner-pass-123", display_name="Owner")

    server, thread, port = _start_admin_server(service)
    try:
        login_status, login_payload, login_headers = _request_json(
            port,
            "POST",
            "/admin/auth/login",
            payload={"login": "owner", "password": "owner-pass-123"},
        )
        assert login_status == HTTPStatus.OK
        cookie = login_headers["Set-Cookie"].split(";", 1)[0]
        csrf_token = login_payload["session"]["csrf_token"]

        create_status, create_payload, _ = _request_json(
            port,
            "POST",
            "/admin/users",
            payload={
                "username": "operator1",
                "display_name": "Operator One",
                "email": "operator1@example.com",
                "password": "operator-pass-123",
                "role": "operator",
            },
            headers={"Cookie": cookie, "X-CSRF-Token": csrf_token},
        )
        assert create_status == HTTPStatus.OK
        user_id = create_payload["user"]["id"]

        list_status, list_payload, _ = _request_json(port, "GET", "/admin/users", headers={"Cookie": cookie})
        assert list_status == HTTPStatus.OK
        assert list_payload["count"] == 2

        update_status, update_payload, _ = _request_json(
            port,
            "PATCH",
            f"/admin/users/{user_id}",
            payload={
                "display_name": "Operator Prime",
                "email": "operator1@example.com",
                "role": "admin",
                "status": "active",
            },
            headers={"Cookie": cookie, "X-CSRF-Token": csrf_token},
        )
        assert update_status == HTTPStatus.OK
        assert update_payload["user"]["role"] == "admin"

        reset_status, reset_payload, _ = _request_json(
            port,
            "POST",
            f"/admin/users/{user_id}/reset-password",
            payload={"new_password": "new-operator-pass-123"},
            headers={"Cookie": cookie, "X-CSRF-Token": csrf_token},
        )
        assert reset_status == HTTPStatus.OK
        assert reset_payload["ok"] is True

        relogin_status, relogin_payload, _ = _request_json(
            port,
            "POST",
            "/admin/auth/login",
            payload={"login": "operator1", "password": "new-operator-pass-123"},
        )
        assert relogin_status == HTTPStatus.OK
        assert relogin_payload["user"]["role"] == "admin"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_admin_server_unauthenticated_unknown_patch_returns_401(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    server, thread, port = _start_admin_server(service)
    try:
        status, payload, _ = _request_json(
            port,
            "PATCH",
            "/admin/unknown",
            payload={"hello": "world"},
        )
        assert status == HTTPStatus.UNAUTHORIZED
        assert payload["error"] == "Authentication required"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_admin_server_unauthenticated_unknown_post_returns_401(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    server, thread, port = _start_admin_server(service)
    try:
        status, payload, _ = _request_json(
            port,
            "POST",
            "/admin/unknown",
            payload={"hello": "world"},
        )
        assert status == HTTPStatus.UNAUTHORIZED
        assert payload["error"] == "Authentication required"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_admin_server_scopes_instances_and_users_by_user_assignment(tmp_path) -> None:
    prod_workspace = tmp_path / "prod-workspace"
    staging_workspace = tmp_path / "staging-workspace"
    prod_workspace.mkdir()
    staging_workspace.mkdir()
    prod_config = tmp_path / "prod.json"
    staging_config = tmp_path / "staging.json"
    registry_path = tmp_path / "instances.json"

    prod = Config()
    prod.agents.defaults.workspace = str(prod_workspace)
    save_config(prod, prod_config)

    staging = Config()
    staging.agents.defaults.workspace = str(staging_workspace)
    save_config(staging, staging_config)

    registry_path.write_text(
        json.dumps(
            {
                "instances": [
                    {"id": "prod", "name": "Production", "config": str(prod_config)},
                    {"id": "staging", "name": "Staging", "config": str(staging_config)},
                ]
            }
        ),
        encoding="utf-8",
    )

    service = AdminService(registry_path=registry_path)
    service.bootstrap_admin_user(username="owner", password="owner-pass-123", display_name="Owner")

    owner = service.authenticate_admin_user(login="owner", password="owner-pass-123")
    assert owner["user"]["instance_ids"] is None

    admin_create = service.create_admin_user(
        username="admin-prod",
        password="admin-pass-123",
        display_name="Admin Prod",
        email="admin.prod@example.com",
        role="admin",
        instance_ids=["prod"],
        allowed_instance_ids=owner["user"]["instance_ids"],
    )
    admin_id = admin_create["user"]["id"]
    assert admin_create["user"]["instance_ids"] == ["prod"]

    operator_create = service.create_admin_user(
        username="operator-prod",
        password="operator-pass-123",
        display_name="Operator Prod",
        email="operator.prod@example.com",
        role="operator",
        instance_ids=["prod"],
        allowed_instance_ids=owner["user"]["instance_ids"],
    )
    assert operator_create["user"]["instance_ids"] == ["prod"]

    staging_create = service.create_admin_user(
        username="operator-stage",
        password="operator-pass-456",
        display_name="Operator Stage",
        email="operator.stage@example.com",
        role="operator",
        instance_ids=["staging"],
        allowed_instance_ids=owner["user"]["instance_ids"],
    )
    assert staging_create["user"]["instance_ids"] == ["staging"]

    admin_login = service.authenticate_admin_user(login="admin-prod", password="admin-pass-123")
    admin_scope = admin_login["user"]["instance_ids"]
    assert admin_scope == ["prod"]

    overview_payload = service.get_overview(accessible_instance_ids=admin_scope)
    assert overview_payload["summary"]["instance_count"] == 1
    assert [item["id"] for item in overview_payload["instances"]] == ["prod"]

    users_payload = service.list_admin_users(accessible_instance_ids=admin_scope)
    assert users_payload["count"] == 2
    assert {user["username"] for user in users_payload["users"]} == {"admin-prod", "operator-prod"}

    default_scope_create = service.create_admin_user(
        username="operator-default",
        password="operator-pass-789",
        display_name="Operator Default",
        email="operator.default@example.com",
        role="operator",
        allowed_instance_ids=admin_scope,
    )
    assert default_scope_create["user"]["instance_ids"] == ["prod"]

    users_payload = service.list_admin_users(accessible_instance_ids=admin_scope)
    assert users_payload["count"] == 3
    assert {user["username"] for user in users_payload["users"]} == {"admin-prod", "operator-prod", "operator-default"}

    updated = service.update_admin_user(
        user_id=admin_id,
        display_name="Admin Prime",
        email="admin.prod@example.com",
        role="admin",
        status="active",
    )
    assert updated["user"]["instance_ids"] == ["prod"]

    with_error = False
    try:
        service.update_admin_user(
            user_id=admin_id,
            instance_ids=["staging"],
            allowed_instance_ids=admin_scope,
        )
    except ValueError as exc:
        with_error = True
        assert "inaccessible instance ids" in str(exc).lower()
    assert with_error is True
