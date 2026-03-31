---
name: insightdoc-connector
description: "Use the InsightDOC connector tools for job management, document upload, OCR processing, review, confirmation, rejection, and integration dispatch tasks."
metadata: {"nanobot":{"emoji":"🗂️","always":true}}
---

# InsightDOC Connector

Use the InsightDOC connector when the task is about InsightDOC workflows rather than a generic shell script or dashboard click path.

## Usage Rules

- Runtime tool names are prefixed as `mcp_insightdoc_*`; use those tool names when calling tools.
- If the user does not specify a job, schema, or integration, first check `mcp_insightdoc_get_connector_context` to see the configured defaults.
- Prefer the connector tools over ad-hoc scraping or raw shell commands when they are available.
- Summarize InsightDOC results clearly. Do not paste raw JSON unless the user asks for it.
- Use the connector for job creation, document upload, OCR processing, review, confirmation, rejection, and integration dispatch.
- Keep writes deliberate. Upload, review, confirm, reject, and send actions should only happen when the user explicitly asks for them.

## Common Patterns

- `mcp_insightdoc_get_connector_context` before default-omitted requests
- `mcp_insightdoc_list_jobs` for job discovery and validation
- `mcp_insightdoc_create_job` for new job creation
- `mcp_insightdoc_upload_document` for attaching a file to a job
- `mcp_insightdoc_list_schemas` for schema discovery
- `mcp_insightdoc_process_document` for OCR or extraction
- `mcp_insightdoc_get_document_status` for processing progress
- `mcp_insightdoc_review_document` for corrected OCR data
- `mcp_insightdoc_confirm_document` for approval
- `mcp_insightdoc_reject_document` for rejection
- `mcp_insightdoc_list_integrations` for integration discovery
- `mcp_insightdoc_send_job_to_integration` for dispatching a completed job

## Workflow Guidance

- Create or reuse a job before uploading documents.
- Upload documents before processing them.
- If a schema is known, pass it when creating or processing the job.
- Poll document status until extraction is ready before reviewing or confirming.
- Save corrections with `reviewed_data` before confirming a document.
- Prefer the connector's default job, schema, and integration values when the user does not specify alternatives.

## Safety

- Avoid write actions unless the user explicitly asks for them.
- Treat API tokens and document contents as sensitive.
- If a resource is unavailable or inaccessible, say that explicitly instead of guessing.
