import httpx

from nanobot.admin.server import resolve_admin_get, resolve_admin_post
from nanobot.admin.connectors import build_github_stdio_server_config
from nanobot.admin.service import AdminService
from nanobot.config.loader import _migrate_config, load_config, save_config
from nanobot.config.schema import Config, MCPServerConfig
import nanobot.integrations.github_mcp_server as github_module
from nanobot.integrations.github_mcp_server import GitHubClient


def test_github_client_uses_expected_routes() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "octocat"})
        if request.url.path == "/repos/owner/repo":
            return httpx.Response(200, json={"full_name": "owner/repo"})
        if request.url.path == "/repos/owner/repo/issues":
            return httpx.Response(200, json=[{"number": 1, "title": "Issue"}])
        if request.url.path == "/repos/owner/repo/actions/runs":
            return httpx.Response(200, json={"workflow_runs": [{"id": 2, "name": "CI"}]})
        if request.url.path == "/repos/owner/repo/commits":
            return httpx.Response(200, json=[{"sha": "abc123", "commit": {"message": "latest"}}])
        if request.url.path == "/search/repositories":
            return httpx.Response(200, json={"items": [{"full_name": "owner/repo"}]})
        return httpx.Response(404, json={"message": "not found"})

    client = GitHubClient(
        token="ghp_example",
        default_repo="owner/repo",
        transport=httpx.MockTransport(handler),
    )

    assert client.whoami()["login"] == "octocat"
    assert client.get_repository()["full_name"] == "owner/repo"
    assert client.list_issues() == [{"number": 1, "title": "Issue"}]
    assert client.list_workflow_runs() == [{"id": 2, "name": "CI"}]
    assert client.list_commits() == [{"sha": "abc123", "commit": {"message": "latest"}}]
    assert client.get_latest_commit()["sha"] == "abc123"
    assert client.search_repositories("owner/repo") == [{"full_name": "owner/repo"}]
    assert requests[0] == ("GET", "/user")
    assert requests[1] == ("GET", "/repos/owner/repo")


def test_admin_service_installs_github_connector(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    result = service.install_github_connector(
        instance_id="default",
        token="ghp_example",
        default_repo="owner/repo",
    )

    installed = result["instance"]
    saved = load_config(config_path)

    assert result["connector"] == "github"
    assert result["server_name"] == "github"
    assert installed["mcp"]["server_count"] == 1
    assert installed["mcp"]["servers"][0]["name"] == "github"
    assert installed["mcp"]["servers"][0]["type"] == "stdio"
    assert installed["mcp"]["servers"][0]["status"] == "pending"
    assert saved.tools.mcp_servers["github"].command == "python3"
    assert saved.tools.mcp_servers["github"].args == [str(config_path.parent / "runtime" / "github_mcp_server.py")]
    assert saved.tools.mcp_servers["github"].env["GITHUB_TOKEN"] == "ghp_example"
    assert saved.tools.mcp_servers["github"].connector_status == "pending"
    assert (workspace / "skills" / "github-connector" / "SKILL.md").exists()
    assert (config_path.parent / "runtime" / "github_mcp_server.py").exists()


def test_admin_service_validates_github_connector(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

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

    service = AdminService(config_path=config_path)
    saved_config = load_config(config_path)
    saved_config.tools.mcp_servers["github"] = MCPServerConfig.model_validate(
        build_github_stdio_server_config(
            token="ghp_saved",
            default_repo="owner/repo",
        )
    )
    save_config(saved_config, config_path)
    result = service.validate_github_connector(
        instance_id="default",
        token="",
    )

    codes = {item["code"] for item in result["findings"]}
    assert result["status"] == "ok"
    assert "token_valid" in codes
    assert "repository_visible" in codes
    saved_after = load_config(config_path)
    assert saved_after.tools.mcp_servers["github"].command == "python3"
    assert saved_after.tools.mcp_servers["github"].args == [str(config_path.parent / "runtime" / "github_mcp_server.py")]
    assert saved_after.tools.mcp_servers["github"].connector_status == "connected"


def test_admin_service_marks_github_connector_error_on_failed_validation(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    class DummyErrorResponse:
        status_code = 401

    class DummyGitHubClient:
        def __init__(self, *, token: str, api_base: str, default_repo: str | None) -> None:
            self.token = token
            self.api_base = api_base
            self.default_repo = default_repo

        def whoami(self) -> dict[str, str]:
            raise httpx.HTTPStatusError("unauthorized", request=httpx.Request("GET", "https://api.github.com/user"), response=httpx.Response(401))

    monkeypatch.setattr("nanobot.admin.service.GitHubClient", DummyGitHubClient)

    service = AdminService(config_path=config_path)
    saved_config = load_config(config_path)
    saved_config.tools.mcp_servers["github"] = MCPServerConfig.model_validate(
        build_github_stdio_server_config(
            token="ghp_saved",
            default_repo="owner/repo",
        )
    )
    save_config(saved_config, config_path)
    result = service.validate_github_connector(
        instance_id="default",
        token="",
    )

    assert result["status"] == "error"
    saved_after = load_config(config_path)
    assert saved_after.tools.mcp_servers["github"].connector_status == "error"


def test_admin_server_routes_github_connector_endpoints() -> None:
    class DummyService:
        def list_connector_presets(self):  # noqa: ANN001
            return {"presets": [{"name": "github"}]}

        def install_github_connector(self, **kwargs):  # noqa: ANN003
            return kwargs

        def validate_github_connector(self, **kwargs):  # noqa: ANN003
            return kwargs

    status, payload = resolve_admin_get(DummyService(), "/admin/connectors/presets")
    assert status.name == "OK"
    assert payload["presets"][0]["name"] == "github"

    status, payload = resolve_admin_post(
        DummyService(),
        "/admin/connectors/github/install",
        {"instance_id": "default", "token": "ghp_example"},
    )
    assert status.name == "OK"
    assert payload["instance_id"] == "default"


def test_github_client_infers_repo_from_git_remote(monkeypatch) -> None:
    def fake_run(*args, **kwargs):  # noqa: ANN001, ANN002
        class Result:
            stdout = "git@github.com:owner/repo.git\n"

        return Result()

    github_module._discover_repo_from_git.cache_clear()
    monkeypatch.setattr("nanobot.integrations.github_mcp_server.subprocess.run", fake_run)

    client = GitHubClient(token="ghp_example", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[{"sha": "abc"}])))
    assert client._resolve_repo() == "owner/repo"


def test_github_connector_context_prefers_default_repo(monkeypatch) -> None:
    github_module._discover_repo_from_git.cache_clear()
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_example")
    monkeypatch.setenv("GITHUB_DEFAULT_REPO", "owner/default")
    monkeypatch.setenv("GITHUB_API_BASE", "https://api.github.com")
    monkeypatch.setattr(github_module, "_discover_repo_from_git", lambda: "owner/inferred")

    context = github_module.get_connector_context()

    assert context["has_token"] is True
    assert context["default_repo"] == "owner/default"
    assert context["inferred_repo"] == "owner/inferred"
    assert context["effective_repo"] == "owner/default"


def test_migrate_config_normalizes_github_connector_python_command() -> None:
    migrated = _migrate_config(
        {
            "tools": {
                "mcpServers": {
                    "github": {
                        "type": "stdio",
                        "command": "/opt/anaconda3/bin/python3",
                        "args": ["-m", "nanobot.integrations.github_mcp_server"],
                    }
                }
            }
        }
    )

    assert migrated["tools"]["mcpServers"]["github"]["command"] == "python3"
