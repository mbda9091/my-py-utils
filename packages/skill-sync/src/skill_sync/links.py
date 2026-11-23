from __future__ import annotations

import errno
import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISLNK
from tempfile import mkdtemp
from typing import TYPE_CHECKING

from skill_sync.config import atomic_write_json
from skill_sync.errors import SkillSyncError

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping


@dataclass(frozen=True, slots=True)
class EntryState:
    device: int
    inode: int
    mode: int
    modified_ns: int
    link: Path | None


def entry_state(path: Path) -> EntryState | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    link = path.readlink() if S_ISLNK(info.st_mode) else None
    return EntryState(info.st_dev, info.st_ino, info.st_mode, info.st_mtime_ns, link)


def require_entry(path: Path, expected: EntryState | None) -> None:
    if entry_state(path) != expected:
        msg = f"Skill path changed during operation; existing content was preserved: {path}"
        raise SkillSyncError(msg)


@contextmanager
def link_directory(
    destination: Path,
    source: Path,
    *,
    expected: EntryState | None,
    persistent_backup: Path | None = None,
) -> Generator[Path | None]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup_root = persistent_backup or Path(
        mkdtemp(prefix=".skill-sync-backup-", dir=destination.parent)
    )
    backup = backup_root / "original"
    new_link = backup_root / "new-link"
    preserve = False
    moved = False
    published = False
    created = None
    committed = False
    try:
        try:
            if expected is not None:
                require_entry(destination, expected)
                destination.rename(backup)
                moved = True
                require_entry(backup, expected)
            # Establish identity privately before exposing the link. link(2) with
            # no following publishes that same symlink inode without overwriting.
            created = _prepare_link(new_link, source)
            os.link(new_link, destination, follow_symlinks=False)
            published = True
            yield backup if moved else None
            committed = True
        except BaseException:
            try:
                if published and created is not None:
                    _withdraw_link(destination, created, backup_root)
                if moved:
                    _restore_entry(backup, destination)
            except (OSError, SkillSyncError) as exc:
                preserve = True
                msg = (
                    f"Failed to restore {destination}. Original data kept at {backup}; "
                    f"recovery files kept at {backup_root}: {exc}"
                )
                raise SkillSyncError(msg) from exc
            raise
    finally:
        if not preserve:
            try:
                if committed and persistent_backup is not None:
                    new_link.unlink(missing_ok=True)
                else:
                    shutil.rmtree(backup_root)
            except OSError as exc:
                if committed:
                    raise OSError(exc.errno or errno.EIO, str(exc), str(backup_root)) from exc
                raise


def _prepare_link(path: Path, source: Path) -> EntryState:
    path.symlink_to(source.resolve(), target_is_directory=True)
    created = entry_state(path)
    if created is None:
        msg = f"Prepared skill link disappeared: {path}"
        raise SkillSyncError(msg)
    return created


def _withdraw_link(destination: Path, expected: EntryState, backup_root: Path) -> None:
    captured = backup_root / "withdrawn"
    try:
        destination.rename(captured)
    except FileNotFoundError:
        return
    # Only unlink the captured object in our private directory. An external
    # replacement at the shared destination must never be deleted by rollback.
    if entry_state(captured) != expected:
        location = captured
        if captured.is_symlink() or captured.is_file():
            try:
                _restore_entry(captured, destination)
                location = destination
            except FileExistsError:
                pass
        msg = f"Skill path changed during rollback; unexpected content preserved at {location}"
        raise SkillSyncError(msg)
    captured.unlink()


def _restore_entry(backup: Path, destination: Path) -> None:
    if backup.is_symlink() or backup.is_file():
        os.link(backup, destination, follow_symlinks=False)
        backup.unlink()
    elif not (destination.exists() or destination.is_symlink()):
        backup.rename(destination)
    else:
        msg = f"Restore destination is occupied: {destination}"
        raise FileExistsError(msg)


def link_path(path: Path) -> Path:
    expanded = path.expanduser()
    return expanded.parent.resolve() / expanded.name


def require_external_path(path: Path, repository: Path) -> None:
    resolved = link_path(path)
    repository_root = repository.resolve()
    if resolved.is_relative_to(repository_root) or repository_root.is_relative_to(resolved):
        msg = "Skill installation path must be outside the personal repository."
        raise SkillSyncError(msg)


@dataclass(frozen=True, slots=True)
class CapturedEntry:
    original: Path
    container: Path

    @property
    def path(self) -> Path:
        return self.container / "original"


@contextmanager
def capture_entries(entries: Mapping[Path, EntryState]) -> Generator[list[CapturedEntry]]:
    captured: list[CapturedEntry] = []
    try:
        for path, expected in entries.items():
            captured.append(_capture_entry(path, expected))
        yield captured
    except BaseException as exc:
        failures: list[str] = []
        for entry in reversed(captured):
            try:
                _restore_entry(entry.path, entry.original)
                shutil.rmtree(entry.container)
            except OSError as restore_error:
                failures.append(f"{entry.container}: {restore_error}")
        if failures:
            msg = f"Cannot restore removed entries; inspect recovery files: {'; '.join(failures)}"
            raise SkillSyncError(msg) from exc
        raise


def _capture_entry(path: Path, expected: EntryState) -> CapturedEntry:
    if link_path(path) != path:
        msg = f"Skill parent path changed; existing content preserved: {path}"
        raise SkillSyncError(msg)
    require_entry(path, expected)
    entry = CapturedEntry(
        path, Path(mkdtemp(prefix=f".skill-sync-remove-{path.name}-", dir=path.parent))
    )
    try:
        atomic_write_json(entry.container / "recovery.json", {"originalPath": str(path)})
        require_entry(path, expected)
        path.rename(entry.path)
        require_entry(entry.path, expected)
    except BaseException as exc:
        try:
            if entry_state(entry.path) is not None:
                _restore_entry(entry.path, path)
            shutil.rmtree(entry.container)
        except OSError as restore_error:
            msg = f"Original data kept at {entry.container}: {restore_error}"
            raise SkillSyncError(msg) from exc
        raise
    return entry


def cleanup_captured(entries: list[CapturedEntry]) -> tuple[Path, ...]:
    remaining: list[Path] = []
    for entry in entries:
        try:
            shutil.rmtree(entry.container)
        except OSError:
            remaining.append(entry.container)
    return tuple(remaining)
