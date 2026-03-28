"""Connector preset definitions for Softnix Admin."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConnectorPreset:
    """Descriptor for an installable connector preset."""

    name: str
    display_name: str
    description: str
    skill_name: str
    server_name: str


GITHUB_CONNECTOR_PRESET = ConnectorPreset(
    name="github",
    display_name="GitHub",
    description="Install a GitHub MCP server and companion skill for repo, issue, PR, and workflow tasks.",
    skill_name="github-connector",
    server_name="github",
)

NOTION_CONNECTOR_PRESET = ConnectorPreset(
    name="notion",
    display_name="Notion",
    description="Install a Notion MCP server and companion skill for search, page, and content-reading tasks.",
    skill_name="notion-connector",
    server_name="notion",
)

GMAIL_CONNECTOR_PRESET = ConnectorPreset(
    name="gmail",
    display_name="Gmail",
    description="Install a Gmail MCP server and companion skill for inbox search, thread, label, draft, and send tasks.",
    skill_name="gmail-connector",
    server_name="gmail",
)


def list_connector_presets() -> list[ConnectorPreset]:
    """Return the built-in connector presets."""
    return [GITHUB_CONNECTOR_PRESET, NOTION_CONNECTOR_PRESET, GMAIL_CONNECTOR_PRESET]


def get_connector_preset(name: str) -> ConnectorPreset:
    """Resolve one preset by name."""
    normalized = str(name or "").strip().lower()
    for preset in list_connector_presets():
        if preset.name == normalized:
            return preset
    raise ValueError(f"Unknown connector preset '{name}'")


def build_github_stdio_server_config(
    *,
    token: str,
    default_repo: str | None = None,
    api_base: str | None = None,
    tool_timeout: int = 30,
    script_path: str | None = None,
) -> dict[str, Any]:
    """Build the MCP server config payload for the GitHub connector."""
    env = {
        "GITHUB_TOKEN": str(token or "").strip(),
        "GITHUB_DEFAULT_REPO": str(default_repo or "").strip(),
        "GITHUB_API_BASE": str(api_base or "").strip(),
    }
    return {
        "type": "stdio",
        "command": "python3",
        "args": [str(script_path).strip()] if str(script_path or "").strip() else ["-m", "nanobot.integrations.github_mcp_server"],
        "env": {key: value for key, value in env.items() if value},
        "tool_timeout": int(tool_timeout),
    }


def build_notion_stdio_server_config(
    *,
    token: str,
    default_page_id: str | None = None,
    api_base: str | None = None,
    notion_version: str | None = None,
    tool_timeout: int = 30,
    script_path: str | None = None,
) -> dict[str, Any]:
    """Build the MCP server config payload for the Notion connector."""
    env = {
        "NOTION_TOKEN": str(token or "").strip(),
        "NOTION_DEFAULT_PAGE_ID": str(default_page_id or "").strip(),
        "NOTION_API_BASE": str(api_base or "https://api.notion.com/v1").strip() or "https://api.notion.com/v1",
        "NOTION_VERSION": str(notion_version or "2026-03-11").strip() or "2026-03-11",
    }
    return {
        "type": "stdio",
        "command": "python3",
        "args": [str(script_path).strip()] if str(script_path or "").strip() else ["-m", "nanobot.integrations.notion_mcp_server"],
        "env": {key: value for key, value in env.items() if value},
        "tool_timeout": int(tool_timeout),
    }


def build_gmail_stdio_server_config(
    *,
    token: str,
    user_id: str | None = None,
    api_base: str | None = None,
    refresh_token: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    token_uri: str | None = None,
    tool_timeout: int = 30,
    script_path: str | None = None,
) -> dict[str, Any]:
    """Build the MCP server config payload for the Gmail connector."""
    env = {
        "GMAIL_TOKEN": str(token or "").strip(),
        "GMAIL_USER_ID": str(user_id or "me").strip() or "me",
        "GMAIL_API_BASE": str(api_base or "https://gmail.googleapis.com/gmail/v1").strip() or "https://gmail.googleapis.com/gmail/v1",
        "GMAIL_REFRESH_TOKEN": str(refresh_token or "").strip(),
        "GMAIL_CLIENT_ID": str(client_id or "").strip(),
        "GMAIL_CLIENT_SECRET": str(client_secret or "").strip(),
        "GMAIL_TOKEN_URI": str(token_uri or "https://oauth2.googleapis.com/token").strip() or "https://oauth2.googleapis.com/token",
    }
    return {
        "type": "stdio",
        "command": "python3",
        "args": [str(script_path).strip()] if str(script_path or "").strip() else ["-m", "nanobot.integrations.gmail_mcp_server"],
        "env": {key: value for key, value in env.items() if value},
        "tool_timeout": int(tool_timeout),
    }
