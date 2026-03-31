from __future__ import annotations

import httpx

from nanobot.admin.connectors import build_insightdoc_stdio_server_config
from nanobot.admin.service import AdminService
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config, MCPServerConfig
from nanobot.integrations.insightdoc_mcp_server import InsightDOCClient


def test_insightdoc_client_uses_expected_routes() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/v1/external/jobs":
            if request.method == "GET":
                return httpx.Response(200, json={"jobs": [{"id": "job-1"}]})
            if request.method == "POST":
                return httpx.Response(200, json={"id": "job-2"})
        if request.url.path == "/api/v1/external/jobs/job-1/documents" and request.method == "POST":
            return httpx.Response(200, json={"id": "doc-1"})
        if request.url.path == "/api/v1/external/schemas":
            return httpx.Response(200, json={"schemas": [{"id": "schema-1", "name": "Invoice Batch"}]})
        if request.url.path == "/api/v1/external/documents/doc-1/process":
            return httpx.Response(200, json={"id": "doc-1", "status": "processing"})
        if request.url.path == "/api/v1/external/documents/doc-1/status":
            return httpx.Response(200, json={"id": "doc-1", "status": "ready"})
        if request.url.path == "/api/v1/external/documents/doc-1/review":
            return httpx.Response(200, json={"id": "doc-1", "reviewed": True})
        if request.url.path == "/api/v1/external/documents/doc-1/decision":
            payload = request.content.decode("utf-8")
            decision = "confirm"
            if payload:
                import json

                decision = str(json.loads(payload).get("decision") or "confirm")
            return httpx.Response(200, json={"id": "doc-1", "decision": decision})
        if request.url.path == "/api/v1/external/integrations":
            return httpx.Response(200, json={"integrations": [{"id": "integration-1", "name": "Comply TOR"}]})
        if request.url.path == "/api/v1/external/jobs/job-1/send-integration":
            return httpx.Response(200, json={"id": "job-1", "sent": True})
        return httpx.Response(404, json={"message": "not found"})

    client = InsightDOCClient(
        token="sid_pat_example",
        api_base="https://127.0.0.1/api/v1",
        external_base_url="https://127.0.0.1/api/v1/external",
        default_job_name="Invoice Batch",
        default_schema_id="schema-1",
        default_integration_name="Comply TOR",
        curl_insecure=True,
        transport=httpx.MockTransport(handler),
    )

    assert client.list_jobs()["jobs"][0]["id"] == "job-1"
    assert client.create_job(name="Invoice Batch", description="Created by agent", schema_id="schema-1")["id"] == "job-2"
    assert client.upload_document("job-1", __file__)["id"] == "doc-1"
    assert client.list_schemas()["schemas"][0]["id"] == "schema-1"
    assert client.process_document("doc-1", schema_id="schema-1")["status"] == "processing"
    assert client.get_document_status("doc-1")["status"] == "ready"
    assert client.review_document("doc-1", reviewed_data={"invoice_number": "INV-001"})["reviewed"] is True
    assert client.confirm_document("doc-1")["decision"] == "confirm"
    assert client.reject_document("doc-1")["decision"] == "reject"
    assert client.list_integrations()["integrations"][0]["name"] == "Comply TOR"
    assert client.send_job_to_integration("job-1", integration_name="Comply TOR")["sent"] is True
    assert ("GET", "/api/v1/external/jobs") in requests
    assert ("POST", "/api/v1/external/jobs/job-1/documents") in requests


def test_admin_service_installs_insightdoc_connector(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    result = service.install_insightdoc_connector(
        instance_id="default",
        token="sid_pat_example",
        api_base_url="https://127.0.0.1/api/v1",
        external_base_url="https://127.0.0.1/api/v1/external",
        default_job_name="Invoice Batch",
        default_schema_id="schema-1",
        default_integration_name="Comply TOR",
        curl_insecure=True,
    )

    installed = result["instance"]
    saved = load_config(config_path)

    assert result["connector"] == "insightdoc"
    assert result["server_name"] == "insightdoc"
    assert installed["mcp"]["server_count"] == 1
    assert installed["mcp"]["servers"][0]["name"] == "insightdoc"
    assert installed["mcp"]["servers"][0]["type"] == "stdio"
    assert installed["mcp"]["servers"][0]["status"] == "pending"
    assert saved.tools.mcp_servers["insightdoc"].command == "python3"
    assert saved.tools.mcp_servers["insightdoc"].args == [str(config_path.parent / "runtime" / "insightdoc_mcp_server.py")]
    assert saved.tools.mcp_servers["insightdoc"].env["INSIGHTOCR_API_TOKEN"] == "sid_pat_example"
    assert saved.tools.mcp_servers["insightdoc"].env["INSIGHTOCR_API_BASE_URL"] == "https://127.0.0.1/api/v1"
    assert saved.tools.mcp_servers["insightdoc"].env["INSIGHTOCR_EXTERNAL_BASE_URL"] == "https://127.0.0.1/api/v1/external"
    assert saved.tools.mcp_servers["insightdoc"].env["INSIGHTOCR_DEFAULT_JOB_NAME"] == "Invoice Batch"
    assert saved.tools.mcp_servers["insightdoc"].env["INSIGHTOCR_DEFAULT_SCHEMA_ID"] == "schema-1"
    assert saved.tools.mcp_servers["insightdoc"].env["INSIGHTOCR_DEFAULT_INTEGRATION_NAME"] == "Comply TOR"
    assert saved.tools.mcp_servers["insightdoc"].env["CURL_INSECURE"] == "true"
    assert saved.tools.mcp_servers["insightdoc"].connector_status == "pending"
    assert (workspace / "skills" / "insightdoc-connector" / "SKILL.md").exists()
    assert (config_path.parent / "runtime" / "insightdoc_mcp_server.py").exists()


def test_admin_service_validates_insightdoc_connector(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

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

    service = AdminService(config_path=config_path)
    saved_config = load_config(config_path)
    saved_config.tools.mcp_servers["insightdoc"] = MCPServerConfig.model_validate(
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
    save_config(saved_config, config_path)
    result = service.validate_insightdoc_connector(instance_id="default", token="")

    codes = {item["code"] for item in result["findings"]}
    assert result["status"] == "ok"
    assert "token_valid" in codes
    assert "schemas_visible" in codes
    assert "integrations_visible" in codes
    assert "default_schema_visible" in codes
    assert "default_integration_visible" in codes
    saved_after = load_config(config_path)
    assert saved_after.tools.mcp_servers["insightdoc"].command == "python3"
    assert saved_after.tools.mcp_servers["insightdoc"].args == [str(config_path.parent / "runtime" / "insightdoc_mcp_server.py")]
    assert saved_after.tools.mcp_servers["insightdoc"].connector_status == "connected"
    assert saved_after.tools.mcp_servers["insightdoc"].env["INSIGHTOCR_API_BASE_URL"] == "https://127.0.0.1/api/v1"


def test_admin_service_marks_insightdoc_connector_error_on_failed_validation(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

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

    service = AdminService(config_path=config_path)
    saved_config = load_config(config_path)
    saved_config.tools.mcp_servers["insightdoc"] = MCPServerConfig.model_validate(
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
    save_config(saved_config, config_path)
    result = service.validate_insightdoc_connector(instance_id="default", token="")

    assert result["status"] == "error"
    saved_after = load_config(config_path)
    assert saved_after.tools.mcp_servers["insightdoc"].connector_status == "error"
