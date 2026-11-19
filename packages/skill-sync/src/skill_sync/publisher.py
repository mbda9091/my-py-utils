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

from skill_sync.catalog import REGISTRY_SKILLS_DIR, SYNC_METADATA_FILE, registry_skill_path
from skill_sync.config import atomic_write_json
from skill_sync.errors import SkillSyncError
from skill_sync.git_repo import (
    apply_git_identity,
    commit_path,
    push_with_retry,
    run_git,
    run_git_result,
    sync_repository,
    validate_registry_repository,
)
from skill_sync.security import (
    SecurityFinding,
    assert_publish_safe,
    iter_publish_files,
    should_ignore_path,
)
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
    destination = registry_skill_path(config.checkout, metadata.name)
    if metadata.path == destination.resolve():
        return _publish_managed_skill(metadata, config, findings, push=push, clock=clock)
    sync_repository(config)

    skills_root = config.checkout / REGISTRY_SKILLS_DIR
    destination = skills_root / metadata.name
    if destination.exists() and not update:
        msg = f"Skill already exists in registry: {metadata.name}. Pass --update to overwrite it."
        raise SkillSyncError(msg)

    skills_root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC) if clock is None else clock()

    with TemporaryDirectory(prefix=".skill-sync-publish-", dir=skills_root) as temporary:
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


def _publish_managed_skill(
    metadata: SkillMetadata,
    config: Config,
    findings: list[SecurityFinding],
    *,
    push: bool,
    clock: Clock | None,
) -> PublishResult:
    validate_registry_repository(config)
    apply_git_identity(config)
    relative_path = Path(REGISTRY_SKILLS_DIR) / metadata.name
    files, changed = _managed_publish_files(metadata.path, config.checkout, relative_path)
    commit_created = False
    if changed or not _metadata_matches(metadata.path, metadata):
        now = datetime.now(UTC) if clock is None else clock()
        write_sync_metadata(metadata.path, metadata, now)
        commit_created = commit_path(
            config.checkout,
            relative_path,
            f"Update skill: {metadata.name}",
            files=files,
        )
    if push:
        # Also retry a previous --no-push publication or a failed push.
        push_with_retry(config.checkout, config.branch)
    return PublishResult(
        name=metadata.name,
        changed=commit_created,
        pushed=push,
        commit_created=commit_created,
        warnings=tuple(_low_risk_findings(findings)),
        registry_path=metadata.path,
    )


def _managed_publish_files(
    skill_dir: Path,
    checkout: Path,
    relative_path: Path,
) -> tuple[list[Path], bool]:
    current = {
        relative_path / path.relative_to(skill_dir) for path in iter_publish_files(skill_dir)
    }
    ignored = run_git_result(
        ["check-ignore", "--stdin", "-z"],
        cwd=checkout,
        input_text="".join(f"{path}\0" for path in sorted(current)),
    )
    if ignored.returncode not in {0, 1}:
        msg = f"Cannot inspect Git ignore rules: {ignored.stderr.strip()}"
        raise SkillSyncError(msg)
    current.difference_update(Path(path) for path in ignored.stdout.split("\0") if path)
    head = run_git(
        ["ls-tree", "-r", "--name-only", "-z", "HEAD", "--", str(relative_path)],
        cwd=checkout,
    )
    index = run_git(["ls-files", "-z", "--", str(relative_path)], cwd=checkout)
    tracked = {
        Path(path)
        for path in (head + index).split("\0")
        if path and not should_ignore_path(Path(path).relative_to(relative_path))
    }
    files = sorted(current | tracked | {relative_path / SYNC_METADATA_FILE})
    diff = run_git_result(
        ["diff", "HEAD", "--quiet", "--", *[str(path) for path in files]],
        cwd=checkout,
    )
    if diff.returncode not in {0, 1}:
        msg = f"Cannot inspect managed skill changes: {diff.stderr.strip()}"
        raise SkillSyncError(msg)
    return files, bool(current - tracked) or diff.returncode == 1


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
    atomic_write_json(skill_dir / SYNC_METADATA_FILE, payload)


def is_registry_skill_unchanged(
    staged_skill: Path,
    existing_skill: Path,
    metadata: SkillMetadata,
) -> bool:
    return _metadata_matches(existing_skill, metadata) and _directories_equal(
        staged_skill,
        existing_skill,
    )


def _metadata_matches(existing_skill: Path, metadata: SkillMetadata) -> bool:
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
    return (
        payload.get("schemaVersion") == 1
        and payload.get("name") == metadata.name
        and payload.get("description") == metadata.description
        and payload.get("compatibility") == ["codex"]
    )


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
