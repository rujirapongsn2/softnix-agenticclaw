"""Tests for cache-friendly prompt construction."""

from __future__ import annotations

from datetime import datetime as real_datetime
from pathlib import Path
import datetime as datetime_module
import base64

from nanobot.agent.context import ContextBuilder


class _FakeDatetime(real_datetime):
    current = real_datetime(2026, 2, 24, 13, 59)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.current


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def test_system_prompt_stays_stable_when_clock_changes(tmp_path, monkeypatch) -> None:
    """System prompt should not change just because wall clock minute changes."""
    monkeypatch.setattr(datetime_module, "datetime", _FakeDatetime)

    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    _FakeDatetime.current = real_datetime(2026, 2, 24, 13, 59)
    prompt1 = builder.build_system_prompt()

    _FakeDatetime.current = real_datetime(2026, 2, 24, 14, 0)
    prompt2 = builder.build_system_prompt()

    assert prompt1 == prompt2


def test_system_prompt_includes_connector_routing_rules(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()

    assert "# Connector Routing" in prompt
    assert "Use the Notion connector" in prompt
    assert "Use the GitHub connector" in prompt


def test_runtime_context_is_separate_untrusted_user_message(tmp_path) -> None:
    """Runtime metadata should be merged with the user message."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="Return exactly: OK",
        channel="cli",
        chat_id="direct",
    )

    assert messages[0]["role"] == "system"
    assert "## Current Session" not in messages[0]["content"]

    # Runtime context is now merged with user message into a single message
    assert messages[-1]["role"] == "user"
    user_content = messages[-1]["content"]
    assert isinstance(user_content, str)
    assert ContextBuilder._RUNTIME_CONTEXT_TAG in user_content
    assert "Current Time:" in user_content
    assert "Channel: cli" in user_content
    assert "Chat ID: direct" in user_content
    assert "Return exactly: OK" in user_content


def test_image_messages_include_attachment_grounding_text(tmp_path) -> None:
    """Multimodal messages should instruct the model to use attached images as primary context."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)
    image_path = workspace / "receipt.png"
    image_path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jr1cAAAAASUVORK5CYII="
    ))

    messages = builder.build_messages(
        history=[],
        current_message="สรุปข้อมูลนี้ให้หน่อย",
        media=[str(image_path)],
        channel="softnix_app",
        chat_id="mobile-device",
    )

    user_content = messages[-1]["content"]
    assert isinstance(user_content, list)
    assert any(item.get("type") == "image_url" for item in user_content)
    text_items = [item for item in user_content if item.get("type") == "text"]
    assert text_items[0]["text"].startswith("[Attachment Context]")
    text_blocks = [item.get("text", "") for item in user_content if item.get("type") == "text"]
    merged_text = "\n".join(text_blocks)
    assert "[Attachment Context]" in merged_text
    assert "Base your answer primarily on the attached image content." in merged_text
    assert "receipt.png" in merged_text
    assert "สรุปข้อมูลนี้ให้หน่อย" in merged_text
    assert "The metadata above is not the user's request." in merged_text
