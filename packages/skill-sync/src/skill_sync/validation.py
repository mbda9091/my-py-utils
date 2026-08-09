from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from skill_sync.errors import SkillSyncError

if TYPE_CHECKING:
    from pathlib import Path

SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MIN_QUOTED_VALUE_LENGTH = 2


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    name: str
    description: str
    path: Path


def validate_skill_name(name: str) -> None:
    if not SKILL_NAME_PATTERN.fullmatch(name):
        msg = f"Invalid skill name: {name}. Use lowercase words separated by hyphens."
        raise SkillSyncError(msg)


def parse_frontmatter(text: str) -> dict[str, str]:
    normalized = text.removeprefix("\ufeff")
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "---":
        msg = "SKILL.md is missing frontmatter delimited by ---."
        raise SkillSyncError(msg)

    fields: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            if index == 1:
                msg = "SKILL.md frontmatter is empty."
                raise SkillSyncError(msg)
            return fields
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            msg = f"Invalid frontmatter line: {line}"
            raise SkillSyncError(msg)
        key, value = line.split(":", 1)
        key = key.strip()
        value = _strip_quotes(value.strip())
        if not key:
            msg = f"Invalid frontmatter line: {line}"
            raise SkillSyncError(msg)
        fields[key] = value

    msg = "SKILL.md frontmatter is missing a closing --- delimiter."
    raise SkillSyncError(msg)


def validate_local_skill(local_skill_dir: Path) -> SkillMetadata:
    skill_dir = local_skill_dir.expanduser()
    if not skill_dir.exists():
        msg = f"Skill directory does not exist: {skill_dir}"
        raise SkillSyncError(msg)
    if not skill_dir.is_dir():
        msg = f"Skill path is not a directory: {skill_dir}"
        raise SkillSyncError(msg)

    name = skill_dir.name
    validate_skill_name(name)

    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        msg = f"Missing SKILL.md: {skill_file}"
        raise SkillSyncError(msg)
    if not skill_file.is_file():
        msg = f"SKILL.md is not a file: {skill_file}"
        raise SkillSyncError(msg)

    try:
        fields = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
    except OSError as exc:
        msg = f"Unable to read SKILL.md: {skill_file}: {exc}"
        raise SkillSyncError(msg) from exc

    frontmatter_name = fields.get("name")
    if not frontmatter_name:
        msg = "SKILL.md frontmatter is missing required field: name"
        raise SkillSyncError(msg)
    if frontmatter_name != name:
        msg = f"SKILL.md frontmatter name '{frontmatter_name}' does not match directory '{name}'."
        raise SkillSyncError(msg)

    description = fields.get("description")
    if not description:
        msg = "SKILL.md frontmatter is missing required field: description"
        raise SkillSyncError(msg)

    return SkillMetadata(name=name, description=description, path=skill_dir.resolve())


def _strip_quotes(value: str) -> str:
    if len(value) < MIN_QUOTED_VALUE_LENGTH:
        return value
    if value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
