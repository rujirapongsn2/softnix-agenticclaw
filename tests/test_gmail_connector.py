import base64
import json

import httpx

from nanobot.admin.connectors import build_gmail_stdio_server_config
from nanobot.admin.service import AdminService
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config, MCPServerConfig
from nanobot.integrations.gmail_mcp_server import GmailClient


def test_gmail_client_uses_expected_routes() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.host == "oauth2.googleapis.com" and request.url.path == "/tokeninfo":
            return httpx.Response(200, json={"scope": "https://www.googleapis.com/auth/gmail.compose https://www.googleapis.com/auth/gmail.readonly"})
        if request.url.path == "/gmail/v1/users/me/profile":
            return httpx.Response(200, json={"emailAddress": "owner@example.com"})
        if request.url.path == "/gmail/v1/users/me/messages":
            return httpx.Response(200, json={"messages": [{"id": "msg-1"}]})
        if request.url.path == "/gmail/v1/users/me/messages/msg-1":
            return httpx.Response(200, json={"id": "msg-1", "snippet": "Hello"})
        if request.url.path == "/gmail/v1/users/me/threads/thread-1":
            return httpx.Response(200, json={"id": "thread-1", "messages": [{"id": "msg-1"}]})
        if request.url.path == "/gmail/v1/users/me/labels":
            return httpx.Response(200, json={"labels": [{"id": "INBOX"}]})
        if request.method == "POST" and request.url.path == "/gmail/v1/users/me/drafts":
            payload = json.loads(request.content.decode("utf-8"))
            raw = payload["message"]["raw"]
            message_text = base64.urlsafe_b64decode(_pad_base64(raw)).decode("utf-8")
            assert "From: owner@example.com" in message_text
            assert "To: receiver@example.com" in message_text
            assert "Subject: Draft subject" in message_text
            assert "Draft body" in message_text
            return httpx.Response(200, json={"id": "draft-1"})
        if request.method == "POST" and request.url.path == "/gmail/v1/users/me/messages/send":
            payload = json.loads(request.content.decode("utf-8"))
            raw = payload["raw"]
            message_text = base64.urlsafe_b64decode(_pad_base64(raw)).decode("utf-8")
            assert "From: owner@example.com" in message_text
            assert "To: receiver@example.com" in message_text
            assert "Subject: Send subject" in message_text
            assert "Send body" in message_text
            return httpx.Response(200, json={"id": "msg-send-1"})
        return httpx.Response(404, json={"message": "not found"})

    client = GmailClient(
        token="ya29_example",
        user_id="me",
        transport=httpx.MockTransport(handler),
    )

    assert client.whoami()["emailAddress"] == "owner@example.com"
    assert client.list_messages(query="is:unread")["messages"][0]["id"] == "msg-1"
    assert client.get_message("msg-1")["id"] == "msg-1"
    assert client.get_thread("thread-1")["id"] == "thread-1"
    assert client.list_labels()["labels"][0]["id"] == "INBOX"
    assert client.create_draft(to="receiver@example.com", subject="Draft subject", body="Draft body")["id"] == "draft-1"
    assert client.send_message(to="receiver@example.com", subject="Send subject", body="Send body")["id"] == "msg-send-1"
    assert ("GET", "/tokeninfo") in requests


def _pad_base64(raw: str) -> str:
    return raw + "=" * (-len(raw) % 4)


def test_gmail_client_requires_write_scope_for_draft_and_send() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com" and request.url.path == "/tokeninfo":
            return httpx.Response(200, json={"scope": "https://www.googleapis.com/auth/gmail.readonly"})
        return httpx.Response(200, json={"emailAddress": "owner@example.com"})

    client = GmailClient(
        token="ya29_example",
        user_id="me",
        transport=httpx.MockTransport(handler),
    )

    try:
        client.create_draft(to="receiver@example.com", subject="Draft subject", body="Draft body")
    except ValueError as exc:
        assert "write scope" in str(exc)
    else:
        raise AssertionError("Expected draft creation to require a Gmail write scope")

    try:
        client.send_message(to="receiver@example.com", subject="Send subject", body="Send body")
    except ValueError as exc:
        assert "write scope" in str(exc)
    else:
        raise AssertionError("Expected send_message to require a Gmail write scope")


def test_gmail_client_refreshes_expired_access_token_and_retries() -> None:
    requests: list[tuple[str, str, str | None]] = []
    tokeninfo_tokens: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth_header = request.headers.get("authorization")
        requests.append((request.method, request.url.path, auth_header))
        if request.url.host == "oauth2.googleapis.com" and request.url.path == "/tokeninfo":
            token = request.url.params.get("access_token")
            tokeninfo_tokens.append(token)
            if token == "expired-access-token":
                return httpx.Response(401, json={"error": {"message": "invalid_token"}})
            return httpx.Response(200, json={"scope": "https://www.googleapis.com/auth/gmail.compose"})
        if request.url.host == "oauth2.googleapis.com" and request.url.path == "/token":
            body = request.content.decode("utf-8")
            assert "grant_type=refresh_token" in body
            assert "refresh_token=refresh-token" in body
            assert "client_id=client-id" in body
            assert "client_secret=client-secret" in body
            return httpx.Response(200, json={"access_token": "fresh-access-token", "expires_in": 3600})
        if request.url.path == "/gmail/v1/users/me/profile":
            if auth_header == "Bearer expired-access-token":
                return httpx.Response(401, json={"error": {"message": "invalid_token"}})
            return httpx.Response(200, json={"emailAddress": "owner@example.com"})
        if request.method == "POST" and request.url.path == "/gmail/v1/users/me/drafts":
            payload = json.loads(request.content.decode("utf-8"))
            raw = payload["message"]["raw"]
            message_text = base64.urlsafe_b64decode(_pad_base64(raw)).decode("utf-8")
            assert "From: owner@example.com" in message_text
            return httpx.Response(200, json={"id": "draft-1"})
        return httpx.Response(404, json={"message": "not found"})

    client = GmailClient(
        token="expired-access-token",
        refresh_token="refresh-token",
        client_id="client-id",
        client_secret="client-secret",
        user_id="me",
        transport=httpx.MockTransport(handler),
    )

    assert client.create_draft(to="receiver@example.com", subject="Draft subject", body="Draft body")["id"] == "draft-1"
    assert client.token == "fresh-access-token"
    assert tokeninfo_tokens == ["fresh-access-token"]
    assert any(header == "Bearer expired-access-token" for _, path, header in requests if path == "/gmail/v1/users/me/profile")
    assert any(header == "Bearer fresh-access-token" for _, path, header in requests if path == "/gmail/v1/users/me/profile")


def test_admin_service_installs_gmail_connector(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    result = service.install_gmail_connector(
        instance_id="default",
        token="ya29_example",
        user_id="me",
    )

    installed = result["instance"]
    saved = load_config(config_path)

    assert result["connector"] == "gmail"
    assert result["server_name"] == "gmail"
    assert installed["mcp"]["server_count"] == 1
    assert installed["mcp"]["servers"][0]["name"] == "gmail"
    assert installed["mcp"]["servers"][0]["type"] == "stdio"
    assert installed["mcp"]["servers"][0]["status"] == "pending"
    assert saved.tools.mcp_servers["gmail"].command == "python3"
    assert saved.tools.mcp_servers["gmail"].args == [str(config_path.parent / "runtime" / "gmail_mcp_server.py")]
    assert saved.tools.mcp_servers["gmail"].env["GMAIL_TOKEN"] == "ya29_example"
    assert saved.tools.mcp_servers["gmail"].env["GMAIL_USER_ID"] == "me"
    assert saved.tools.mcp_servers["gmail"].env["GMAIL_API_BASE"] == "https://gmail.googleapis.com/gmail/v1"
    assert saved.tools.mcp_servers["gmail"].connector_status == "pending"
    assert (workspace / "skills" / "gmail-connector" / "SKILL.md").exists()
    assert (config_path.parent / "runtime" / "gmail_mcp_server.py").exists()


def test_admin_service_installs_gmail_connector_with_refresh_credentials(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    service.install_gmail_connector(
        instance_id="default",
        token="ya29_example",
        user_id="me",
        refresh_token="refresh-token",
        client_id="client-id",
        client_secret="client-secret",
        token_uri="https://oauth2.googleapis.com/token",
    )

    saved = load_config(config_path)
    env = saved.tools.mcp_servers["gmail"].env
    assert env["GMAIL_REFRESH_TOKEN"] == "refresh-token"
    assert env["GMAIL_CLIENT_ID"] == "client-id"
    assert env["GMAIL_CLIENT_SECRET"] == "client-secret"
    assert env["GMAIL_TOKEN_URI"] == "https://oauth2.googleapis.com/token"


def test_admin_service_validates_gmail_connector(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

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

    service = AdminService(config_path=config_path)
    saved_config = load_config(config_path)
    saved_config.tools.mcp_servers["gmail"] = MCPServerConfig.model_validate(
        build_gmail_stdio_server_config(
            token="ya29_saved",
            user_id="me",
        )
    )
    save_config(saved_config, config_path)
    result = service.validate_gmail_connector(instance_id="default", token="")

    codes = {item["code"] for item in result["findings"]}
    assert result["status"] == "ok"
    assert "token_valid" in codes
    assert "mailbox_visible" in codes
    saved_after = load_config(config_path)
    assert saved_after.tools.mcp_servers["gmail"].connector_status == "connected"


def test_admin_service_marks_gmail_connector_error_on_failed_validation(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

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

    service = AdminService(config_path=config_path)
    saved_config = load_config(config_path)
    saved_config.tools.mcp_servers["gmail"] = MCPServerConfig.model_validate(
        build_gmail_stdio_server_config(
            token="ya29_saved",
            user_id="me",
        )
    )
    save_config(saved_config, config_path)
    result = service.validate_gmail_connector(instance_id="default", token="")

    assert result["status"] == "error"
    saved_after = load_config(config_path)
    assert saved_after.tools.mcp_servers["gmail"].connector_status == "error"
