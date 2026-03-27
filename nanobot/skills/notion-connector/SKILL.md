---
name: notion-connector
description: "Use the Notion connector tools for search, page retrieval, and workspace content-reading tasks."
metadata: {"nanobot":{"emoji":"📝","always":true}}
---

# Notion Connector

Use the Notion connector when the task is about searching Notion, reading a page, or inspecting page content.

## Usage Rules

- Runtime tool names are prefixed as `mcp_notion_*`; use those tool names when calling tools.
- If the user does not specify a page, first check `mcp_notion_get_connector_context` to see the configured `effective_page_id`.
- Only ask for a page ID when `mcp_notion_get_connector_context` shows no effective page ID and search does not identify the target.
- Prefer the Notion connector tools over ad-hoc web scraping when they are available.
- Summarize Notion content directly and clearly. Do not paste raw JSON unless the user asks for it.

## Common Patterns

- `mcp_notion_get_connector_context` before page-omitted requests
- `mcp_notion_whoami` for token validation
- `mcp_notion_search` to locate pages or data sources
- `mcp_notion_get_page` for page metadata
- `mcp_notion_get_block_children` to read page content
- `mcp_notion_get_data_source` for data source metadata
- `mcp_notion_get_database` for legacy or database-backed Notion targets

## Page Selection

- If the user does not specify a page, prefer the connector's `effective_page_id`.
- If no default page ID exists, use `mcp_notion_search` to find likely pages or data sources.
- If search is still ambiguous, ask the user which page or database to inspect.

## Safety

- Avoid write actions unless the user explicitly asks for them and the connector supports them.
- Treat token-backed workspace access as sensitive.
- If a page is not shared with the integration, say that explicitly instead of guessing.
