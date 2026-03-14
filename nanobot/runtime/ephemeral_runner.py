"""Ephemeral Docker runner for tool-using tasks."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

from nanobot.config.schema import RuntimeConfig


def _common_mount_root(paths: list[Path]) -> Path:
    resolved = [str(path.expanduser().resolve()) for path in paths]
    return Path(os.path.commonpath(resolved))


class DockerEphemeralTaskRunner:
    """Run a tool-using task inside a one-off Docker sandbox."""

    def __init__(
        self,
        *,
        config_path: Path,
        workspace: Path,
        runtime: RuntimeConfig,
    ):
        self.config_path = config_path.expanduser().resolve()
        self.workspace = workspace.expanduser().resolve()
        self.runtime = runtime

    async def run_messages(
        self,
        initial_messages: list[dict[str, Any]],
        *,
        session_key: str,
        channel: str,
        chat_id: str,
    ) -> str:
        sandbox = self.runtime.sandbox
        run_id = uuid.uuid4().hex[:12]
        mount_root = _common_mount_root([self.config_path, self.workspace])
        sandbox_home = self.workspace / ".sandbox-home"
        ephemeral_dir = self.workspace / ".nanobot" / "ephemeral"
        messages_file = ephemeral_dir / f"{run_id}-messages.json"
        output_file = ephemeral_dir / f"{run_id}-output.json"

        sandbox_home.mkdir(parents=True, exist_ok=True)
        (sandbox_home / ".cache" / "pip").mkdir(parents=True, exist_ok=True)
        ephemeral_dir.mkdir(parents=True, exist_ok=True)
        messages_file.write_text(json.dumps(initial_messages, ensure_ascii=False), encoding="utf-8")

        container_name = f"softnix-ephemeral-{run_id}"
        cmd = [
            "docker", "run", "--rm", "--init",
            "--name", container_name,
            "--hostname", container_name,
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--cap-drop=ALL",
            "--security-opt", "no-new-privileges",
            "--read-only",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,size={int(sandbox.tmpfs_size_mb)}m",
            "-e", f"HOME={sandbox_home}",
            "-e", f"PIP_CACHE_DIR={sandbox_home / '.cache' / 'pip'}",
            "-e", f"PATH=/usr/local/bin:/usr/bin:/bin:{sandbox_home / '.local' / 'bin'}",
            "-v", f"{mount_root}:{mount_root}",
            "-w", str(self.workspace),
            "--pids-limit", str(int(sandbox.pids_limit)),
        ]
        if sandbox.cpu_limit.strip():
            cmd.extend(["--cpus", sandbox.cpu_limit.strip()])
        if sandbox.memory_limit.strip():
            cmd.extend(["--memory", sandbox.memory_limit.strip()])
        if sandbox.network_policy == "none":
            cmd.extend(["--network", "none"])

        cmd.extend(
            [
                sandbox.image or "softnixclaw:latest",
                "task-run",
                "--config", str(self.config_path),
                "--workspace", str(self.workspace),
                "--messages-file", str(messages_file),
                "--output-file", str(output_file),
                "--session-key", session_key,
                "--channel", channel,
                "--chat-id", chat_id,
            ]
        )

        completed = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=int(sandbox.timeout_seconds) + 30,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "ephemeral sandbox failed").strip()
            raise RuntimeError(detail[:2000])
        if not output_file.exists():
            raise RuntimeError("Ephemeral sandbox finished without writing an output payload")
        payload = json.loads(output_file.read_text(encoding="utf-8"))
        content = str(payload.get("content") or "")
        if not content:
            raise RuntimeError("Ephemeral sandbox returned an empty response")
        return content
