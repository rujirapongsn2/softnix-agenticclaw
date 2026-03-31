from nanobot.admin.connectors import (
    GMAIL_CONNECTOR_PRESET,
    GITHUB_CONNECTOR_PRESET,
    INSIGHTDOC_CONNECTOR_PRESET,
    NOTION_CONNECTOR_PRESET,
    build_gmail_stdio_server_config,
    build_github_stdio_server_config,
    build_insightdoc_stdio_server_config,
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
    assert any(preset.name == "gmail" for preset in presets)
    assert get_connector_preset("gmail") == GMAIL_CONNECTOR_PRESET
    assert any(preset.name == "insightdoc" for preset in presets)
    assert get_connector_preset("insightdoc") == INSIGHTDOC_CONNECTOR_PRESET


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


def test_gmail_stdio_server_config_accepts_runtime_script_path() -> None:
    config = build_gmail_stdio_server_config(
        token="ya29.example",
        user_id="me",
        api_base="https://gmail.googleapis.com/gmail/v1",
        script_path="/tmp/gmail_mcp_server.py",
    )

    assert config["type"] == "stdio"
    assert config["command"] == "python3"
    assert config["args"] == ["/tmp/gmail_mcp_server.py"]
    assert config["env"]["GMAIL_TOKEN"] == "ya29.example"
    assert config["env"]["GMAIL_USER_ID"] == "me"
    assert config["env"]["GMAIL_API_BASE"] == "https://gmail.googleapis.com/gmail/v1"


def test_gmail_stdio_server_config_includes_refresh_credentials_when_present() -> None:
    config = build_gmail_stdio_server_config(
        token="ya29.example",
        user_id="me",
        api_base="https://gmail.googleapis.com/gmail/v1",
        refresh_token="1//refresh",
        client_id="client-id",
        client_secret="client-secret",
        token_uri="https://oauth2.googleapis.com/token",
    )

    assert config["env"]["GMAIL_REFRESH_TOKEN"] == "1//refresh"
    assert config["env"]["GMAIL_CLIENT_ID"] == "client-id"
    assert config["env"]["GMAIL_CLIENT_SECRET"] == "client-secret"
    assert config["env"]["GMAIL_TOKEN_URI"] == "https://oauth2.googleapis.com/token"


def test_insightdoc_stdio_server_config_accepts_runtime_script_path() -> None:
    config = build_insightdoc_stdio_server_config(
        token="sid_pat_example",
        api_base_url="https://127.0.0.1/api/v1",
        external_base_url="https://127.0.0.1/api/v1/external",
        default_job_name="Invoice Batch",
        default_schema_id="schema-1",
        default_integration_name="Comply TOR",
        curl_insecure=True,
        script_path="/tmp/insightdoc_mcp_server.py",
    )

    assert config["type"] == "stdio"
    assert config["command"] == "python3"
    assert config["args"] == ["/tmp/insightdoc_mcp_server.py"]
    assert config["env"]["INSIGHTOCR_API_TOKEN"] == "sid_pat_example"
    assert config["env"]["INSIGHTOCR_API_BASE_URL"] == "https://127.0.0.1/api/v1"
    assert config["env"]["INSIGHTOCR_EXTERNAL_BASE_URL"] == "https://127.0.0.1/api/v1/external"
    assert config["env"]["INSIGHTOCR_DEFAULT_JOB_NAME"] == "Invoice Batch"
    assert config["env"]["INSIGHTOCR_DEFAULT_SCHEMA_ID"] == "schema-1"
    assert config["env"]["INSIGHTOCR_DEFAULT_INTEGRATION_NAME"] == "Comply TOR"
    assert config["env"]["CURL_INSECURE"] == "true"
