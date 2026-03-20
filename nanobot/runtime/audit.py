"""Runtime audit logging for agent tool execution."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def runtime_audit_path(workspace: Path) -> Path:
    """Return the JSONL audit path for a workspace."""
    return workspace / ".nanobot" / "runtime-audit.jsonl"


def _truncate(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _relative_path(workspace: Path, raw_path: Any) -> str:
    try:
        path = Path(str(raw_path)).expanduser()
        if not path.is_absolute():
            path = (workspace / path).resolve()
        else:
            path = path.resolve()
        try:
            return str(path.relative_to(workspace.resolve()))
        except ValueError:
            return str(path)
    except Exception:
        return str(raw_path or "")


def _detect_package_install(command: str) -> str | None:
    cmd = command.strip().lower()
    patterns = {
        "pip": r"(?:^|\s)(?:python(?:3)?\s+-m\s+pip|pip(?:3)?)\s+install(?:\s|$)",
        "uv": r"(?:^|\s)uv\s+(?:pip\s+install|add)(?:\s|$)",
        "npm": r"(?:^|\s)npm\s+install(?:\s|$)",
        "pnpm": r"(?:^|\s)pnpm\s+add(?:\s|$)",
        "yarn": r"(?:^|\s)yarn\s+add(?:\s|$)",
        "apt": r"(?:^|\s)apt(?:-get)?\s+install(?:\s|$)",
        "apk": r"(?:^|\s)apk\s+add(?:\s|$)",
        "brew": r"(?:^|\s)brew\s+install(?:\s|$)",
    }
    for name, pattern in patterns.items():
        if re.search(pattern, cmd):
            return name
    return None


def summarize_tool_call(workspace: Path, tool_name: str, params: dict[str, Any], result: str) -> dict[str, Any]:
    """Build a compact audit payload for a tool call."""
    status = "error" if isinstance(result, str) and result.startswith("Error") else "ok"
    summary: dict[str, Any] = {
        "tool_name": tool_name,
        "status": status,
        "result_preview": _truncate(result, 240),
    }

    if tool_name == "exec":
        command = str(params.get("command") or "")
        summary["command"] = _truncate(command, 300)
        if working_dir := params.get("working_dir"):
            summary["working_dir"] = _relative_path(workspace, working_dir)
        package_manager = _detect_package_install(command)
        if package_manager:
            summary["operation"] = "package_install"
            summary["package_manager"] = package_manager
        else:
            summary["operation"] = "command"
        exit_match = re.search(r"\nExit code:\s*(-?\d+)\s*$", result or "")
        if exit_match:
            summary["exit_code"] = int(exit_match.group(1))
    elif tool_name in {"read_file", "write_file", "edit_file", "list_dir"}:
        path = params.get("path")
        if path is not None:
            summary["path"] = _relative_path(workspace, path)
        summary["operation"] = {
            "read_file": "file_read",
            "write_file": "file_write",
            "edit_file": "file_edit",
            "list_dir": "file_list",
        }[tool_name]
    elif tool_name == "web_fetch":
        url = str(params.get("url") or "")
        if url:
            summary["path"] = _truncate(url, 300)
        try:
            result_json = json.loads(result) if result else {}
            http_status = result_json.get("status")
            text_len = result_json.get("length", 0)
            err = result_json.get("error")
            if err:
                summary["result_preview"] = _truncate(f"Error: {err}", 240)
                summary["status"] = "error"
            elif http_status:
                parts = [f"HTTP {http_status}"]
                if text_len:
                    parts.append(f"{text_len:,} chars")
                final_url = result_json.get("finalUrl") or url
                if final_url and final_url != url:
                    parts.append(f"→ {final_url[:80]}")
                summary["result_preview"] = " · ".join(parts)
        except Exception:
            pass
        summary["operation"] = "web_fetch"
    elif tool_name == "web_search":
        query = str(params.get("query") or "")
        if query:
            summary["path"] = _truncate(query, 300)
        summary["operation"] = "web_search"
    else:
        summary["operation"] = tool_name

    return summary


def summarize_policy_event(
    scope: str,
    decision: dict[str, Any],
    *,
    channel: str | None = None,
    session_key: str | None = None,
    tool_name: str | None = None,
    instance_id: str | None = None,
    instance_name: str | None = None,
) -> dict[str, Any]:
    action = str(decision.get("action") or "allow")
    blocked = bool(decision.get("blocked"))
    preview = str(decision.get("sanitized_text") or decision.get("text") or "")
    status = "error" if blocked else "ok"
    return {
        "operation": "policy_detection",
        "status": status,
        "scope": str(scope or ""),
        "action": action,
        "severity": str(decision.get("severity") or ""),
        "rule_ids": list(decision.get("matched_rules") or []),
        "policy_mode": str(decision.get("mode") or ""),
        "monitor_only": bool(decision.get("monitor_only")),
        "tool_name": str(tool_name or ""),
        "channel": str(channel or ""),
        "session_key": str(session_key or ""),
        "instance_id": str(instance_id or ""),
        "instance_name": str(instance_name or ""),
        "message_preview": _truncate(decision.get("text"), 240),
        "result_preview": _truncate(preview, 240),
        "policy_version": decision.get("policy_version"),
    }


class RuntimeAuditLogger:
    """Append JSONL audit records for runtime tool execution."""

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.path = runtime_audit_path(self.workspace)

    def _append_record(self, event_type: str, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now().astimezone().isoformat(),
            "event_type": event_type,
            "workspace": str(self.workspace),
            "payload": payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_message_event(
        self,
        phase: str,
        *,
        channel: str,
        session_key: str,
        content: str,
        status: str = "ok",
    ) -> None:
        operation = "message_received" if phase == "received" else "message_completed"
        self._append_record(
            f"message.{phase}",
            {
                "operation": operation,
                "status": status,
                "channel": str(channel or ""),
                "session_key": str(session_key or ""),
                "message_preview": _truncate(content, 240),
                "result_preview": _truncate(content, 240),
            },
        )

    def log_tool_start(self, tool_name: str, params: dict[str, Any]) -> None:
        payload = summarize_tool_call(self.workspace, tool_name, params, "")
        payload["status"] = "running"
        payload["result_preview"] = "Tool execution started."
        self._append_record("tool.start", payload)

    def log_tool_call(self, tool_name: str, params: dict[str, Any], result: str) -> None:
        self._append_record("tool.execute", summarize_tool_call(self.workspace, tool_name, params, result))

    def log_policy_event(
        self,
        *,
        scope: str,
        decision: dict[str, Any],
        channel: str | None = None,
        session_key: str | None = None,
        tool_name: str | None = None,
        instance_id: str | None = None,
        instance_name: str | None = None,
    ) -> None:
        payload = summarize_policy_event(
            scope,
            decision,
            channel=channel,
            session_key=session_key,
            tool_name=tool_name,
            instance_id=instance_id,
            instance_name=instance_name,
        )
        event_type = f"policy.{payload['action']}" if payload.get("action") else "policy.detected"
        self._append_record(event_type, payload)
