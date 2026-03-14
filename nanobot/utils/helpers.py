"""Utility functions for nanobot."""

import re
import shutil
from datetime import datetime
from pathlib import Path


def detect_image_mime(data: bytes) -> str | None:
    """Detect image MIME type from magic bytes, ignoring file extension."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists, return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_path() -> Path:
    """~/.nanobot data directory."""
    return ensure_dir(Path.home() / ".nanobot")


def get_workspace_path(workspace: str | None = None) -> Path:
    """Resolve and ensure workspace path. Defaults to ~/.nanobot/workspace."""
    path = Path(workspace).expanduser() if workspace else Path.home() / ".nanobot" / "workspace"
    return ensure_dir(path)


def timestamp() -> str:
    """Current ISO timestamp."""
    return datetime.now().isoformat()


_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')

def safe_filename(name: str) -> str:
    """Replace unsafe path characters with underscores."""
    return _UNSAFE_CHARS.sub("_", name).strip()


def split_message(content: str, max_len: int = 2000) -> list[str]:
    """
    Split content into chunks within max_len, preferring line breaks.

    Args:
        content: The text content to split.
        max_len: Maximum length per chunk (default 2000 for Discord compatibility).

    Returns:
        List of message chunks, each within max_len.
    """
    if not content:
        return []
    if len(content) <= max_len:
        return [content]
    chunks: list[str] = []
    while content:
        if len(content) <= max_len:
            chunks.append(content)
            break
        cut = content[:max_len]
        # Try to break at newline first, then space, then hard break
        pos = cut.rfind('\n')
        if pos <= 0:
            pos = cut.rfind(' ')
        if pos <= 0:
            pos = max_len
        chunks.append(content[:pos])
        content = content[pos:].lstrip()
    return chunks


def _render_workspace_template(content: str, *, agent_name: str | None = None) -> str:
    """Render one workspace template with optional identity placeholders."""
    name = (agent_name or "nanobot").strip() or "nanobot"
    return content.replace("{{agent_name}}", name)


def _sync_workspace_identity(workspace: Path, *, agent_name: str) -> list[str]:
    """Apply agent identity replacements in existing workspace prompt files."""
    identity_files = [
        workspace / "SOUL.md",
        workspace / "HEARTBEAT.md",
        workspace / "USER.md",
        workspace / "memory" / "MEMORY.md",
    ]
    line_rules: dict[Path, list[tuple[re.Pattern[str], str]]] = {
        workspace / "SOUL.md": [
            (
                re.compile(r"^I am .+? 🐈, a personal AI assistant\.$", flags=re.MULTILINE),
                f"I am {agent_name} 🐈, a personal AI assistant.",
            )
        ],
        workspace / "HEARTBEAT.md": [
            (
                re.compile(r"^This file is checked every 30 minutes by your .+? agent\.$", flags=re.MULTILINE),
                f"This file is checked every 30 minutes by your {agent_name} agent.",
            )
        ],
        workspace / "USER.md": [
            (
                re.compile(
                    r"^\*Edit this file to customize .+?'s behavior for your needs\.\*$",
                    flags=re.MULTILINE,
                ),
                f"*Edit this file to customize {agent_name}'s behavior for your needs.*",
            )
        ],
        workspace / "memory" / "MEMORY.md": [
            (
                re.compile(
                    r"^\*This file is automatically updated by .+? when important information should be remembered\.\*$",
                    flags=re.MULTILINE,
                ),
                f"*This file is automatically updated by {agent_name} when important information should be remembered.*",
            )
        ],
    }
    updated: list[str] = []
    for path in identity_files:
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            continue
        rendered = _render_workspace_template(content, agent_name=agent_name)
        for pattern, replacement in line_rules.get(path, []):
            rendered = pattern.sub(replacement, rendered)
        if rendered == content:
            rendered = content.replace("nanobot", agent_name)
        if rendered == content:
            continue
        path.write_text(rendered, encoding="utf-8")
        try:
            updated.append(str(path.relative_to(workspace)))
        except ValueError:
            updated.append(str(path))
    return updated


def sync_workspace_templates(
    workspace: Path,
    silent: bool = False,
    *,
    agent_name: str | None = None,
    apply_identity: bool = False,
) -> list[str]:
    """Sync bundled templates and built-in skills to workspace. Only creates missing files."""
    from importlib.resources import files as pkg_files
    try:
        tpl = pkg_files("nanobot") / "templates"
    except Exception:
        return []
    if not tpl.is_dir():
        return []

    added: list[str] = []

    def _write(src, dest: Path):
        if dest.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = src.read_text(encoding="utf-8") if src else ""
        content = _render_workspace_template(content, agent_name=agent_name)
        dest.write_text(content, encoding="utf-8")
        added.append(str(dest.relative_to(workspace)))

    for item in tpl.iterdir():
        if item.name.endswith(".md"):
            _write(item, workspace / item.name)
    _write(tpl / "memory" / "MEMORY.md", workspace / "memory" / "MEMORY.md")
    _write(None, workspace / "memory" / "HISTORY.md")
    skills_root = workspace / "skills"
    skills_root.mkdir(exist_ok=True)

    # Mirror built-in skills into each workspace so restrictToWorkspace=true
    # can still read skill files without crossing directory boundaries.
    try:
        builtin_skills = pkg_files("nanobot") / "skills"
    except Exception:
        builtin_skills = None
    if builtin_skills and builtin_skills.is_dir():
        for skill_dir in builtin_skills.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_manifest = skill_dir / "SKILL.md"
            if not skill_manifest.exists():
                continue

            for source in skill_dir.rglob("*"):
                if source.is_dir():
                    continue
                relative = source.relative_to(skill_dir)
                target = skills_root / skill_dir.name / relative
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                added.append(str(target.relative_to(workspace)))

    if apply_identity and agent_name:
        added.extend(_sync_workspace_identity(workspace, agent_name=agent_name))

    if added and not silent:
        from rich.console import Console
        for name in added:
            Console().print(f"  [dim]Created {name}[/dim]")
    return added
