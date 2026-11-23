from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from stat import S_ISDIR, S_ISLNK, S_ISREG
from tempfile import mkdtemp

from skill_sync.catalog import find_local_skill
from skill_sync.config import atomic_write_json
from skill_sync.errors import SkillSyncError, TargetSkillExistsError
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


@dataclass(frozen=True, slots=True)
class InstallResult:
    name: str
    path: Path
    source_path: Path
    backup_path: Path | None = None
    cleanup_path: Path | None = None


def agents_skills_root() -> Path:
    return Path.home() / ".agents" / "skills"


def install_skill(
    name: str,
    config: RepositoryConfig,
    *,
    force: bool = False,
    target_root: Path | None = None,
) -> InstallResult:
    # target_root is an internal filesystem-test seam, not a CLI option.
    with mutation_lock(config.state_home):
        require_current_binding(config)
        return _install_skill(name, config, force=force, target_root=target_root)


def _install_skill(
    name: str, config: RepositoryConfig, *, force: bool, target_root: Path | None
) -> InstallResult:
    skill = find_local_skill(config.repository_path, name)
    root = agents_skills_root() if target_root is None else target_root
    destination = link_path(root / name)
    require_external_path(destination, config.repository_path)
    records = read_managed_installations(config.state_home)
    expected = entry_state(destination)
    already_linked = symlink_target(destination) == skill.path
    same_skill = already_linked or _links_to_skill(destination, skill.path)
    if expected is not None and not same_skill:
        supported = any(test(expected.mode) for test in (S_ISDIR, S_ISREG, S_ISLNK))
        if not force or not supported or (S_ISDIR(expected.mode) and destination.is_mount()):
            msg = (
                f"Target skill already exists: {destination}; existing content was preserved. "
                "Pass --force to back up and replace an ordinary directory, file or link."
            )
            raise TargetSkillExistsError(msg)
    require_entry(destination, expected)
    records[str(destination)] = ManagedInstallation(name, "codex", destination, skill.path)
    backup_root = None
    cleanup_path = None
    committed = False
    try:
        if already_linked:
            require_entry(destination, expected)
            write_managed_installations(records, config.state_home)
        else:
            if expected is not None:
                backup_root = _prepare_backup(destination, skill.path)
            options = {"persistent_backup": backup_root} if backup_root is not None else {}
            with link_directory(destination, skill.path, expected=expected, **options):
                write_managed_installations(records, config.state_home)
                committed = True
    except OSError as exc:
        if not committed:
            msg = f"Failed to link skill {destination}: {exc}"
            raise SkillSyncError(msg) from exc
        cleanup_path = Path(exc.filename) if exc.filename is not None else backup_root
    return InstallResult(
        name,
        destination,
        skill.path,
        backup_path=backup_root / "original" if backup_root is not None else None,
        cleanup_path=cleanup_path,
    )


def _prepare_backup(destination: Path, source: Path) -> Path:
    root = destination.parent.parent / ".skill-sync-backups"
    if root.is_symlink():
        msg = f"Backup directory must be a real directory: {root}"
        raise SkillSyncError(msg)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    container = Path(mkdtemp(prefix=f"{destination.name}-{timestamp}-", dir=root))
    try:
        atomic_write_json(
            container / "backup.json",
            {
                "originalPath": str(destination),
                "sourcePath": str(source),
                "createdAt": timestamp,
                "kind": "symlink"
                if destination.is_symlink()
                else ("directory" if destination.is_dir() else "file"),
            },
        )
    except BaseException:
        (container / "backup.json").unlink(missing_ok=True)
        container.rmdir()
        raise
    return container


def _links_to_skill(destination: Path, source: Path) -> bool:
    try:
        return (
            destination.is_symlink()
            and destination.is_dir()
            and destination.resolve(strict=True) == source
        )
    except (OSError, RuntimeError):
        return False
