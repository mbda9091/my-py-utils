from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from skill_sync.installations import (
    ManagedInstallation,
    read_managed_installations,
    symlink_target,
    write_managed_installations,
)
from skill_sync.installer import agents_skills_root
from skill_sync.links import (
    capture_entries,
    cleanup_captured,
    entry_state,
    link_path,
    require_external_path,
)
from skill_sync.locking import mutation_lock
from skill_sync.repository import RepositoryConfig, require_current_binding
from skill_sync.validation import validate_skill_name

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class UninstallResult:
    name: str
    path: Path
    changed: bool
    skipped: bool = False
    cleanup_paths: tuple[Path, ...] = ()


def matching_record(record: ManagedInstallation | None, name: str, path: Path) -> bool:
    return (
        record is not None
        and record.skill == name
        and record.path == path
        and record.source_path is not None
    )


def uninstall_skill(name: str, config: RepositoryConfig) -> UninstallResult:
    with mutation_lock(config.state_home):
        require_current_binding(config, allow_missing=True)
        validate_skill_name(name)
        path = link_path(agents_skills_root() / name)
        require_external_path(path, config.repository_path)
        records = read_managed_installations(config.state_home)
        record = records.get(str(path))
        expected = entry_state(path)
        matches = matching_record(record, name, path)
        actual = symlink_target(path)
        owned = actual == config.repository_path / "skills" / name or (
            matches and record is not None and actual == record.source_path
        )
        if expected is not None and not owned:
            return UninstallResult(name, path, changed=False, skipped=True)
        if matches or (expected is not None and owned):
            records.pop(str(path), None)
        if expected is None:
            if matches:
                write_managed_installations(records, config.state_home)
            return UninstallResult(name, path, changed=False)
        with capture_entries({path: expected}) as captured:
            write_managed_installations(records, config.state_home)
        return UninstallResult(name, path, changed=True, cleanup_paths=cleanup_captured(captured))
