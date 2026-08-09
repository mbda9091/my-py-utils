from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from skill_sync.errors import SkillSyncError
from skill_sync.validation import parse_frontmatter, validate_local_skill, validate_skill_name

if TYPE_CHECKING:
    from pathlib import Path


def write_skill(root: Path, name: str = "demo-skill", description: str = "Demo skill.") -> Path:
    skill_dir = root / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nBody\n",
        encoding="utf-8",
    )
    return skill_dir


def test_validate_local_skill_returns_metadata(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path)

    metadata = validate_local_skill(skill_dir)

    assert metadata.name == "demo-skill"
    assert metadata.description == "Demo skill."
    assert metadata.path == skill_dir.resolve()


def test_validate_local_skill_rejects_missing_skill_file(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()

    with pytest.raises(SkillSyncError, match=r"Missing SKILL\.md"):
        validate_local_skill(skill_dir)


def test_validate_local_skill_rejects_invalid_directory_name(tmp_path: Path) -> None:
    skill_dir = tmp_path / "Demo_Skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Demo_Skill\ndescription: Demo\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillSyncError, match="Invalid skill name"):
        validate_local_skill(skill_dir)


def test_validate_local_skill_rejects_name_mismatch(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path, name="demo-skill")
    (skill_dir / "SKILL.md").write_text(
        "---\nname: other-skill\ndescription: Demo\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillSyncError, match="does not match directory"):
        validate_local_skill(skill_dir)


def test_validate_local_skill_rejects_missing_description(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: demo-skill\n---\n", encoding="utf-8")

    with pytest.raises(SkillSyncError, match="description"):
        validate_local_skill(skill_dir)


def test_parse_frontmatter_rejects_missing_delimiters() -> None:
    with pytest.raises(SkillSyncError, match="missing frontmatter"):
        parse_frontmatter("name: demo-skill\n")


def test_validate_skill_name_accepts_lowercase_hyphenated_names() -> None:
    validate_skill_name("abc-123-skill")
