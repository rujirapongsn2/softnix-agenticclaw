---
name: composio-connector
description: Use the built-in Composio MCP connector for third-party app actions exposed through Composio.
---

# Composio Connector

Use this skill when the user asks for actions that are available through the Composio MCP connector.

## Rules

- Prefer dedicated connectors such as Gmail, GitHub, Notion, or InsightDOC when the task clearly belongs to those domains.
- Use the Composio connector for third-party apps and integrations that are exposed through Composio and are not already covered by a dedicated built-in connector.
- Treat the Composio MCP server as the source of available tools. Choose the narrowest tool that solves the task.
- If the requested app is not available through Composio, explain that the connector is not configured for that app and ask for a supported integration.

## Operating Notes

- The Composio connector uses a remote MCP endpoint.
- The API key is configured in the instance connector settings.
- Default endpoint and header values are managed by the connector preset.
