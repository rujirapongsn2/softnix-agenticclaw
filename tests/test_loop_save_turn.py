from nanobot.agent.context import ContextBuilder
from nanobot.agent.loop import AgentLoop
from nanobot.session.manager import Session
import json


def _mk_loop() -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    loop._TOOL_RESULT_MAX_CHARS = 500
    loop._ASSISTANT_CONTENT_MAX_CHARS = 40
    loop._TOOL_CALL_ARGS_MAX_CHARS = 30
    return loop


def test_save_turn_skips_multimodal_user_when_only_runtime_context() -> None:
    loop = _mk_loop()
    session = Session(key="test:runtime-only")
    runtime = ContextBuilder._RUNTIME_CONTEXT_TAG + "\nCurrent Time: now (UTC)"

    loop._save_turn(
        session,
        [{"role": "user", "content": [{"type": "text", "text": runtime}]}],
        skip=0,
    )
    assert session.messages == []


def test_save_turn_keeps_image_placeholder_after_runtime_strip() -> None:
    loop = _mk_loop()
    session = Session(key="test:image")
    runtime = ContextBuilder._RUNTIME_CONTEXT_TAG + "\nCurrent Time: now (UTC)"

    loop._save_turn(
        session,
        [{
            "role": "user",
            "content": [
                {"type": "text", "text": runtime},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        }],
        skip=0,
    )
    assert session.messages[0]["content"] == [{"type": "text", "text": "[image]"}]


def test_save_turn_truncates_large_assistant_content_and_tool_args() -> None:
    loop = _mk_loop()
    session = Session(key="test:assistant-compaction")

    loop._save_turn(
        session,
        [{
            "role": "assistant",
            "content": "A" * 100,
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "exec",
                    "arguments": json.dumps({"command": "B" * 100}, ensure_ascii=False),
                },
            }],
        }],
        skip=0,
    )

    saved = session.messages[0]
    assert saved["content"].endswith("\n... (truncated)")
    saved_args = saved["tool_calls"][0]["function"]["arguments"]
    parsed = json.loads(saved_args)
    assert parsed["_truncated"] is True
    assert "preview" in parsed
