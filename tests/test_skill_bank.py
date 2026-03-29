import base64
import io
import zipfile

from nanobot.admin.service import AdminService
from nanobot.admin.skills_bank import (
    build_skill_bank_archive,
    list_skill_bank_catalog,
    resolve_skill_bank_entry,
)
from nanobot.config.loader import save_config
from nanobot.config.schema import Config


def test_skill_bank_catalog_groups_curated_entries() -> None:
    catalog = list_skill_bank_catalog()

    categories = {category["category"] for category in catalog["categories"]}
    assert {"project-management", "product", "sales", "support", "engineering", "specialized"}.issubset(categories)
    assert catalog["total"] >= 20

    pm = next(category for category in catalog["categories"] if category["category"] == "project-management")
    assert any(item["bank_id"] == "project-management-project-shepherd" for item in pm["skills"])
    assert any(item["bank_id"] == "project-manager-senior" for item in pm["skills"])

    product = next(category for category in catalog["categories"] if category["category"] == "product")
    assert any(item["bank_id"] == "product-manager" for item in product["skills"])
    assert any(item["bank_id"] == "product-feedback-synthesizer" for item in product["skills"])

    sales = next(category for category in catalog["categories"] if category["category"] == "sales")
    assert any(item["bank_id"] == "sales-outbound-strategist" for item in sales["skills"])
    assert any(item["bank_id"] == "sales-engineer" for item in sales["skills"])

    support = next(category for category in catalog["categories"] if category["category"] == "support")
    assert any(item["bank_id"] == "support-support-responder" for item in support["skills"])

    engineering = next(category for category in catalog["categories"] if category["category"] == "engineering")
    assert any(item["bank_id"] == "engineering-backend-architect" for item in engineering["skills"])
    assert any(item["bank_id"] == "engineering-sre" for item in engineering["skills"])


def test_skill_bank_archive_wraps_source_markdown_as_skill_md() -> None:
    entry = resolve_skill_bank_entry("engineering-frontend-developer")
    archive_name, archive_base64 = build_skill_bank_archive(entry)

    assert archive_name == "engineering-frontend-developer.zip"

    archive_bytes = base64.b64decode(archive_base64)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        names = archive.namelist()
        assert names == ["engineering-frontend-developer/SKILL.md"]
        content = archive.read("engineering-frontend-developer/SKILL.md").decode("utf-8")
        assert "Frontend Developer Agent Personality" in content


def test_skill_bank_unicode_emoji_frontmatter_is_decoded() -> None:
    entry = resolve_skill_bank_entry("specialized-workflow-architect")
    assert entry.emoji == "\U0001F5FA\uFE0F"


def test_skill_bank_imports_into_instance_workspace(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.json"

    config = Config()
    config.agents.defaults.workspace = str(workspace)
    save_config(config, config_path)

    service = AdminService(config_path=config_path)
    instance_id = service.list_instances()[0]["id"]

    result = service.import_skill_bank_entry(
        instance_id=instance_id,
        bank_skill_id="engineering-frontend-developer",
    )

    skill_dir = workspace / "skills" / "engineering-frontend-developer"
    assert result["skill_name"] == "engineering-frontend-developer"
    assert result["display_name"] == "Frontend Developer"
    assert result["category"] == "engineering"
    assert skill_dir.is_dir()
    assert (skill_dir / "SKILL.md").exists()

    skills = service.list_instance_skills(instance_id=instance_id)["skills"]
    assert any(item["skill_name"] == "engineering-frontend-developer" for item in skills)
