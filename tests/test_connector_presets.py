from nanobot.admin.connectors import (
    GITHUB_CONNECTOR_PRESET,
    NOTION_CONNECTOR_PRESET,
    build_github_stdio_server_config,
    build_notion_stdio_server_config,
    get_connector_preset,
    list_connector_presets,
)


def test_github_connector_preset_is_registered() -> None:
    presets = list_connector_presets()
    assert any(preset.name == "github" for preset in presets)
    assert get_connector_preset("github") == GITHUB_CONNECTOR_PRESET
    assert any(preset.name == "notion" for preset in presets)
    assert get_connector_preset("notion") == NOTION_CONNECTOR_PRESET


def test_github_stdio_server_config_uses_portable_python3_command() -> None:
    config = build_github_stdio_server_config(
        token="ghp_example",
        default_repo="owner/repo",
        api_base="https://api.github.com",
    )

    assert config["type"] == "stdio"
    assert config["command"] == "python3"
    assert config["args"] == ["-m", "nanobot.integrations.github_mcp_server"]
    assert config["env"]["GITHUB_TOKEN"] == "ghp_example"
    assert config["env"]["GITHUB_DEFAULT_REPO"] == "owner/repo"
    assert config["env"]["GITHUB_API_BASE"] == "https://api.github.com"


def test_github_stdio_server_config_accepts_runtime_script_path() -> None:
    config = build_github_stdio_server_config(
        token="ghp_example",
        script_path="/tmp/github_mcp_server.py",
    )

    assert config["command"] == "python3"
    assert config["args"] == ["/tmp/github_mcp_server.py"]


def test_notion_stdio_server_config_accepts_runtime_script_path() -> None:
    config = build_notion_stdio_server_config(
        token="secret_example",
        default_page_id="page-123",
        api_base="https://api.notion.com/v1",
        notion_version="2026-03-11",
        script_path="/tmp/notion_mcp_server.py",
    )

    assert config["type"] == "stdio"
    assert config["command"] == "python3"
    assert config["args"] == ["/tmp/notion_mcp_server.py"]
    assert config["env"]["NOTION_TOKEN"] == "secret_example"
    assert config["env"]["NOTION_DEFAULT_PAGE_ID"] == "page-123"
    assert config["env"]["NOTION_API_BASE"] == "https://api.notion.com/v1"
    assert config["env"]["NOTION_VERSION"] == "2026-03-11"
