from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from skill_sync.catalog import SYNC_METADATA_FILE, find_skill
from skill_sync.config import Config, InstallationRecord, read_installations, write_installations
from skill_sync.errors import SkillSyncError, TargetSkillExistsError
from skill_sync.git_repo import current_commit
from skill_sync.validation import validate_skill_name


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
    validate_skill_name(name)
    if target != "codex":
        msg = "MVP only supports --target codex."
        raise SkillSyncError(msg)

    registry_skill = find_skill(config.checkout, name)
    commit = current_commit(config.checkout)
    skills_root = (Path.home() / ".agents" / "skills") if target_root is None else target_root
    destination = skills_root / name
    if destination.exists() and not force:
        msg = f"Target skill already exists: {destination}. Pass --force to overwrite it."
        raise TargetSkillExistsError(msg)

    skills_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="skill-sync-install-", dir=skills_root) as temporary:
        staged_skill = Path(temporary) / name
        copy_install_tree(registry_skill.path, staged_skill)
        replace_directory(staged_skill, destination)

    records = read_installations(config.state_home)
    key = f"{target}:{name}"
    records[key] = InstallationRecord(
        skill=name,
        target=target,
        commit=commit,
        path=destination.resolve(),
    )
    write_installations(records, config.state_home)
    return InstallResult(name=name, target=target, path=destination.resolve(), commit=commit)


def copy_install_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, ignore=_install_ignore, symlinks=True)


def replace_directory(source: Path, destination: Path) -> None:
    if not destination.exists():
        source.replace(destination)
        return

    backup = destination.with_name(f".{destination.name}.skill-sync-backup")
    if backup.exists():
        shutil.rmtree(backup)
    destination.replace(backup)
    try:
        source.replace(destination)
    except OSError as exc:
        if destination.exists():
            shutil.rmtree(destination)
        backup.replace(destination)
        msg = f"Failed to replace directory {destination}: {exc}"
        raise SkillSyncError(msg) from exc
    else:
        shutil.rmtree(backup)


def _install_ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name == SYNC_METADATA_FILE}
