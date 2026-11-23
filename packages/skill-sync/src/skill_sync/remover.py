from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from skill_sync.catalog import personal_skill_path
from skill_sync.errors import SkillSyncError
from skill_sync.installations import (
    ManagedInstallation,
    read_managed_installations,
    symlink_target,
    write_managed_installations,
)
from skill_sync.installer import agents_skills_root
from skill_sync.links import (
    EntryState,
    capture_entries,
    cleanup_captured,
    entry_state,
    link_path,
    require_external_path,
)
from skill_sync.locking import mutation_lock
from skill_sync.repository import RepositoryConfig, require_current_binding


@dataclass(frozen=True, slots=True)
class RemoveResult:
    name: str
    repository_path: Path
    removed: bool
    removed_links: tuple[Path, ...]
    skipped_paths: tuple[Path, ...]
    cleanup_paths: tuple[Path, ...]


def remove_skill(name: str, config: RepositoryConfig) -> RemoveResult:
    with mutation_lock(config.state_home):
        require_current_binding(config, allow_missing=True)
        source = personal_skill_path(config.repository_path, name)
        expected = entry_state(source)
        if expected is not None:
            _check_removable_tree(source)
        records = read_managed_installations(config.state_home)
        entries, skipped = _select_links(name, source, records, config)
        links = tuple(entries)
        for path in entries:
            _forget_current_record(records, path, name, source)
        if expected is not None:
            entries[source] = expected
        with capture_entries(entries) as captured:
            write_managed_installations(records, config.state_home)
        return RemoveResult(
            name, source, expected is not None, links, tuple(skipped), cleanup_captured(captured)
        )


def _select_links(
    name: str,
    source: Path,
    records: dict[str, ManagedInstallation],
    config: RepositoryConfig,
) -> tuple[dict[Path, EntryState], list[Path]]:
    candidates = {
        record.path
        for record in records.values()
        if record.skill == name and record.source_path == source
    }
    candidates.add(link_path(agents_skills_root() / name))
    entries: dict[Path, EntryState] = {}
    skipped: list[Path] = []
    for path in sorted(candidates):
        try:
            require_external_path(path, config.repository_path)
            if link_path(path) != path:
                skipped.append(path)
                continue
        except SkillSyncError:
            skipped.append(path)
            continue
        expected = entry_state(path)
        if expected is None:
            _forget_current_record(records, path, name, source)
        elif symlink_target(path) == source:
            entries[path] = expected
        else:
            skipped.append(path)
    return entries, skipped


def _forget_current_record(
    records: dict[str, ManagedInstallation], path: Path, name: str, source: Path
) -> None:
    record = records.get(str(path))
    if record is not None and record.skill == name and record.source_path == source:
        records.pop(str(path))


def _check_removable_tree(source: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        msg = f"Personal skill must be a real directory: {source}"
        raise SkillSyncError(msg)
    device = source.stat().st_dev

    def on_error(error: OSError) -> None:
        raise error

    for parent, names, _files in os.walk(source, followlinks=False, onerror=on_error):
        for path in (Path(parent), *(Path(parent) / name for name in names)):
            if not path.is_symlink() and (path.is_mount() or path.stat().st_dev != device):
                msg = f"Cannot remove a skill containing a mounted directory: {path}"
                raise SkillSyncError(msg)
