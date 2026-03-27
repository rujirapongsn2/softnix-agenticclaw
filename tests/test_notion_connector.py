import httpx

from nanobot.admin.server import resolve_admin_post
from nanobot.admin.connectors import build_notion_stdio_server_config
from nanobot.admin.service import AdminService
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config, MCPServerConfig
from nanobot.integrations.notion_mcp_server import NotionClient, normalize_notion_target_id


def test_notion_client_uses_expected_routes() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/v1/users/me":
            return httpx.Response(200, json={"object": "user", "name": "Workspace Bot"})
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={"results": [{"id": "page-1", "object": "page"}]})
        if request.url.path == "/v1/pages/page-1":
            return httpx.Response(200, json={"id": "page-1", "object": "page"})
        if request.url.path == "/v1/blocks/page-1/children":
            return httpx.Response(200, json={"results": [{"id": "block-1", "type": "paragraph"}]})
        if request.url.path == "/v1/data-sources/db-1":
            return httpx.Response(200, json={"id": "db-1", "object": "data_source"})
        if request.url.path == "/v1/databases/db-1":
            return httpx.Response(200, json={"id": "db-1", "object": "database"})
        return httpx.Response(404, json={"message": "not found"})

    client = NotionClient(
        token="secret_example",
        default_page_id="page-1",
        transport=httpx.MockTransport(handler),
    )

    assert client.whoami()["name"] == "Workspace Bot"
    assert client.search("roadmap")["results"][0]["id"] == "page-1"
    assert client.get_page()["id"] == "page-1"
    assert client.get_block_children()["results"][0]["id"] == "block-1"
    assert client.get_data_source("db-1")["id"] == "db-1"
    assert client.get_database("db-1")["id"] == "db-1"
    assert requests[0] == ("GET", "/v1/users/me")
    assert requests[1] == ("POST", "/v1/search")


def test_notion_client_normalizes_page_url_to_page_id() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/v1/pages/30ac48c040d58074ab8de60fab6d6dd8":
            return httpx.Response(200, json={"id": "30ac48c040d58074ab8de60fab6d6dd8", "object": "page"})
        return httpx.Response(404, json={"message": "not found"})

    client = NotionClient(
        token="secret_example",
        default_page_id="https://www.notion.so/Workspace/Page-Title-30ac48c040d58074ab8de60fab6d6dd8",
        transport=httpx.MockTransport(handler),
    )

    assert client.get_page()["id"] == "30ac48c040d58074ab8de60fab6d6dd8"
    assert requests == ["/v1/pages/30ac48c040d58074ab8de60fab6d6dd8"]


def test_normalize_notion_target_id_extracts_id_from_url() -> None:
    assert normalize_notion_target_id("https://www.notion.so/Workspace/Page-Title-30ac48c040d58074ab8de60fab6d6dd8") == "30ac48c040d58074ab8de60fab6d6dd8"
    assert normalize_notion_target_id("30ac48c040d58074ab8de60fab6d6dd8") == "30ac48c040d58074ab8de60fab6d6dd8"


def test_admin_service_installs_notion_connector(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    result = service.install_notion_connector(
        instance_id="default",
        token="secret_example",
        default_page_id="page-1",
        notion_version="2026-03-11",
    )

    installed = result["instance"]
    saved = load_config(config_path)

    assert result["connector"] == "notion"
    assert result["server_name"] == "notion"
    assert installed["mcp"]["server_count"] == 1
    assert installed["mcp"]["servers"][0]["name"] == "notion"
    assert installed["mcp"]["servers"][0]["type"] == "stdio"
    assert installed["mcp"]["servers"][0]["status"] == "pending"
    assert saved.tools.mcp_servers["notion"].command == "python3"
    assert saved.tools.mcp_servers["notion"].args == [str(config_path.parent / "runtime" / "notion_mcp_server.py")]
    assert saved.tools.mcp_servers["notion"].env["NOTION_TOKEN"] == "secret_example"
    assert saved.tools.mcp_servers["notion"].env["NOTION_API_BASE"] == "https://api.notion.com/v1"
    assert saved.tools.mcp_servers["notion"].connector_status == "pending"
    assert (workspace / "skills" / "notion-connector" / "SKILL.md").exists()
    assert (config_path.parent / "runtime" / "notion_mcp_server.py").exists()


def test_admin_service_installs_notion_connector_from_page_url(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    result = service.install_notion_connector(
        instance_id="default",
        token="secret_example",
        default_page_id="https://www.notion.so/Workspace/Page-Title-30ac48c040d58074ab8de60fab6d6dd8",
        notion_version="2026-03-11",
    )

    saved = load_config(config_path)
    assert result["connector"] == "notion"
    assert saved.tools.mcp_servers["notion"].env["NOTION_DEFAULT_PAGE_ID"] == "30ac48c040d58074ab8de60fab6d6dd8"
    assert saved.tools.mcp_servers["notion"].env["NOTION_API_BASE"] == "https://api.notion.com/v1"


def test_admin_service_validates_notion_connector(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

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

    service = AdminService(config_path=config_path)
    saved_config = load_config(config_path)
    saved_config.tools.mcp_servers["notion"] = MCPServerConfig.model_validate(
        build_notion_stdio_server_config(
            token="secret_saved",
            default_page_id="page-1",
            notion_version="2026-03-11",
        )
    )
    save_config(saved_config, config_path)
    result = service.validate_notion_connector(instance_id="default", token="")

    codes = {item["code"] for item in result["findings"]}
    assert result["status"] == "ok"
    assert "token_valid" in codes
    assert "page_visible" in codes
    saved_after = load_config(config_path)
    assert saved_after.tools.mcp_servers["notion"].command == "python3"
    assert saved_after.tools.mcp_servers["notion"].args == [str(config_path.parent / "runtime" / "notion_mcp_server.py")]
    assert saved_after.tools.mcp_servers["notion"].connector_status == "connected"
    assert saved_after.tools.mcp_servers["notion"].env["NOTION_API_BASE"] == "https://api.notion.com/v1"


def test_admin_service_validates_notion_connector_from_page_url(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

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

    service = AdminService(config_path=config_path)
    saved_config = load_config(config_path)
    saved_config.tools.mcp_servers["notion"] = MCPServerConfig.model_validate(
        build_notion_stdio_server_config(
            token="secret_saved",
            default_page_id="https://www.notion.so/Workspace/Page-Title-30ac48c040d58074ab8de60fab6d6dd8",
            notion_version="2026-03-11",
        )
    )
    save_config(saved_config, config_path)
    result = service.validate_notion_connector(instance_id="default", token="")

    codes = {item["code"] for item in result["findings"]}
    assert result["status"] == "ok"
    assert "page_visible" in codes
    saved_after = load_config(config_path)
    assert saved_after.tools.mcp_servers["notion"].env["NOTION_DEFAULT_PAGE_ID"] == "30ac48c040d58074ab8de60fab6d6dd8"


def test_admin_service_validates_notion_database_id(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    class DummyNotionClient:
        def __init__(self, *, token: str, api_base: str, notion_version: str, default_page_id: str | None) -> None:
            self.token = token
            self.api_base = api_base
            self.notion_version = notion_version
            self.default_page_id = default_page_id

        def whoami(self) -> dict[str, str]:
            return {"name": "Workspace Bot"}

        def get_page(self, page_id: str | None = None) -> dict[str, str]:
            raise httpx.HTTPStatusError(
                "not found",
                request=httpx.Request("GET", "https://api.notion.com/v1/pages/db-1"),
                response=httpx.Response(404),
            )

        def get_data_source(self, data_source_id: str) -> dict[str, str]:
            raise httpx.HTTPStatusError(
                "not found",
                request=httpx.Request("GET", "https://api.notion.com/v1/data-sources/db-1"),
                response=httpx.Response(404),
            )

        def get_database(self, database_id: str) -> dict[str, str]:
            return {"id": database_id}

    monkeypatch.setattr("nanobot.admin.service.NotionClient", DummyNotionClient)

    service = AdminService(config_path=config_path)
    saved_config = load_config(config_path)
    saved_config.tools.mcp_servers["notion"] = MCPServerConfig.model_validate(
        build_notion_stdio_server_config(
            token="secret_saved",
            default_page_id="db-1",
        )
    )
    save_config(saved_config, config_path)
    result = service.validate_notion_connector(instance_id="default", token="")

    codes = {item["code"] for item in result["findings"]}
    assert result["status"] == "ok"
    assert "database_visible" in codes
    saved_after = load_config(config_path)
    assert saved_after.tools.mcp_servers["notion"].connector_status == "connected"
    assert saved_after.tools.mcp_servers["notion"].env["NOTION_API_BASE"] == "https://api.notion.com/v1"


def test_admin_service_marks_notion_connector_error_on_failed_validation(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

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

    service = AdminService(config_path=config_path)
    saved_config = load_config(config_path)
    saved_config.tools.mcp_servers["notion"] = MCPServerConfig.model_validate(
        build_notion_stdio_server_config(
            token="secret_saved",
            default_page_id="page-1",
        )
    )
    save_config(saved_config, config_path)
    result = service.validate_notion_connector(instance_id="default", token="")

    assert result["status"] == "error"
    saved_after = load_config(config_path)
    assert saved_after.tools.mcp_servers["notion"].connector_status == "error"


def test_admin_service_marks_notion_connector_warning_on_missing_target(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    class DummyNotionClient:
        def __init__(self, *, token: str, api_base: str, notion_version: str, default_page_id: str | None) -> None:
            self.token = token
            self.api_base = api_base
            self.notion_version = notion_version
            self.default_page_id = default_page_id

        def whoami(self) -> dict[str, str]:
            return {"name": "Workspace Bot"}

        def get_page(self, page_id: str | None = None) -> dict[str, str]:
            raise httpx.HTTPStatusError(
                "not found",
                request=httpx.Request("GET", "https://api.notion.com/v1/pages/unknown"),
                response=httpx.Response(404),
            )

        def get_data_source(self, data_source_id: str) -> dict[str, str]:
            raise httpx.HTTPStatusError(
                "not found",
                request=httpx.Request("GET", "https://api.notion.com/v1/data-sources/unknown"),
                response=httpx.Response(404),
            )

        def get_database(self, database_id: str) -> dict[str, str]:
            raise httpx.HTTPStatusError(
                "not found",
                request=httpx.Request("GET", "https://api.notion.com/v1/databases/unknown"),
                response=httpx.Response(404),
            )

    monkeypatch.setattr("nanobot.admin.service.NotionClient", DummyNotionClient)

    service = AdminService(config_path=config_path)
    saved_config = load_config(config_path)
    saved_config.tools.mcp_servers["notion"] = MCPServerConfig.model_validate(
        build_notion_stdio_server_config(
            token="secret_saved",
            default_page_id="unknown",
        )
    )
    save_config(saved_config, config_path)
    result = service.validate_notion_connector(instance_id="default", token="")

    assert result["status"] == "warning"
    assert any(item["code"] == "notion_target_unavailable" for item in result["findings"])


def test_admin_server_routes_notion_connector_endpoints() -> None:
    class DummyService:
        def install_notion_connector(self, **kwargs):  # noqa: ANN003
            return kwargs

        def validate_notion_connector(self, **kwargs):  # noqa: ANN003
            return kwargs

    status, payload = resolve_admin_post(
        DummyService(),
        "/admin/connectors/notion/install",
        {"instance_id": "default", "token": "secret_example"},
    )
    assert status.name == "OK"
    assert payload["instance_id"] == "default"

    status, payload = resolve_admin_post(
        DummyService(),
        "/admin/connectors/notion/validate",
        {"instance_id": "default", "token": "secret_example"},
    )
    assert status.name == "OK"
    assert payload["instance_id"] == "default"
