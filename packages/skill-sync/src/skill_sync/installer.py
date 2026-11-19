from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from skill_sync.catalog import find_skill
from skill_sync.config import Config, InstallationRecord, read_installations, write_installations
from skill_sync.errors import SkillSyncError, TargetSkillExistsError
from skill_sync.git_repo import current_commit
from skill_sync.links import link_directory, link_path, require_external_path


@dataclass(frozen=True, slots=True)
class InstallResult:
    name: str
    target: str
    path: Path
    commit: str


def install_skill(
    name: str,
    config: Config,
    *,
    target: str,
    force: bool = False,
    target_root: Path | None = None,
) -> InstallResult:
    if target != "codex":
        msg = "MVP only supports --target codex."
        raise SkillSyncError(msg)

    registry_skill = find_skill(config.checkout, name)
    commit = current_commit(config.checkout)
    skills_root = (Path.home() / ".agents" / "skills") if target_root is None else target_root
    destination = link_path(skills_root / name)
    already_linked = (
        destination.is_symlink() and destination.resolve() == registry_skill.path.resolve()
    )
    if (destination.exists() or destination.is_symlink()) and not already_linked and not force:
        msg = f"Target skill already exists: {destination}. Pass --force to overwrite it."
        raise TargetSkillExistsError(msg)
    if not already_linked:
        require_external_path(destination, config.checkout)

    records = read_installations(config.state_home)
    records[f"{target}:{name}"] = InstallationRecord(
        skill=name,
        target=target,
        commit=commit,
        path=destination,
    )
    try:
        if already_linked:
            write_installations(records, config.state_home)
        else:
            with link_directory(destination, registry_skill.path):
                write_installations(records, config.state_home)
    except OSError as exc:
        msg = f"Failed to link skill {destination}: {exc}"
        raise SkillSyncError(msg) from exc
    return InstallResult(name=name, target=target, path=destination, commit=commit)
