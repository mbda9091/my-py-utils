from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from skill_sync.catalog import list_local_skill_names, list_local_skills
from skill_sync.errors import SkillSyncError

from tests.skill_sync_helpers import make_local_repository, write_skill

if TYPE_CHECKING:
    from pathlib import Path


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


def test_candidate_names_include_invalid_descriptors_without_blocking_other_skills(
    tmp_path: Path,
) -> None:
    repository = make_local_repository(tmp_path)
    invalid = write_skill(repository / "skills", "alpha-skill")
    write_skill(repository / "skills", "beta-skill")
    (invalid / "SKILL.md").write_text("Invalid descriptor")
    (repository / "skills/draft").mkdir()
    (repository / "skills/README.md").write_text("Notes")

    assert list_local_skill_names(repository) == ["alpha-skill", "beta-skill"]
