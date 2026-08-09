from __future__ import annotations

import filecmp
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, TypeAlias, cast

from skill_sync.catalog import REGISTRY_SKILLS_DIR, SYNC_METADATA_FILE
from skill_sync.errors import SkillSyncError
from skill_sync.git_repo import commit_path, push_with_retry, sync_repository
from skill_sync.security import SecurityFinding, assert_publish_safe, should_ignore_path
from skill_sync.validation import validate_local_skill

if TYPE_CHECKING:
    from skill_sync.config import Config
    from skill_sync.validation import SkillMetadata

Clock: TypeAlias = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class PublishResult:
    name: str
    changed: bool
    pushed: bool
    commit_created: bool
    warnings: tuple[SecurityFinding, ...]
    registry_path: Path


def publish_skill(
    local_skill_dir: Path,
    config: Config,
    *,
    update: bool = False,
    push: bool = True,
    clock: Clock | None = None,
) -> PublishResult:
    ensure_publish_identity(config)
    metadata = validate_local_skill(local_skill_dir)
    findings = assert_publish_safe(metadata.path)
    sync_repository(config)

    skills_root = config.checkout / REGISTRY_SKILLS_DIR
    destination = skills_root / metadata.name
    if destination.exists() and not update:
        msg = f"Skill already exists in registry: {metadata.name}. Pass --update to overwrite it."
        raise SkillSyncError(msg)

    skills_root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC) if clock is None else clock()

    with TemporaryDirectory(prefix="skill-sync-publish-") as temporary:
        staged_skill = Path(temporary) / metadata.name
        copy_publish_tree(metadata.path, staged_skill)
        if destination.exists() and is_registry_skill_unchanged(
            staged_skill,
            destination,
            metadata,
        ):
            return PublishResult(
                name=metadata.name,
                changed=False,
                pushed=False,
                commit_created=False,
                warnings=tuple(_low_risk_findings(findings)),
                registry_path=destination,
            )
        write_sync_metadata(staged_skill, metadata, now)
        replace_directory(staged_skill, destination)

    commit_message = (
        f"Update skill: {metadata.name}" if update else f"Publish skill: {metadata.name}"
    )
    commit_created = commit_path(
        config.checkout,
        Path(REGISTRY_SKILLS_DIR) / metadata.name,
        commit_message,
    )
    if not commit_created:
        return PublishResult(
            name=metadata.name,
            changed=False,
            pushed=False,
            commit_created=False,
            warnings=tuple(_low_risk_findings(findings)),
            registry_path=destination,
        )

    if push:
        push_with_retry(config.checkout, config.branch)

    return PublishResult(
        name=metadata.name,
        changed=True,
        pushed=push,
        commit_created=commit_created,
        warnings=tuple(_low_risk_findings(findings)),
        registry_path=destination,
    )


def ensure_publish_identity(config: Config) -> None:
    if config.git_identity is not None:
        return
    msg = (
        "Git identity is not configured for this registry. "
        "Run 'skill-sync init <git-url> --branch <branch> --git-name <name> "
        "--git-email <email>' before publishing."
    )
    raise SkillSyncError(msg)


def copy_publish_tree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=_copy_ignore,
        symlinks=True,
    )


def write_sync_metadata(skill_dir: Path, metadata: SkillMetadata, updated_at: datetime) -> None:
    payload = {
        "schemaVersion": 1,
        "name": metadata.name,
        "description": metadata.description,
        "compatibility": ["codex"],
        "updatedAt": _format_timestamp(updated_at),
    }
    (skill_dir / SYNC_METADATA_FILE).write_text(
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )


def is_registry_skill_unchanged(
    staged_skill: Path,
    existing_skill: Path,
    metadata: SkillMetadata,
) -> bool:
    metadata_path = existing_skill / SYNC_METADATA_FILE
    if not metadata_path.is_file():
        return False
    try:
        raw_payload: object = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    if not isinstance(raw_payload, dict):
        return False

    payload = cast("dict[str, object]", raw_payload)
    static_metadata_matches = (
        payload.get("schemaVersion") == 1
        and payload.get("name") == metadata.name
        and payload.get("description") == metadata.description
        and payload.get("compatibility") == ["codex"]
    )
    if not static_metadata_matches:
        return False
    return _directories_equal(staged_skill, existing_skill)


def replace_directory(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        source.replace(destination)
        return

    backup = destination.with_name(f".{destination.name}.skill-sync-backup")
    if backup.exists():
        shutil.rmtree(backup)
    destination.replace(backup)
    try:
        source.replace(destination)
    except OSError as exc:
        if destination.exists():
            shutil.rmtree(destination)
        backup.replace(destination)
        msg = f"Failed to replace directory {destination}: {exc}"
        raise SkillSyncError(msg) from exc
    else:
        shutil.rmtree(backup)


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    _ = directory
    ignored: set[str] = set()
    for name in names:
        if should_ignore_path(Path(name)):
            ignored.add(name)
    return ignored


def _directories_equal(left: Path, right: Path) -> bool:
    left_paths = _relative_file_paths(left)
    right_paths = _relative_file_paths(right)
    right_paths.discard(Path(SYNC_METADATA_FILE))
    if left_paths != right_paths:
        return False
    return all(filecmp.cmp(left / path, right / path, shallow=False) for path in left_paths)


def _relative_file_paths(root: Path) -> set[Path]:
    paths: set[Path] = set()
    for path in root.rglob("*"):
        relative_path = path.relative_to(root)
        if should_ignore_path(relative_path) or relative_path == Path(SYNC_METADATA_FILE):
            continue
        if path.is_file() or path.is_symlink():
            paths.add(relative_path)
    return paths


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _low_risk_findings(findings: list[SecurityFinding]) -> list[SecurityFinding]:
    return [finding for finding in findings if finding.severity == "low"]
