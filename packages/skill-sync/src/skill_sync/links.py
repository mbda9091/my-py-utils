from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISLNK
from tempfile import mkdtemp
from typing import TYPE_CHECKING

from skill_sync.errors import SkillSyncError

if TYPE_CHECKING:
    from collections.abc import Generator


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
    destination: Path, source: Path, *, expected: EntryState | None
) -> Generator[Path | None]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if expected is None:
        # symlink(2) fails if another actor created any entry, including a directory.
        destination.symlink_to(source.resolve(), target_is_directory=True)
        created = entry_state(destination)
        try:
            yield None
        except BaseException:
            require_entry(destination, created)
            destination.unlink()
            raise
        return
    backup_root = Path(mkdtemp(prefix=".skill-sync-backup-", dir=destination.parent))
    backup = backup_root / "original"
    committed = False
    try:
        require_entry(destination, expected)
        linked = False
        created = None
        destination.rename(backup)
        try:
            # Recheck the moved entry: a replacement between check and rename must
            # be restored, never discarded as a successful installation backup.
            require_entry(backup, expected)
            destination.symlink_to(source.resolve(), target_is_directory=True)
            linked = True
            created = entry_state(destination)
            yield backup
        except BaseException:
            try:
                if linked:
                    require_entry(destination, created)
                    destination.unlink()
                if backup.is_symlink():
                    # A hard link to the symlink itself restores without overwriting
                    # a newly created entry at the destination.
                    os.link(backup, destination, follow_symlinks=False)
                    backup.unlink()
                elif not (destination.exists() or destination.is_symlink()):
                    backup.rename(destination)
                else:
                    msg = f"Restore destination is occupied: {destination}"
                    raise FileExistsError(msg)
            except (OSError, SkillSyncError) as exc:
                msg = f"Failed to restore {destination}. Original data kept at {backup}: {exc}"
                raise SkillSyncError(msg) from exc
            raise
        else:
            committed = True
    finally:
        if committed or not (backup.exists() or backup.is_symlink()):
            shutil.rmtree(backup_root)


def link_path(path: Path) -> Path:
    expanded = path.expanduser()
    return expanded.parent.resolve() / expanded.name


def require_external_path(path: Path, repository: Path) -> None:
    resolved = link_path(path)
    repository_root = repository.resolve()
    if resolved.is_relative_to(repository_root) or repository_root.is_relative_to(resolved):
        msg = "Skill installation path must be outside the personal repository."
        raise SkillSyncError(msg)
