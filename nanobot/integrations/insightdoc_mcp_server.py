"""InsightDOC MCP server for the built-in InsightDOC connector preset."""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

INSIGHTDOC_API_BASE_DEFAULT = "https://127.0.0.1/api/v1"
INSIGHTDOC_EXTERNAL_BASE_DEFAULT = "https://127.0.0.1/api/v1/external"
INSIGHTDOC_USER_AGENT = "nanobot-insightdoc-connector/1.0"


def _parse_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "results", "jobs", "documents", "schemas", "integrations"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


@dataclass
class InsightDOCClient:
    """Small InsightDOC REST API client used by the MCP server and validation flow."""

    token: str
    api_base: str = INSIGHTDOC_API_BASE_DEFAULT
    external_base_url: str = INSIGHTDOC_EXTERNAL_BASE_DEFAULT
    default_job_name: str = ""
    default_schema_id: str = ""
    default_integration_name: str = ""
    curl_insecure: bool = False
    transport: httpx.BaseTransport | None = None

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.external_base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "User-Agent": INSIGHTDOC_USER_AGENT,
            },
            timeout=20.0,
            verify=not self.curl_insecure,
            transport=self.transport,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: Any | None = None,
    ) -> Any:
        if not self.token:
            raise ValueError("InsightDOC token is required")
        with self._client() as client:
            response = client.request(method, path, params=params, json=json_data, data=data, files=files)
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()

    def list_jobs(self) -> Any:
        return self._request("GET", "/jobs")

    def create_job(
        self,
        name: str | None = None,
        *,
        description: str = "Created by agent",
        schema_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_name = _normalize_text(name) or _normalize_text(self.default_job_name)
        if not resolved_name:
            raise ValueError("InsightDOC job name is required")
        payload: dict[str, Any] = {
            "name": resolved_name,
            "description": str(description or "").strip() or "Created by agent",
            "schema_id": _normalize_text(schema_id) or _normalize_text(self.default_schema_id),
        }
        return self._request("POST", "/jobs", json_data=payload)

    def upload_document(
        self,
        job_id: str,
        file_path: str,
    ) -> dict[str, Any]:
        path = Path(str(file_path or "").strip()).expanduser()
        if not path.is_file():
            raise ValueError(f"File not found: {file_path}")
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as handle:
            return self._request(
                "POST",
                f"/jobs/{str(job_id).strip()}/documents",
                files={"file": (path.name, handle, mime_type)},
            )

    def list_schemas(self) -> Any:
        return self._request("GET", "/schemas")

    def process_document(
        self,
        document_id: str,
        *,
        schema_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {"schema_id": _normalize_text(schema_id) or _normalize_text(self.default_schema_id)}
        return self._request("POST", f"/documents/{str(document_id).strip()}/process", json_data=payload)

    def get_document_status(self, document_id: str) -> dict[str, Any]:
        return self._request("GET", f"/documents/{str(document_id).strip()}/status")

    def review_document(self, document_id: str, *, reviewed_data: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/documents/{str(document_id).strip()}/review",
            json_data={"reviewed_data": reviewed_data},
        )

    def confirm_document(self, document_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/documents/{str(document_id).strip()}/decision",
            json_data={"decision": "confirm"},
        )

    def reject_document(self, document_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/documents/{str(document_id).strip()}/decision",
            json_data={"decision": "reject"},
        )

    def list_integrations(self) -> Any:
        return self._request("GET", "/integrations")

    def send_job_to_integration(
        self,
        job_id: str,
        *,
        integration_name: str | None = None,
        include_unconfirmed: bool = False,
    ) -> dict[str, Any]:
        resolved_name = _normalize_text(integration_name) or _normalize_text(self.default_integration_name)
        if not resolved_name:
            raise ValueError("InsightDOC integration name is required")
        return self._request(
            "POST",
            f"/jobs/{str(job_id).strip()}/send-integration",
            json_data={
                "integration_name": resolved_name,
                "include_unconfirmed": bool(include_unconfirmed),
            },
        )


def _client_from_env() -> InsightDOCClient:
    return InsightDOCClient(
        token=str(os.environ.get("INSIGHTOCR_API_TOKEN") or "").strip(),
        api_base=str(os.environ.get("INSIGHTOCR_API_BASE_URL") or INSIGHTDOC_API_BASE_DEFAULT).strip() or INSIGHTDOC_API_BASE_DEFAULT,
        external_base_url=str(os.environ.get("INSIGHTOCR_EXTERNAL_BASE_URL") or INSIGHTDOC_EXTERNAL_BASE_DEFAULT).strip() or INSIGHTDOC_EXTERNAL_BASE_DEFAULT,
        default_job_name=str(os.environ.get("INSIGHTOCR_DEFAULT_JOB_NAME") or "").strip(),
        default_schema_id=str(os.environ.get("INSIGHTOCR_DEFAULT_SCHEMA_ID") or "").strip(),
        default_integration_name=str(os.environ.get("INSIGHTOCR_DEFAULT_INTEGRATION_NAME") or "").strip(),
        curl_insecure=_parse_bool(os.environ.get("CURL_INSECURE")),
    )


def _connector_context() -> dict[str, Any]:
    default_job_name = str(os.environ.get("INSIGHTOCR_DEFAULT_JOB_NAME") or "").strip() or None
    default_schema_id = str(os.environ.get("INSIGHTOCR_DEFAULT_SCHEMA_ID") or "").strip() or None
    default_integration_name = str(os.environ.get("INSIGHTOCR_DEFAULT_INTEGRATION_NAME") or "").strip() or None
    return {
        "api_base_url": str(os.environ.get("INSIGHTOCR_API_BASE_URL") or INSIGHTDOC_API_BASE_DEFAULT).strip() or INSIGHTDOC_API_BASE_DEFAULT,
        "external_base_url": str(os.environ.get("INSIGHTOCR_EXTERNAL_BASE_URL") or INSIGHTDOC_EXTERNAL_BASE_DEFAULT).strip() or INSIGHTDOC_EXTERNAL_BASE_DEFAULT,
        "has_token": bool(str(os.environ.get("INSIGHTOCR_API_TOKEN") or "").strip()),
        "default_job_name": default_job_name,
        "default_schema_id": default_schema_id,
        "default_integration_name": default_integration_name,
        "effective_job_name": default_job_name,
        "effective_schema_id": default_schema_id,
        "effective_integration_name": default_integration_name,
        "curl_insecure": _parse_bool(os.environ.get("CURL_INSECURE")),
    }


mcp = FastMCP(
    "insightdoc-connector",
    instructions=(
        "InsightDOC connector for job management, document upload, OCR processing, review, decision, "
        "and integration dispatch workflows. Use the tools for structured workflow access instead of ad-hoc scraping."
    ),
)


@mcp.tool(description="List InsightDOC jobs for the current account.")
def list_jobs() -> Any:
    return _client_from_env().list_jobs()


@mcp.tool(description="Create a new InsightDOC job. If name is omitted, use the configured default job name.")
def create_job(name: str | None = None, description: str = "Created by agent", schema_id: str | None = None) -> dict[str, Any]:
    return _client_from_env().create_job(name=name, description=description, schema_id=schema_id)


@mcp.tool(description="Upload one document into an InsightDOC job.")
def upload_document(job_id: str, file_path: str) -> dict[str, Any]:
    return _client_from_env().upload_document(job_id=job_id, file_path=file_path)


@mcp.tool(description="List InsightDOC schemas.")
def list_schemas() -> Any:
    return _client_from_env().list_schemas()


@mcp.tool(description="Process one InsightDOC document. If schema_id is omitted, use the configured default schema id.")
def process_document(document_id: str, schema_id: str | None = None) -> dict[str, Any]:
    return _client_from_env().process_document(document_id=document_id, schema_id=schema_id)


@mcp.tool(description="Get the processing status of one InsightDOC document.")
def get_document_status(document_id: str) -> dict[str, Any]:
    return _client_from_env().get_document_status(document_id=document_id)


@mcp.tool(description="Submit reviewed OCR data for one InsightDOC document.")
def review_document(document_id: str, reviewed_data: dict[str, Any]) -> dict[str, Any]:
    return _client_from_env().review_document(document_id=document_id, reviewed_data=reviewed_data)


@mcp.tool(description="Confirm one InsightDOC document.")
def confirm_document(document_id: str) -> dict[str, Any]:
    return _client_from_env().confirm_document(document_id=document_id)


@mcp.tool(description="Reject one InsightDOC document.")
def reject_document(document_id: str) -> dict[str, Any]:
    return _client_from_env().reject_document(document_id=document_id)


@mcp.tool(description="List InsightDOC integrations.")
def list_integrations() -> Any:
    return _client_from_env().list_integrations()


@mcp.tool(description="Send one InsightDOC job to an integration.")
def send_job_to_integration(job_id: str, integration_name: str | None = None, include_unconfirmed: bool = False) -> dict[str, Any]:
    return _client_from_env().send_job_to_integration(
        job_id=job_id,
        integration_name=integration_name,
        include_unconfirmed=include_unconfirmed,
    )


@mcp.tool(description="Return the InsightDOC connector runtime context, including defaults and effective selections.")
def get_connector_context() -> dict[str, Any]:
    return _connector_context()


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
