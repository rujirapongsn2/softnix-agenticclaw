from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from nanobot.admin.connectors import build_gmail_stdio_server_config, get_connector_preset, list_connector_presets
from nanobot.admin.service import AdminService
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config, MCPServerConfig


@dataclass(frozen=True)
class ConnectorContract:
    name: str
    runtime_script_name: str
    expected_env_keys: tuple[str, ...]
    install: Callable[[AdminService], dict[str, Any]]
    seed_saved_config: Callable[[Path], None]
    patch_validate_success: Callable[[pytest.MonkeyPatch], None]
    patch_validate_failure: Callable[[pytest.MonkeyPatch], None]
    validate: Callable[[AdminService], dict[str, Any]]


def _make_config(config_path: Path) -> None:
    workspace = config_path.parent / "workspace"
    workspace.mkdir()
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)


def _github_install(service: AdminService) -> dict[str, Any]:
    return service.install_github_connector(
        instance_id="default",
        token="ghp_example",
        default_repo="owner/repo",
    )


def _github_seed_saved_config(config_path: Path) -> None:
    config = load_config(config_path)
    config.tools.mcp_servers["github"] = MCPServerConfig.model_validate(
        {
            "type": "stdio",
            "command": "python3",
            "args": ["-m", "nanobot.integrations.github_mcp_server"],
            "env": {
                "GITHUB_TOKEN": "ghp_saved",
                "GITHUB_DEFAULT_REPO": "owner/repo",
                "GITHUB_API_BASE": "https://api.github.com",
            },
        }
    )
    save_config(config, config_path)


def _patch_github_validate_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyGitHubClient:
        def __init__(self, *, token: str, api_base: str, default_repo: str | None) -> None:
            self.token = token
            self.api_base = api_base
            self.default_repo = default_repo

        def whoami(self) -> dict[str, str]:
            return {"login": "octocat"}

        def get_repository(self, repo: str | None = None) -> dict[str, str]:
            return {"full_name": repo or self.default_repo or "owner/repo"}

    monkeypatch.setattr("nanobot.admin.service.GitHubClient", DummyGitHubClient)


def _patch_github_validate_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyGitHubClient:
        def __init__(self, *, token: str, api_base: str, default_repo: str | None) -> None:
            self.token = token
            self.api_base = api_base
            self.default_repo = default_repo

        def whoami(self) -> dict[str, str]:
            raise httpx.HTTPStatusError(
                "unauthorized",
                request=httpx.Request("GET", "https://api.github.com/user"),
                response=httpx.Response(401),
            )

    monkeypatch.setattr("nanobot.admin.service.GitHubClient", DummyGitHubClient)


def _github_validate(service: AdminService) -> dict[str, Any]:
    return service.validate_github_connector(
        instance_id="default",
        token="",
    )


def _notion_install(service: AdminService) -> dict[str, Any]:
    return service.install_notion_connector(
        instance_id="default",
        token="secret_example",
        default_page_id="page-1",
        notion_version="2026-03-11",
    )


def _notion_seed_saved_config(config_path: Path) -> None:
    config = load_config(config_path)
    config.tools.mcp_servers["notion"] = MCPServerConfig.model_validate(
        {
            "type": "stdio",
            "command": "python3",
            "args": ["-m", "nanobot.integrations.notion_mcp_server"],
            "env": {
                "NOTION_TOKEN": "secret_saved",
                "NOTION_DEFAULT_PAGE_ID": "page-1",
                "NOTION_API_BASE": "https://api.notion.com/v1",
                "NOTION_VERSION": "2026-03-11",
            },
        }
    )
    save_config(config, config_path)


def _patch_notion_validate_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyNotionClient:
        def __init__(self, *, token: str, api_base: str, notion_version: str, default_page_id: str | None) -> None:
            self.token = token
            self.api_base = api_base
            self.notion_version = notion_version
            self.default_page_id = default_page_id

        def whoami(self) -> dict[str, str]:
            return {"name": "Workspace Bot"}

        def get_page(self, page_id: str | None = None) -> dict[str, str]:
            return {"id": page_id or self.default_page_id or "page-1"}

    monkeypatch.setattr("nanobot.admin.service.NotionClient", DummyNotionClient)


def _patch_notion_validate_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyNotionClient:
        def __init__(self, *, token: str, api_base: str, notion_version: str, default_page_id: str | None) -> None:
            self.token = token
            self.api_base = api_base
            self.notion_version = notion_version
            self.default_page_id = default_page_id

        def whoami(self) -> dict[str, str]:
            raise httpx.HTTPStatusError(
                "unauthorized",
                request=httpx.Request("GET", "https://api.notion.com/v1/users/me"),
                response=httpx.Response(401),
            )

    monkeypatch.setattr("nanobot.admin.service.NotionClient", DummyNotionClient)


def _notion_validate(service: AdminService) -> dict[str, Any]:
    return service.validate_notion_connector(
        instance_id="default",
        token="",
    )


def _gmail_install(service: AdminService) -> dict[str, Any]:
    return service.install_gmail_connector(
        instance_id="default",
        token="ya29_example",
        user_id="me",
    )


def _gmail_seed_saved_config(config_path: Path) -> None:
    config = load_config(config_path)
    config.tools.mcp_servers["gmail"] = MCPServerConfig.model_validate(
        build_gmail_stdio_server_config(
            token="ya29_saved",
            user_id="me",
            api_base="https://gmail.googleapis.com/gmail/v1",
        )
    )
    save_config(config, config_path)


def _patch_gmail_validate_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyGmailClient:
        def __init__(self, *, token: str, api_base: str, user_id: str) -> None:
            self.token = token
            self.api_base = api_base
            self.user_id = user_id

        def whoami(self) -> dict[str, str]:
            return {"emailAddress": "owner@example.com"}

        def list_labels(self, user_id: str | None = None) -> dict[str, list[dict[str, str]]]:
            return {"labels": [{"id": "INBOX"}]}

        def token_scopes(self) -> set[str]:
            return {"https://www.googleapis.com/auth/gmail.compose"}

    monkeypatch.setattr("nanobot.admin.service.GmailClient", DummyGmailClient)


def _patch_gmail_validate_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyGmailClient:
        def __init__(self, *, token: str, api_base: str, user_id: str) -> None:
            self.token = token
            self.api_base = api_base
            self.user_id = user_id

        def whoami(self) -> dict[str, str]:
            raise httpx.HTTPStatusError(
                "unauthorized",
                request=httpx.Request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile"),
                response=httpx.Response(401),
            )

    monkeypatch.setattr("nanobot.admin.service.GmailClient", DummyGmailClient)


def _gmail_validate(service: AdminService) -> dict[str, Any]:
    return service.validate_gmail_connector(
        instance_id="default",
        token="",
    )


CONNECTOR_CONTRACTS: tuple[ConnectorContract, ...] = (
    ConnectorContract(
        name="github",
        runtime_script_name="github_mcp_server.py",
        expected_env_keys=("GITHUB_TOKEN", "GITHUB_DEFAULT_REPO"),
        install=_github_install,
        seed_saved_config=_github_seed_saved_config,
        patch_validate_success=_patch_github_validate_success,
        patch_validate_failure=_patch_github_validate_failure,
        validate=_github_validate,
    ),
    ConnectorContract(
        name="notion",
        runtime_script_name="notion_mcp_server.py",
        expected_env_keys=("NOTION_TOKEN", "NOTION_DEFAULT_PAGE_ID"),
        install=_notion_install,
        seed_saved_config=_notion_seed_saved_config,
        patch_validate_success=_patch_notion_validate_success,
        patch_validate_failure=_patch_notion_validate_failure,
        validate=_notion_validate,
    ),
    ConnectorContract(
        name="gmail",
        runtime_script_name="gmail_mcp_server.py",
        expected_env_keys=("GMAIL_TOKEN", "GMAIL_USER_ID", "GMAIL_API_BASE"),
        install=_gmail_install,
        seed_saved_config=_gmail_seed_saved_config,
        patch_validate_success=_patch_gmail_validate_success,
        patch_validate_failure=_patch_gmail_validate_failure,
        validate=_gmail_validate,
    ),
)


@pytest.mark.parametrize("contract", CONNECTOR_CONTRACTS, ids=lambda contract: contract.name)
def test_connector_contract_install_persists_runtime_and_skill(tmp_path: Path, contract: ConnectorContract) -> None:
    config_path = tmp_path / "config.json"
    _make_config(config_path)

    service = AdminService(config_path=config_path)
    result = contract.install(service)

    preset = get_connector_preset(contract.name)
    saved = load_config(config_path)
    server = saved.tools.mcp_servers[preset.server_name]
    runtime_script = config_path.parent / "runtime" / contract.runtime_script_name

    assert result["connector"] == contract.name
    assert server.command == "python3"
    assert server.args == [str(runtime_script)]
    assert server.connector_status == "pending"
    assert runtime_script.exists()
    assert (config_path.parent / "workspace" / "skills" / preset.skill_name / "SKILL.md").exists()
    for key in contract.expected_env_keys:
        assert server.env.get(key)


@pytest.mark.parametrize("contract", CONNECTOR_CONTRACTS, ids=lambda contract: contract.name)
def test_connector_contract_validate_success_marks_connected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contract: ConnectorContract,
) -> None:
    config_path = tmp_path / "config.json"
    _make_config(config_path)
    contract.seed_saved_config(config_path)
    contract.patch_validate_success(monkeypatch)

    service = AdminService(config_path=config_path)
    result = contract.validate(service)

    preset = get_connector_preset(contract.name)
    saved = load_config(config_path)
    server = saved.tools.mcp_servers[preset.server_name]

    assert result["status"] == "ok"
    assert server.connector_status == "connected"
    assert server.command == "python3"
    assert server.args == [str(config_path.parent / "runtime" / contract.runtime_script_name)]


@pytest.mark.parametrize("contract", CONNECTOR_CONTRACTS, ids=lambda contract: contract.name)
def test_connector_contract_validate_failure_marks_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    contract: ConnectorContract,
) -> None:
    config_path = tmp_path / "config.json"
    _make_config(config_path)
    contract.seed_saved_config(config_path)
    contract.patch_validate_failure(monkeypatch)

    service = AdminService(config_path=config_path)
    result = contract.validate(service)

    preset = get_connector_preset(contract.name)
    saved = load_config(config_path)
    server = saved.tools.mcp_servers[preset.server_name]

    assert result["status"] == "error"
    assert server.connector_status == "error"
    assert server.command == "python3"
    assert server.args == [str(config_path.parent / "runtime" / contract.runtime_script_name)]


def test_registered_connector_presets_have_unique_identity_and_skill_files() -> None:
    presets = list_connector_presets()

    assert presets
    assert len({preset.name for preset in presets}) == len(presets)
    assert len({preset.server_name for preset in presets}) == len(presets)
    assert len({preset.skill_name for preset in presets}) == len(presets)

    skills_root = Path(__file__).resolve().parent.parent / "nanobot" / "skills"
    for preset in presets:
        assert preset.name == preset.name.strip().lower()
        assert preset.server_name == preset.server_name.strip().lower()
        assert preset.display_name.strip()
        assert preset.description.strip()
        assert (skills_root / preset.skill_name / "SKILL.md").exists()
