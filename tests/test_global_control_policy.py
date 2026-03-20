import json
from http import HTTPStatus
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nanobot.admin.server import resolve_admin_get, resolve_admin_patch, resolve_admin_post
from nanobot.admin.service import AdminService
from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.config.loader import save_config
from nanobot.config.schema import Config
from nanobot.providers.base import LLMResponse, ToolCallRequest
from nanobot.runtime.audit import RuntimeAuditLogger
from nanobot.security.policy import GlobalControlPolicyStore, infer_global_policy_path


class DummyProvider:
    def get_default_model(self) -> str:
        return "dummy"

    async def chat(self, *args, **kwargs):
        return LLMResponse(content="ok", tool_calls=[])


def _write_policy(config_path: Path, policy: dict) -> Path:
    policy_path = infer_global_policy_path(config_path)
    store = GlobalControlPolicyStore(policy_path)
    store.save(policy, actor={"user_id": "test", "username": "tester"})
    return policy_path


@pytest.mark.asyncio
async def test_agent_loop_blocks_input_before_provider(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    provider = DummyProvider()
    provider.chat = AsyncMock(return_value=LLMResponse(content="should-not-run", tool_calls=[]))
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        global_policy_path=_write_policy(
            config_path,
            {
                "enabled": True,
                "mode": "enforce",
                "rules": [
                    {
                        "rule_id": "block-secret",
                        "name": "Block secret phrase",
                        "category": "custom",
                        "enabled": True,
                        "scope": ["input"],
                        "severity": "critical",
                        "priority": 100,
                        "action": "block",
                        "message_template": "Blocked by policy",
                        "detectors": [{"type": "phrase", "values": ["internal secret"]}],
                    }
                ],
            },
        ),
    )

    result = await loop.process_direct("please use internal secret now")

    assert result == "Blocked by policy"
    provider.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_loop_masks_output_before_returning(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    provider = DummyProvider()
    provider.chat = AsyncMock(return_value=LLMResponse(content="Contact me at owner@example.com", tool_calls=[]))
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        global_policy_path=_write_policy(
            config_path,
            {
                "enabled": True,
                "mode": "enforce",
                "rules": [
                    {
                        "rule_id": "mask-email",
                        "name": "Mask email",
                        "category": "pii",
                        "enabled": True,
                        "scope": ["output"],
                        "severity": "high",
                        "priority": 100,
                        "action": "mask",
                        "message_template": "Email masked",
                        "detectors": [{"type": "pii", "pii_types": ["email"]}],
                    }
                ],
            },
        ),
    )

    result = await loop.process_direct("hello")

    assert "[REDACTED_EMAIL]" in result
    assert "owner@example.com" not in result


@pytest.mark.asyncio
async def test_agent_loop_refuses_masked_sensitive_persistence_request_in_thai(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    provider = DummyProvider()
    provider.chat = AsyncMock(return_value=LLMResponse(content="should-not-run", tool_calls=[]))
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        global_policy_path=_write_policy(
            config_path,
            {
                "enabled": True,
                "mode": "enforce",
                "rules": [
                    {
                        "rule_id": "mask-email",
                        "name": "Mask email",
                        "category": "pii",
                        "enabled": True,
                        "scope": ["input", "memory_write"],
                        "severity": "high",
                        "priority": 100,
                        "action": "mask",
                        "message_template": "Email masked",
                        "detectors": [{"type": "pii", "pii_types": ["email"]}],
                    }
                ],
            },
        ),
    )

    result = await loop.process_direct("ช่วยจำอีเมลนี้ไว้ให้หน่อย user.qa@example.com")

    assert "ไม่สามารถเก็บข้อมูลจริง" in result
    assert "[REDACTED_EMAIL]" in result
    provider.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_loop_refuses_masked_sensitive_persistence_request_in_english(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    provider = DummyProvider()
    provider.chat = AsyncMock(return_value=LLMResponse(content="should-not-run", tool_calls=[]))
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        global_policy_path=_write_policy(
            config_path,
            {
                "enabled": True,
                "mode": "enforce",
                "rules": [
                    {
                        "rule_id": "mask-email",
                        "name": "Mask email",
                        "category": "pii",
                        "enabled": True,
                        "scope": ["input", "memory_write"],
                        "severity": "high",
                        "priority": 100,
                        "action": "mask",
                        "message_template": "Email masked",
                        "detectors": [{"type": "pii", "pii_types": ["email"]}],
                    }
                ],
            },
        ),
    )

    result = await loop.process_direct("Please remember this email user.qa@example.com")

    assert "can't store the original sensitive data" in result
    assert "[REDACTED_EMAIL]" in result
    provider.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_loop_blocks_tool_args_before_execution(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    provider = DummyProvider()
    provider.chat = AsyncMock(
        side_effect=[
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="call-1", name="exec", arguments={"command": "rm -rf /tmp/demo"})],
            ),
            LLMResponse(content="tool blocked", tool_calls=[]),
        ]
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        global_policy_path=_write_policy(
            config_path,
            {
                "enabled": True,
                "mode": "enforce",
                "rules": [
                    {
                        "rule_id": "block-rm-rf",
                        "name": "Block rm -rf",
                        "category": "custom",
                        "enabled": True,
                        "scope": ["tool_args"],
                        "severity": "critical",
                        "priority": 100,
                        "action": "block",
                        "message_template": "Dangerous command blocked",
                        "detectors": [{"type": "phrase", "values": ["rm -rf"]}],
                    }
                ],
            },
        ),
    )
    loop.tools.execute = AsyncMock(return_value="should-not-run")

    result = await loop.process_direct("delete the folder")

    assert result == "tool blocked"
    loop.tools.execute.assert_not_awaited()


def test_admin_service_manages_global_policy_and_security_audit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    updated = service.update_global_policy(
        policy={
            "enabled": True,
            "mode": "monitor",
            "rules": [
                {
                    "rule_id": "warn-phrase",
                    "name": "Warn phrase",
                    "category": "custom",
                    "enabled": True,
                    "scope": ["input"],
                    "severity": "medium",
                    "priority": 10,
                    "action": "warn",
                    "message_template": "warned",
                    "detectors": [{"type": "phrase", "values": ["watch this"]}],
                }
            ],
        }
    )

    assert updated["summary"]["mode"] == "monitor"
    assert updated["summary"]["rule_count"] == len(updated["policy"]["rules"])
    assert updated["summary"]["enabled_rule_count"] == 1

    audit = RuntimeAuditLogger(workspace)
    audit.log_policy_event(
        scope="input",
        decision={
            "action": "block",
            "blocked": True,
            "severity": "critical",
            "matched_rules": ["warn-phrase"],
            "mode": "enforce",
            "monitor_only": False,
            "text": "watch this now",
            "sanitized_text": "watch this now",
            "policy_version": updated["summary"]["version"],
        },
        channel="cli",
        session_key="cli:test",
        instance_id="default",
        instance_name="Default",
    )

    security = service.get_security()
    assert security["global_policy"]["mode"] == "monitor"
    assert security["detections_by_instance"][0]["instance_id"] == "default"
    assert security["recent_policy_hits"][0]["policy_action"] == "block"

    auth_audit = service.get_auth_audit_log(limit=50)
    assert any(event.get("event_type") == "security.policy_global_created" for event in auth_audit["events"])
    assert any(event.get("category") == "policy_runtime" for event in auth_audit["events"])


def test_admin_server_resolves_global_policy_endpoints(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    get_status, get_payload = resolve_admin_get(service, "/admin/security/policies/global")
    assert get_status == HTTPStatus.OK
    assert get_payload["summary"]["exists"] is True

    validate_status, validate_payload = resolve_admin_post(
        service,
        "/admin/security/policies/global/validate",
        {"policy": {"enabled": True, "mode": "enforce", "rules": []}},
    )
    assert validate_status == HTTPStatus.OK
    assert validate_payload["valid"] is True

    patch_status, patch_payload = resolve_admin_patch(
        service,
        "/admin/security/policies/global",
        {"policy": {"enabled": True, "mode": "monitor", "rules": []}},
    )
    assert patch_status == HTTPStatus.OK
    assert patch_payload["summary"]["mode"] == "monitor"
