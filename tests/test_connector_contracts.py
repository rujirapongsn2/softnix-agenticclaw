from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from unittest.mock import AsyncMock

import httpx
import pytest

from nanobot.admin.connectors import build_composio_mcp_server_config, build_gmail_stdio_server_config, build_insightdoc_stdio_server_config, get_connector_preset, list_connector_presets
from nanobot.admin.service import AdminService, _probe_remote_mcp_server_async
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config, MCPServerConfig


@dataclass(frozen=True)
class ConnectorContract:
    name: str
    runtime_script_name: str | None
    expected_env_keys: tuple[str, ...]
    install: Callable[[AdminService], dict[str, Any]]
    seed_saved_config: Callable[[Path], None]
    patch_validate_success: Callable[[pytest.MonkeyPatch], None]
    patch_validate_failure: Callable[[pytest.MonkeyPatch], None]
    validate: Callable[[AdminService], dict[str, Any]]
    expected_transport: str = "stdio"
    expected_url: str | None = None
    expected_header_keys: tuple[str, ...] = ()


def _make_config(config_path: Path) -> None:
    workspace = config_path.parent / "workspace"
    workspace.mkdir()
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)


def test_probe_remote_mcp_server_supports_streamable_http_context_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyClientSession:
        def __init__(self, read: object, write: object) -> None:
            self.read = read
            self.write = write

        async def __aenter__(self) -> "DummyClientSession":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def initialize(self) -> None:
            return None

        async def list_tools(self):
            return type(
                "DummyToolsResult",
                (),
                {"tools": [type("DummyTool", (), {"name": "search_apps"})(), type("DummyTool", (), {"name": "list_connected_accounts"})()]},
            )()

    class DummyHttpClient:
        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> "DummyHttpClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    class DummyStreamableHttpContext:
        async def __aenter__(self):
            return ("read_stream", "write_stream", lambda: "session-1")

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr("nanobot.admin.service.httpx.AsyncClient", DummyHttpClient)
    monkeypatch.setattr("mcp.ClientSession", DummyClientSession)
    monkeypatch.setattr(
        "mcp.client.streamable_http.streamable_http_client",
        lambda *args, **kwargs: DummyStreamableHttpContext(),
    )

    result = asyncio.run(
        _probe_remote_mcp_server_async(
            MCPServerConfig.model_validate(
                build_composio_mcp_server_config(api_key="ck_example")
            )
        )
    )

    assert result["tool_count"] == 2
    assert result["tool_names"] == ["search_apps", "list_connected_accounts"]


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
        def __init__(
            self,
            *,
            token: str,
            api_base: str,
            user_id: str,
            refresh_token: str = "",
            client_id: str = "",
            client_secret: str = "",
            token_uri: str = "",
        ) -> None:
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
        def __init__(
            self,
            *,
            token: str,
            api_base: str,
            user_id: str,
            refresh_token: str = "",
            client_id: str = "",
            client_secret: str = "",
            token_uri: str = "",
        ) -> None:
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


def _composio_install(service: AdminService) -> dict[str, Any]:
    return service.install_composio_connector(
        instance_id="default",
        api_key="ck_example",
    )


def _composio_seed_saved_config(config_path: Path) -> None:
    config = load_config(config_path)
    config.tools.mcp_servers["composio"] = MCPServerConfig.model_validate(
        build_composio_mcp_server_config(
            api_key="ck_saved",
            url="https://connect.composio.dev/mcp",
        )
    )
    save_config(config, config_path)


def _patch_composio_validate_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "nanobot.admin.service._probe_remote_mcp_server_async",
        AsyncMock(return_value={"tool_count": 7, "tool_names": ["gmail_send", "slack_message"]}),
    )


def _patch_composio_validate_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "nanobot.admin.service._probe_remote_mcp_server_async",
        AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "unauthorized",
                request=httpx.Request("POST", "https://connect.composio.dev/mcp"),
                response=httpx.Response(401),
            )
        ),
    )


def _composio_validate(service: AdminService) -> dict[str, Any]:
    return service.validate_composio_connector(
        instance_id="default",
        api_key="",
    )


def _insightdoc_install(service: AdminService) -> dict[str, Any]:
    return service.install_insightdoc_connector(
        instance_id="default",
        token="sid_pat_example",
        api_base_url="https://127.0.0.1/api/v1",
        external_base_url="https://127.0.0.1/api/v1/external",
        default_job_name="Invoice Batch",
        default_schema_id="schema-1",
        default_integration_name="Comply TOR",
        curl_insecure=True,
    )


def _insightdoc_seed_saved_config(config_path: Path) -> None:
    config = load_config(config_path)
    config.tools.mcp_servers["insightdoc"] = MCPServerConfig.model_validate(
        build_insightdoc_stdio_server_config(
            token="sid_pat_saved",
            api_base_url="https://127.0.0.1/api/v1",
            external_base_url="https://127.0.0.1/api/v1/external",
            default_job_name="Invoice Batch",
            default_schema_id="schema-1",
            default_integration_name="Comply TOR",
            curl_insecure=True,
        )
    )
    save_config(config, config_path)


def _patch_insightdoc_validate_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyInsightDOCClient:
        def __init__(
            self,
            *,
            token: str,
            api_base: str,
            external_base_url: str,
            default_job_name: str,
            default_schema_id: str,
            default_integration_name: str,
            curl_insecure: bool,
        ) -> None:
            self.token = token
            self.api_base = api_base
            self.external_base_url = external_base_url
            self.default_job_name = default_job_name
            self.default_schema_id = default_schema_id
            self.default_integration_name = default_integration_name
            self.curl_insecure = curl_insecure

        def list_jobs(self) -> dict[str, list[dict[str, str]]]:
            return {"jobs": [{"id": "job-1"}]}

        def list_schemas(self) -> dict[str, list[dict[str, str]]]:
            return {"schemas": [{"id": "schema-1", "name": "Invoice Batch"}]}

        def list_integrations(self) -> dict[str, list[dict[str, str]]]:
            return {"integrations": [{"id": "integration-1", "name": "Comply TOR"}]}

    monkeypatch.setattr("nanobot.admin.service.InsightDOCClient", DummyInsightDOCClient)


def _patch_insightdoc_validate_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyInsightDOCClient:
        def __init__(
            self,
            *,
            token: str,
            api_base: str,
            external_base_url: str,
            default_job_name: str,
            default_schema_id: str,
            default_integration_name: str,
            curl_insecure: bool,
        ) -> None:
            self.token = token
            self.api_base = api_base
            self.external_base_url = external_base_url
            self.default_job_name = default_job_name
            self.default_schema_id = default_schema_id
            self.default_integration_name = default_integration_name
            self.curl_insecure = curl_insecure

        def list_jobs(self) -> dict[str, list[dict[str, str]]]:
            raise httpx.HTTPStatusError(
                "unauthorized",
                request=httpx.Request("GET", "https://127.0.0.1/api/v1/external/jobs"),
                response=httpx.Response(401),
            )

    monkeypatch.setattr("nanobot.admin.service.InsightDOCClient", DummyInsightDOCClient)


def _insightdoc_validate(service: AdminService) -> dict[str, Any]:
    return service.validate_insightdoc_connector(
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
    ConnectorContract(
        name="composio",
        runtime_script_name=None,
        expected_env_keys=(),
        expected_transport="streamableHttp",
        expected_url="https://connect.composio.dev/mcp",
        expected_header_keys=("x-consumer-api-key",),
        install=_composio_install,
        seed_saved_config=_composio_seed_saved_config,
        patch_validate_success=_patch_composio_validate_success,
        patch_validate_failure=_patch_composio_validate_failure,
        validate=_composio_validate,
    ),
    ConnectorContract(
        name="insightdoc",
        runtime_script_name="insightdoc_mcp_server.py",
        expected_env_keys=(
            "INSIGHTOCR_API_TOKEN",
            "INSIGHTOCR_API_BASE_URL",
            "INSIGHTOCR_EXTERNAL_BASE_URL",
            "INSIGHTOCR_DEFAULT_JOB_NAME",
            "INSIGHTOCR_DEFAULT_SCHEMA_ID",
            "INSIGHTOCR_DEFAULT_INTEGRATION_NAME",
            "CURL_INSECURE",
        ),
        install=_insightdoc_install,
        seed_saved_config=_insightdoc_seed_saved_config,
        patch_validate_success=_patch_insightdoc_validate_success,
        patch_validate_failure=_patch_insightdoc_validate_failure,
        validate=_insightdoc_validate,
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
    assert result["connector"] == contract.name
    assert server.connector_status == "pending"
    assert (config_path.parent / "workspace" / "skills" / preset.skill_name / "SKILL.md").exists()
    if contract.runtime_script_name:
        runtime_script = config_path.parent / "runtime" / contract.runtime_script_name
        assert server.command == "python3"
        assert server.args == [str(runtime_script)]
        assert runtime_script.exists()
        for key in contract.expected_env_keys:
            assert server.env.get(key)
    else:
        assert server.type == contract.expected_transport
        assert server.url == (contract.expected_url or "")
        for key in contract.expected_header_keys:
            assert server.headers.get(key)


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
    if contract.runtime_script_name:
        assert server.command == "python3"
        assert server.args == [str(config_path.parent / "runtime" / contract.runtime_script_name)]
    else:
        assert server.type == contract.expected_transport
        assert server.url == (contract.expected_url or "")


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
    if contract.runtime_script_name:
        assert server.command == "python3"
        assert server.args == [str(config_path.parent / "runtime" / contract.runtime_script_name)]
    else:
        assert server.type == contract.expected_transport
        assert server.url == (contract.expected_url or "")


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
