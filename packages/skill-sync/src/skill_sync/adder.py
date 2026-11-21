from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from stat import S_IFMT, S_IMODE, S_ISDIR, S_ISLNK, S_ISREG
from tempfile import TemporaryDirectory, mkdtemp
from typing import TYPE_CHECKING

from skill_sync.catalog import find_local_skill, personal_skill_path
from skill_sync.config import atomic_write_bytes, installations_path
from skill_sync.errors import SkillSyncError
from skill_sync.installations import (
    ManagedInstallation,
    read_managed_installations,
    symlink_target,
    write_managed_installations,
)
from skill_sync.links import (
    entry_state,
    link_directory,
    link_path,
    require_entry,
    require_external_path,
)
from skill_sync.locking import mutation_lock
from skill_sync.repository import RepositoryConfig, require_current_binding
from skill_sync.validation import validate_local_skill

if TYPE_CHECKING:
    from skill_sync.links import EntryState


@dataclass(frozen=True, slots=True)
class AddResult:
    name: str
    path: Path
    repository_path: Path
    changed: bool
    cleanup_path: Path | None = None


def add_skill(local_skill_dir: Path, config: RepositoryConfig, *, force: bool = False) -> AddResult:
    with mutation_lock(config.state_home):
        require_current_binding(config)
        return _add_skill(local_skill_dir, config, force=force)


def _add_skill(local_skill_dir: Path, config: RepositoryConfig, *, force: bool) -> AddResult:
    source = link_path(local_skill_dir)
    destination = personal_skill_path(config.repository_path, source.name).absolute()
    if source == destination:
        find_local_skill(config.repository_path, source.name)
        return AddResult(source.name, source, destination, changed=False)
    if source.is_symlink():
        if source.resolve() == destination and destination.is_dir():
            find_local_skill(config.repository_path, source.name)
            _record_link(source, destination, config)
            return AddResult(source.name, source, destination, changed=False)
        msg = f"Cannot add a symlink to another location: {source}"
        raise SkillSyncError(msg)
    require_external_path(source, config.repository_path)
    metadata = validate_local_skill(source)
    if (source / "SKILL.md").is_symlink():
        msg = "Skill descriptor must be a real file."
        raise SkillSyncError(msg)
    previous = entry_state(destination)
    if previous is not None and not force:
        msg = (
            f"Skill already exists in personal repository: {metadata.name}. "
            "Pass --force to replace it. No files were moved."
        )
        raise SkillSyncError(msg)
    cleanup_path = _move_and_link(source, destination, config, previous=previous)
    return AddResult(metadata.name, source, destination, changed=True, cleanup_path=cleanup_path)


def _move_and_link(
    source: Path, destination: Path, config: RepositoryConfig, *, previous: EntryState | None
) -> Path | None:
    records = read_managed_installations(config.state_home)
    target = (
        "codex"
        if source == link_path(Path.home() / ".agents" / "skills" / source.name)
        else "local-path"
    )
    records[str(source)] = ManagedInstallation(source.name, target, source, destination)
    records_path = installations_path(config.state_home)
    old_records = records_path.read_bytes() if records_path.exists() else None
    installed = False
    recorded = False
    repository_backup: Path | None = None
    try:
        before = _tree_snapshot(source)
        _validate_relative_links(source)
        _assert_unchanged(source, before)
        expected = entry_state(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".skill-sync-add-", dir=destination.parent) as temporary:
            staged = Path(temporary) / source.name
            shutil.copytree(source, staged, symlinks=True)
            _assert_unchanged(source, before)
            _assert_unchanged(staged, before)
            repository_backup = _place_skill(staged, destination, previous)
            installed = True
            with link_directory(source, destination, expected=expected) as backup:
                _assert_unchanged(backup, before)
                write_managed_installations(records, config.state_home)
                recorded = True
                _assert_unchanged(backup, before)
    except (OSError, SkillSyncError) as exc:
        try:
            if symlink_target(source) != destination:
                if recorded:
                    if old_records is None:
                        records_path.unlink(missing_ok=True)
                    else:
                        atomic_write_bytes(records_path, old_records)
                _restore_repository_skill(destination, repository_backup, installed=installed)
        except (OSError, SkillSyncError) as restore_error:
            msg = f"Failed to restore add state: {restore_error}"
            if repository_backup is not None:
                msg += f". Previous repository content kept at {repository_backup}"
            raise SkillSyncError(msg) from exc
        msg = f"Failed to add skill {source}: {exc}"
        if repository_backup is not None and os.path.lexists(repository_backup):
            msg += f". Previous repository content kept at {repository_backup}"
        raise SkillSyncError(msg) from exc
    finally:
        if repository_backup is not None and not os.path.lexists(repository_backup):
            shutil.rmtree(repository_backup.parent)
    return _cleanup_repository_backup(repository_backup)


def _cleanup_repository_backup(backup: Path | None) -> Path | None:
    if backup is None:
        return None
    # The replacement, source link and records have committed. Cleanup failure
    # must not turn that successful operation into a failed add or trigger rollback.
    try:
        shutil.rmtree(backup.parent)
    except OSError:
        return backup.parent
    return None


def _restore_repository_skill(destination: Path, backup: Path | None, *, installed: bool) -> None:
    if installed:
        shutil.rmtree(destination)
    if backup is not None:
        require_entry(destination, None)
        backup.rename(destination)


def _place_skill(staged: Path, destination: Path, previous: EntryState | None) -> Path | None:
    if previous is not None and previous.link is not None:
        msg = f"Personal skill must be stored in a real directory: {destination}"
        raise SkillSyncError(msg)
    require_entry(destination, previous)
    backup = None
    try:
        if previous is not None:
            backup_root = Path(mkdtemp(prefix=".skill-sync-replaced-", dir=destination.parent))
            backup = backup_root / "original"
            destination.rename(backup)
            require_entry(backup, previous)
        require_entry(destination, None)
        staged.rename(destination)
    except (OSError, SkillSyncError) as exc:
        if backup is not None:
            try:
                if os.path.lexists(backup):
                    require_entry(destination, None)
                    backup.rename(destination)
            except (OSError, SkillSyncError) as restore_error:
                msg = f"Cannot restore repository skill; original kept at {backup}: {restore_error}"
                raise SkillSyncError(msg) from exc
            shutil.rmtree(backup.parent)
        raise
    return backup


def _record_link(source: Path, destination: Path, config: RepositoryConfig) -> None:
    expected = entry_state(source)
    records = read_managed_installations(config.state_home)
    target = (
        "codex"
        if source == link_path(Path.home() / ".agents" / "skills" / source.name)
        else "local-path"
    )
    records[str(source)] = ManagedInstallation(source.name, target, source, destination)
    # destination has already been validated as the current personal skill.
    if source.resolve() != destination:
        msg = "Skill link changed during registration."
        raise SkillSyncError(msg)
    if symlink_target(source) == destination:
        write_managed_installations(records, config.state_home)
    else:
        with link_directory(source, destination, expected=expected):
            write_managed_installations(records, config.state_home)


def _validate_relative_links(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_symlink() or path.readlink().is_absolute():
            continue
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError) as exc:
            msg = f"Cannot resolve relative symlink before moving skill: {path}"
            raise SkillSyncError(msg) from exc
        lexical_target = Path(os.path.normpath(path.parent / path.readlink()))
        if not lexical_target.is_relative_to(root) or not resolved.is_relative_to(root):
            msg = (
                f"Relative symlink points outside the skill and would change after moving: {path}. "
                "Use an absolute link or move the referenced content into the skill first."
            )
            raise SkillSyncError(msg)


def _assert_unchanged(path: Path | None, expected: dict[str, tuple[object, ...]]) -> None:
    if path is None or _tree_snapshot(path) != expected:
        msg = (
            "Skill changed during migration. Stop editing it and retry; original data is preserved."
        )
        raise SkillSyncError(msg)


def _tree_snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    entries: dict[str, tuple[object, ...]] = {}
    for path in [root, *sorted(root.rglob("*"))]:
        if path.name == ".git":
            msg = f"Nested Git metadata must be handled before adding this skill: {path}"
            raise SkillSyncError(msg)
        info = path.lstat()
        if S_ISLNK(info.st_mode):
            content = str(path.readlink())
        elif S_ISREG(info.st_mode):
            with path.open("rb") as stream:
                content = hashlib.file_digest(stream, "sha256").hexdigest()
        elif S_ISDIR(info.st_mode):
            content = "directory"
        else:
            msg = f"Unsupported special file in skill: {path}"
            raise SkillSyncError(msg)
        entries[str(path.relative_to(root))] = (
            S_IFMT(info.st_mode),
            S_IMODE(info.st_mode),
            info.st_mtime_ns,
            content,
        )
    return entries
