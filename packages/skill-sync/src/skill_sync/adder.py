from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from skill_sync.catalog import ensure_registry_checkout, registry_skill_path
from skill_sync.config import Config, InstallationRecord, read_installations, write_installations
from skill_sync.errors import SkillSyncError
from skill_sync.git_repo import current_commit, validate_registry_repository
from skill_sync.links import link_directory, link_path, require_external_path
from skill_sync.publisher import write_sync_metadata
from skill_sync.validation import validate_local_skill


@dataclass(frozen=True, slots=True)
class AddResult:
    name: str
    path: Path
    registry_path: Path
    changed: bool


def add_skill(local_skill_dir: Path, config: Config) -> AddResult:
    ensure_registry_checkout(config.checkout)
    validate_registry_repository(config)
    source = link_path(local_skill_dir)
    destination = registry_skill_path(config.checkout, source.name).resolve()
    if source.is_symlink():
        if source.resolve() == destination and destination.is_dir():
            validate_local_skill(source)
            return AddResult(source.name, source, destination, changed=False)
        msg = f"Cannot add a symlink to another location: {source}"
        raise SkillSyncError(msg)
    require_external_path(source, config.checkout)
    metadata = validate_local_skill(source)
    if destination.exists():
        msg = f"Skill already exists in registry: {metadata.name}. No files were moved."
        raise SkillSyncError(msg)

    records = read_installations(config.state_home)
    records[f"codex:{metadata.name}"] = InstallationRecord(
        skill=metadata.name,
        target="codex",
        commit=current_commit(config.checkout),
        path=source,
    )
    installed = False
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".skill-sync-add-", dir=destination.parent) as temporary:
            staged = Path(temporary) / metadata.name
            # Keep every local file; publishing applies its own filters and safety checks.
            shutil.copytree(source, staged, symlinks=True)
            write_sync_metadata(staged, metadata, datetime.now(UTC))
            staged.rename(destination)
            installed = True
            with link_directory(source, destination):
                write_installations(records, config.state_home)
    except (OSError, SkillSyncError) as exc:
        # link_directory restores the original on failure. Never delete a live link's data.
        if installed and not (source.is_symlink() and source.resolve() == destination):
            shutil.rmtree(destination)
        msg = f"Failed to add skill {source}: {exc}"
        raise SkillSyncError(msg) from exc
    return AddResult(metadata.name, source, destination, changed=True)
