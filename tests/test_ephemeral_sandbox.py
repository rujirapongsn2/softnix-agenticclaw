from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import RuntimeConfig
from nanobot.providers.base import LLMResponse, ToolCallRequest
from nanobot.runtime.ephemeral_runner import DockerEphemeralTaskRunner


@pytest.mark.asyncio
async def test_agent_loop_delegates_tool_tasks_to_ephemeral_runner(tmp_path: Path) -> None:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat = AsyncMock(
        return_value=LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(id="call-1", name="read_file", arguments={"path": "README.md"})],
        )
    )
    runner = MagicMock()
    runner.run_messages = AsyncMock(return_value="delegated result")

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        tool_task_runner=runner,
        tool_execution_strategy="tool_ephemeral",
        enable_interactive_tools=False,
    )

    result = await loop.process_direct("Read the file", session_key="cli:test")

    assert result == "delegated result"
    runner.run_messages.assert_awaited_once()


@pytest.mark.asyncio
async def test_ephemeral_runner_invokes_docker_task_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    runtime = RuntimeConfig()
    runtime.sandbox.image = "softnixclaw:latest"
    runtime.sandbox.network_policy = "default"

    runner = DockerEphemeralTaskRunner(
        config_path=config_path,
        workspace=workspace,
        runtime=runtime,
    )

    def _fake_run(cmd, capture_output, text, timeout, check):
        output_file = Path(cmd[cmd.index("--output-file") + 1])
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps({"content": "ok"}, ensure_ascii=False), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("nanobot.runtime.ephemeral_runner.subprocess.run", side_effect=_fake_run) as mocked_run:
        result = await runner.run_messages([{"role": "user", "content": "hello"}], session_key="cli:test", channel="cli", chat_id="direct")

    assert result == "ok"
    docker_cmd = mocked_run.call_args.args[0]
    assert docker_cmd[:3] == ["docker", "run", "--rm"]
    assert "task-run" in docker_cmd
    assert "--messages-file" in docker_cmd
    assert "--output-file" in docker_cmd
