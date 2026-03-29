"""Curated skills bank catalog and import helpers."""

from __future__ import annotations

import base64
import codecs
import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_BANK_ROOT = PROJECT_ROOT / "docs" / "skillsbank" / "agency-agents"

CURATED_SKILL_PATHS: tuple[str, ...] = (
    "project-management/project-management-project-shepherd.md",
    "project-management/project-management-studio-producer.md",
    "project-management/project-manager-senior.md",
    "product/product-manager.md",
    "product/product-feedback-synthesizer.md",
    "product/product-trend-researcher.md",
    "product/product-sprint-prioritizer.md",
    "product/product-behavioral-nudge-engine.md",
    "sales/sales-outbound-strategist.md",
    "sales/sales-discovery-coach.md",
    "sales/sales-deal-strategist.md",
    "sales/sales-engineer.md",
    "sales/sales-proposal-strategist.md",
    "sales/sales-pipeline-analyst.md",
    "sales/sales-account-strategist.md",
    "sales/sales-coach.md",
    "support/support-support-responder.md",
    "support/support-analytics-reporter.md",
    "support/support-infrastructure-maintainer.md",
    "support/support-finance-tracker.md",
    "support/support-legal-compliance-checker.md",
    "engineering/engineering-backend-architect.md",
    "engineering/engineering-frontend-developer.md",
    "engineering/engineering-sre.md",
    "engineering/engineering-devops-automator.md",
    "engineering/engineering-security-engineer.md",
    "engineering/engineering-code-reviewer.md",
    "engineering/engineering-git-workflow-master.md",
    "engineering/engineering-rapid-prototyper.md",
    "engineering/engineering-ai-engineer.md",
    "engineering/engineering-data-engineer.md",
    "engineering/engineering-email-intelligence-engineer.md",
    "engineering/engineering-technical-writer.md",
    "engineering/engineering-incident-response-commander.md",
    "specialized/agents-orchestrator.md",
    "specialized/specialized-workflow-architect.md",
    "specialized/specialized-mcp-builder.md",
    "specialized/specialized-document-generator.md",
    "specialized/data-consolidation-agent.md",
    "specialized/compliance-auditor.md",
    "specialized/automation-governance-architect.md",
    "specialized/identity-graph-operator.md",
    "specialized/report-distribution-agent.md",
    "specialized/agentic-identity-trust.md",
    "specialized/lsp-index-engineer.md",
    "specialized/specialized-model-qa.md",
)

CATEGORY_LABELS: dict[str, str] = {
    "engineering": "Engineering",
    "design": "Design",
    "product": "Product",
    "support": "Support",
    "testing": "Testing",
    "specialized": "Specialized",
}

CATEGORY_ORDER: tuple[str, ...] = (
    "engineering",
    "design",
    "product",
    "support",
    "testing",
    "specialized",
)


@dataclass(frozen=True)
class SkillBankEntry:
    """One curated skill bank entry."""

    bank_id: str
    category: str
    category_label: str
    display_name: str
    description: str
    emoji: str
    vibe: str
    source_path: Path
    source_relative_path: str
    import_skill_name: str


def parse_skill_frontmatter(raw: str) -> dict[str, Any]:
    """Parse YAML-style frontmatter from SKILL.md (simple key/value lines only)."""
    result: dict[str, Any] = {}
    if not raw.startswith("---"):
        return result
    end = raw.find("---", 3)
    if end == -1:
        return result
    fm_block = raw[3:end].strip()
    for line in fm_block.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if val.startswith("{") and val.endswith("}"):
            try:
                result[key] = json.loads(val)
            except Exception:
                result[key] = val
        else:
            result[key] = val
    return result


_QUOTED_STRING_RE = re.compile(r'^(?P<quote>["\'])(?P<value>.*)(?P=quote)$')
_ESCAPED_UNICODE_RE = re.compile(r"\\(?:u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2}|[nrt\\\"'])")


def _decode_frontmatter_scalar(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = _QUOTED_STRING_RE.match(raw)
    if match:
        raw = match.group("value")
    if _ESCAPED_UNICODE_RE.search(raw):
        try:
            raw = codecs.decode(raw, "unicode_escape")
        except Exception:
            pass
    return raw.strip()


def _normalize_category(value: str) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _category_label(category: str) -> str:
    normalized = _normalize_category(category)
    return CATEGORY_LABELS.get(normalized, normalized.replace("-", " ").title() or "Other")


def _read_skill_entry(root: Path, relative_path: str) -> SkillBankEntry | None:
    source_path = (root / relative_path).resolve()
    if not source_path.is_file():
        return None
    raw = source_path.read_text(encoding="utf-8")
    frontmatter = parse_skill_frontmatter(raw)
    category = _normalize_category(Path(relative_path).parts[0] if Path(relative_path).parts else "")
    display_name = _decode_frontmatter_scalar(frontmatter.get("name") or source_path.stem) or source_path.stem
    description = _decode_frontmatter_scalar(frontmatter.get("description") or "")
    emoji = _decode_frontmatter_scalar(frontmatter.get("emoji") or "")
    vibe = _decode_frontmatter_scalar(frontmatter.get("vibe") or "")
    bank_id = source_path.stem
    return SkillBankEntry(
        bank_id=bank_id,
        category=category,
        category_label=_category_label(category),
        display_name=display_name,
        description=description,
        emoji=emoji,
        vibe=vibe,
        source_path=source_path,
        source_relative_path=str(source_path.relative_to(root).as_posix()),
        import_skill_name=bank_id,
    )


def iter_skill_bank_entries(
    *,
    root: Path = SKILLS_BANK_ROOT,
    curated_paths: Iterable[str] = CURATED_SKILL_PATHS,
) -> list[SkillBankEntry]:
    """Load the curated skills bank entries from disk."""
    entries: list[SkillBankEntry] = []
    for relative_path in curated_paths:
        entry = _read_skill_entry(root, relative_path)
        if entry is not None:
            entries.append(entry)
    return entries


def group_skill_bank_entries(
    entries: Iterable[SkillBankEntry],
    *,
    installed_skill_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Group skill bank entries by category for UI rendering."""
    installed = {str(item or "").strip() for item in (installed_skill_names or set()) if str(item or "").strip()}
    grouped: dict[str, list[dict[str, Any]]] = {category: [] for category in CATEGORY_ORDER}
    extras: dict[str, list[dict[str, Any]]] = {}

    for entry in entries:
        skill_payload = {
            "bank_id": entry.bank_id,
            "category": entry.category,
            "category_label": entry.category_label,
            "display_name": entry.display_name,
            "description": entry.description,
            "emoji": entry.emoji,
            "vibe": entry.vibe,
            "source_path": entry.source_relative_path,
            "import_skill_name": entry.import_skill_name,
            "installed": entry.import_skill_name in installed,
        }
        bucket = grouped.get(entry.category)
        if bucket is None:
            bucket = extras.setdefault(entry.category, [])
        bucket.append(skill_payload)

    categories: list[dict[str, Any]] = []
    for category in CATEGORY_ORDER:
        items = grouped.get(category) or []
        if items:
            categories.append(
                {
                    "category": category,
                    "category_label": _category_label(category),
                    "skills": sorted(items, key=lambda item: (item["display_name"], item["bank_id"])),
                }
            )
    for category in sorted(extras):
        items = extras[category]
        if items:
            categories.append(
                {
                    "category": category,
                    "category_label": _category_label(category),
                    "skills": sorted(items, key=lambda item: (item["display_name"], item["bank_id"])),
                }
            )
    return categories


def list_skill_bank_catalog(
    *,
    root: Path = SKILLS_BANK_ROOT,
    curated_paths: Iterable[str] = CURATED_SKILL_PATHS,
    installed_skill_names: set[str] | None = None,
) -> dict[str, Any]:
    """Return the curated catalog grouped by category."""
    entries = iter_skill_bank_entries(root=root, curated_paths=curated_paths)
    categories = group_skill_bank_entries(entries, installed_skill_names=installed_skill_names)
    total = sum(len(category["skills"]) for category in categories)
    return {
        "source_root": str(root),
        "total": total,
        "categories": categories,
    }


def resolve_skill_bank_entry(
    bank_id: str,
    *,
    root: Path = SKILLS_BANK_ROOT,
    curated_paths: Iterable[str] = CURATED_SKILL_PATHS,
) -> SkillBankEntry:
    """Resolve a curated entry by its bank id."""
    normalized = str(bank_id or "").strip()
    if not normalized:
        raise ValueError("bank_skill_id is required")
    for entry in iter_skill_bank_entries(root=root, curated_paths=curated_paths):
        if entry.bank_id == normalized:
            return entry
    raise ValueError(f"Unknown skill bank entry '{bank_id}'")


def build_skill_bank_archive(
    entry: SkillBankEntry,
) -> tuple[str, str]:
    """Build a zip archive payload suitable for the existing skill import flow."""
    if not entry.source_path.is_file():
        raise ValueError(f"Skill bank source file not found: {entry.source_relative_path}")
    raw = entry.source_path.read_text(encoding="utf-8")
    archive_name = f"{entry.import_skill_name}.zip"
    with io.BytesIO() as buffer:
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{entry.import_skill_name}/SKILL.md", raw)
        archive_base64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    return archive_name, archive_base64
