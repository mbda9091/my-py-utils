from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from skill_sync.catalog import find_local_skill
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
    target: str
    path: Path
    source_path: Path


def install_skill(
    name: str,
    config: RepositoryConfig,
    *,
    target: str,
    force: bool = False,
    target_root: Path | None = None,
) -> InstallResult:
    with mutation_lock(config.state_home):
        require_current_binding(config)
        return _install_skill(name, config, target=target, force=force, target_root=target_root)


def _install_skill(
    name: str,
    config: RepositoryConfig,
    *,
    target: str,
    force: bool,
    target_root: Path | None,
) -> InstallResult:
    if target != "codex":
        msg = "Only --target codex is supported."
        raise SkillSyncError(msg)
    skill = find_local_skill(config.repository_path, name)
    skills_root = (Path.home() / ".agents" / "skills") if target_root is None else target_root
    destination = link_path(skills_root / name)
    require_external_path(destination, config.repository_path)
    source = skill.path
    records = read_managed_installations(config.state_home)
    previous = records.get(str(destination))
    expected = entry_state(destination)
    actual = symlink_target(destination)
    already_linked = actual == source
    same_skill = destination.is_symlink() and destination.resolve() == source
    if (destination.exists() or destination.is_symlink()) and not same_skill:
        owned = (
            previous is not None
            and previous.skill == name
            and previous.path == destination
            and previous.source_path is not None
            and actual == previous.source_path
        )
        if not owned or not force:
            detail = (
                "Pass --force to relink this recorded personal skill."
                if owned
                else ("Existing content is not a verified personal skill link; it was preserved.")
            )
            msg = f"Target skill already exists: {destination}. {detail}"
            raise TargetSkillExistsError(msg)
    require_entry(destination, expected)
    records[str(destination)] = ManagedInstallation(name, target, destination, source)
    try:
        if already_linked:
            require_entry(destination, expected)
            write_managed_installations(records, config.state_home)
        else:
            with link_directory(destination, source, expected=expected):
                write_managed_installations(records, config.state_home)
    except OSError as exc:
        msg = f"Failed to link skill {destination}: {exc}"
        raise SkillSyncError(msg) from exc
    return InstallResult(name=name, target=target, path=destination, source_path=source)
