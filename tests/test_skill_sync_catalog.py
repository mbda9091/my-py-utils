from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from skill_sync.catalog import find_skill, list_local_skills, list_skills
from skill_sync.errors import SkillSyncError

from tests.skill_sync_helpers import make_local_repository, write_skill

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


def test_local_catalog_discovers_skill_files_without_publish_metadata(tmp_path: Path) -> None:
    repository = make_local_repository(tmp_path)
    zeta = write_skill(repository / "skills", "zeta-skill")
    alpha = write_skill(repository / "skills", "alpha-skill")
    (alpha / "skill-sync.json").write_text("invalid legacy metadata")
    (repository / "skills" / "README.md").write_text("Notes")
    (repository / "skills" / "draft").mkdir()

    skills = list_local_skills(repository)

    assert [skill.name for skill in skills] == ["alpha-skill", "zeta-skill"]
    assert [skill.path for skill in skills] == [alpha, zeta]
    assert skills[0].description == "Demo skill."
    assert not (zeta / "skill-sync.json").exists()
    assert (alpha / "skill-sync.json").read_text() == "invalid legacy metadata"


def test_local_catalog_accepts_missing_skills_directory(tmp_path: Path) -> None:
    repository = make_local_repository(tmp_path)
    assert list_local_skills(repository) == []
    assert not (repository / "skills").exists()


@pytest.mark.parametrize("kind", ["skill-link", "descriptor-link", "root-link"])
def test_local_catalog_rejects_linked_content(tmp_path: Path, kind: str) -> None:
    repository = make_local_repository(tmp_path)
    external = write_skill(tmp_path / "external")
    root = repository / "skills"
    root.mkdir()
    if kind == "root-link":
        root.rmdir()
        root.symlink_to(external.parent, target_is_directory=True)
    elif kind == "skill-link":
        (root / "demo-skill").symlink_to(external, target_is_directory=True)
    else:
        skill = root / "demo-skill"
        skill.mkdir()
        (skill / "SKILL.md").symlink_to(external / "SKILL.md")

    with pytest.raises(SkillSyncError, match="real"):
        list_local_skills(repository)


def test_local_catalog_reports_invalid_skill_frontmatter(tmp_path: Path) -> None:
    repository = make_local_repository(tmp_path)
    skill = write_skill(repository / "skills")
    (skill / "SKILL.md").write_text("---\nname: demo-skill\n---\n")

    with pytest.raises(SkillSyncError, match="description"):
        list_local_skills(repository)
