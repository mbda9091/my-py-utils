from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import mkdtemp
from typing import TYPE_CHECKING

from skill_sync.errors import SkillSyncError

if TYPE_CHECKING:
    from collections.abc import Generator


@contextmanager
def link_directory(destination: Path, source: Path) -> Generator[None]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup_root = Path(mkdtemp(prefix=".skill-sync-backup-", dir=destination.parent))
    backup = backup_root / "original"
    committed = False
    try:
        had_original = destination.exists() or destination.is_symlink()
        linked = False
        if had_original:
            destination.rename(backup)
        try:
            destination.symlink_to(source.resolve(), target_is_directory=True)
            linked = True
            yield
        except BaseException:
            try:
                if linked:
                    destination.unlink()
                if had_original:
                    backup.rename(destination)
            except OSError as exc:
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


def require_external_path(path: Path, checkout: Path) -> None:
    resolved = link_path(path)
    registry = checkout.resolve()
    if resolved.is_relative_to(registry) or registry.is_relative_to(resolved):
        msg = "Skill installation path must be outside the registry checkout."
        raise SkillSyncError(msg)
