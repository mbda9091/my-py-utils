from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from skill_sync.errors import SkillSyncError
from skill_sync.git_repo import ensure_git_repository
from skill_sync.validation import validate_skill_name

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

REGISTRY_SKILLS_DIR = "skills"
SYNC_METADATA_FILE = "skill-sync.json"


@dataclass(frozen=True, slots=True)
class RegistrySkill:
    name: str
    description: str
    compatibility: tuple[str, ...]
    updated_at: str
    path: Path


def list_skills(checkout: Path) -> list[RegistrySkill]:
    ensure_registry_checkout(checkout)
    skills_root = checkout / REGISTRY_SKILLS_DIR
    if not skills_root.exists():
        return []
    if not skills_root.is_dir():
        msg = f"Registry skills path is not a directory: {skills_root}"
        raise SkillSyncError(msg)

    skills = [
        read_registry_skill(path)
        for path in sorted(skills_root.iterdir())
        if path.is_dir() and (path / SYNC_METADATA_FILE).exists()
    ]
    return sorted(skills, key=lambda skill: skill.name)


def find_skill(checkout: Path, name: str) -> RegistrySkill:
    validate_skill_name(name)
    ensure_registry_checkout(checkout)
    skill_dir = checkout / REGISTRY_SKILLS_DIR / name
    if not skill_dir.exists():
        msg = f"Skill not found in registry cache: {name}"
        raise SkillSyncError(msg)
    if not skill_dir.is_dir():
        msg = f"Registry skill path is not a directory: {skill_dir}"
        raise SkillSyncError(msg)
    if not (skill_dir / "SKILL.md").is_file():
        msg = f"Registry skill is missing SKILL.md: {skill_dir}"
        raise SkillSyncError(msg)
    return read_registry_skill(skill_dir)


def read_registry_skill(skill_dir: Path) -> RegistrySkill:
    metadata_path = skill_dir / SYNC_METADATA_FILE
    payload = _load_metadata_payload(metadata_path)
    schema_version = payload.get("schemaVersion")
    if schema_version != 1:
        msg = f"Unsupported registry metadata schemaVersion: {metadata_path}: {schema_version}"
        raise SkillSyncError(msg)

    name = _metadata_string(payload, "name", metadata_path)
    if name != skill_dir.name:
        msg = f"Registry metadata name does not match directory: {metadata_path}"
        raise SkillSyncError(msg)

    return RegistrySkill(
        name=name,
        description=_metadata_string(payload, "description", metadata_path),
        compatibility=_metadata_compatibility(payload, metadata_path),
        updated_at=_metadata_string(payload, "updatedAt", metadata_path),
        path=skill_dir,
    )


def ensure_registry_checkout(checkout: Path) -> None:
    if not checkout.exists():
        msg = "Registry cache does not exist. Run 'skill-sync sync' first."
        raise SkillSyncError(msg)
    ensure_git_repository(checkout)


def _load_metadata_payload(metadata_path: Path) -> dict[str, object]:
    try:
        raw_payload: object = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"Invalid registry metadata JSON: {metadata_path}: {exc}"
        raise SkillSyncError(msg) from exc
    except OSError as exc:
        msg = f"Unable to read registry metadata: {metadata_path}: {exc}"
        raise SkillSyncError(msg) from exc

    if not isinstance(raw_payload, dict):
        msg = f"Registry metadata must contain a JSON object: {metadata_path}"
        raise SkillSyncError(msg)

    return cast("dict[str, object]", raw_payload)


def _metadata_string(payload: Mapping[str, object], field: str, metadata_path: Path) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        msg = f"Registry metadata is missing {field}: {metadata_path}"
        raise SkillSyncError(msg)
    return value


def _metadata_compatibility(payload: Mapping[str, object], metadata_path: Path) -> tuple[str, ...]:
    compatibility = payload.get("compatibility")
    if not isinstance(compatibility, list):
        msg = f"Registry metadata compatibility must be a string list: {metadata_path}"
        raise SkillSyncError(msg)
    compatibility_items: list[str] = []
    for item in cast("list[object]", compatibility):
        if not isinstance(item, str):
            msg = f"Registry metadata compatibility must be a string list: {metadata_path}"
            raise SkillSyncError(msg)
        compatibility_items.append(item)
    return tuple(compatibility_items)
