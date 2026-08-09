from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from skill_sync.catalog import find_skill, list_skills
from skill_sync.errors import SkillSyncError

if TYPE_CHECKING:
    from pathlib import Path


def make_checkout(root: Path) -> Path:
    checkout = root / "repository"
    (checkout / ".git").mkdir(parents=True)
    return checkout


def write_registry_skill(
    checkout: Path,
    name: str,
    description: str = "Demo skill.",
    updated_at: str = "2026-08-09T00:00:00Z",
) -> Path:
    skill_dir = checkout / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
    (skill_dir / "skill-sync.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "name": name,
                "description": description,
                "compatibility": ["codex"],
                "updatedAt": updated_at,
            },
        ),
        encoding="utf-8",
    )
    return skill_dir


def test_list_skills_returns_empty_for_empty_registry(tmp_path: Path) -> None:
    checkout = make_checkout(tmp_path)

    assert list_skills(checkout) == []


def test_list_skills_scans_metadata_sorted_by_name(tmp_path: Path) -> None:
    checkout = make_checkout(tmp_path)
    write_registry_skill(checkout, "zeta-skill", "Zeta")
    write_registry_skill(checkout, "alpha-skill", "Alpha")

    skills = list_skills(checkout)

    assert [skill.name for skill in skills] == ["alpha-skill", "zeta-skill"]
    assert skills[0].description == "Alpha"
    assert skills[0].compatibility == ("codex",)


def test_list_skills_rejects_broken_metadata(tmp_path: Path) -> None:
    checkout = make_checkout(tmp_path)
    skill_dir = checkout / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill-sync.json").write_text("{", encoding="utf-8")

    with pytest.raises(SkillSyncError, match="Invalid registry metadata JSON"):
        list_skills(checkout)


def test_list_skills_rejects_metadata_name_mismatch(tmp_path: Path) -> None:
    checkout = make_checkout(tmp_path)
    write_registry_skill(checkout, "demo-skill")
    metadata = checkout / "skills" / "demo-skill" / "skill-sync.json"
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["name"] = "other-skill"
    metadata.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SkillSyncError, match="does not match directory"):
        list_skills(checkout)


def test_find_skill_rejects_missing_skill(tmp_path: Path) -> None:
    checkout = make_checkout(tmp_path)

    with pytest.raises(SkillSyncError, match="Skill not found"):
        find_skill(checkout, "missing-skill")
