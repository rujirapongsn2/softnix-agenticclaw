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


def list_connector_presets() -> list[ConnectorPreset]:
    """Return the built-in connector presets."""
    return [GITHUB_CONNECTOR_PRESET]


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
