from __future__ import annotations

from typing import TYPE_CHECKING

from skill_sync.errors import SkillSyncError
from skill_sync.validation import SkillMetadata, validate_local_skill, validate_skill_name

if TYPE_CHECKING:
    from pathlib import Path

SKILLS_DIR = "skills"


def list_local_skill_names(repository: Path) -> list[str]:
    skills_root = repository / SKILLS_DIR
    if skills_root.is_symlink() or (skills_root.exists() and not skills_root.is_dir()):
        msg = f"Personal skills path must be a real directory: {skills_root}"
        raise SkillSyncError(msg)
    if not skills_root.exists():
        return []
    return [
        path.name
        for path in sorted(skills_root.iterdir())
        if path.is_symlink()
        or (path.is_dir() and ((path / "SKILL.md").exists() or (path / "SKILL.md").is_symlink()))
    ]


def list_local_skills(repository: Path) -> list[SkillMetadata]:
    return [find_local_skill(repository, name) for name in list_local_skill_names(repository)]


def find_local_skill(repository: Path, name: str) -> SkillMetadata:
    skill = personal_skill_path(repository, name)
    if not skill.exists():
        msg = f"Skill not found in personal repository: {name}"
        raise SkillSyncError(msg)
    if (skill / "SKILL.md").is_symlink():
        msg = f"Skill descriptor must be a real file: {skill / 'SKILL.md'}"
        raise SkillSyncError(msg)
    return validate_local_skill(skill)


def personal_skill_path(repository: Path, name: str) -> Path:
    validate_skill_name(name)
    skills_root = repository / SKILLS_DIR
    skill_dir = skills_root / name
    if skills_root.is_symlink() or skill_dir.is_symlink():
        msg = f"Personal skill must be stored in a real directory: {skill_dir}"
        raise SkillSyncError(msg)
    return skill_dir
