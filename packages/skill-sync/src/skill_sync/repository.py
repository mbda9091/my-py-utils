from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from skill_sync.config import (
    LOCAL_SCHEMA_VERSION,
    atomic_write_json,
    config_path,
    get_state_home,
    installations_path,
    read_config,
)
from skill_sync.errors import LegacyConfigError, SkillSyncError
from skill_sync.git_repo import run_git

INIT_HINT = "Run 'skill-sync init --repo <local-repo>' to configure a local repository."


@dataclass(frozen=True, slots=True)
class RepositoryConfig:
    repository_path: Path
    state_home: Path


def init_repository(repository_path: Path, *, state_home: Path | None = None) -> RepositoryConfig:
    home = (get_state_home() if state_home is None else state_home).expanduser().resolve()
    repository = repository_path.expanduser().resolve()
    validate_repository(repository, home)
    path = config_path(home)
    existing = _read_payload(path) if path.exists() else None
    if existing is not None:
        version = existing.get("schemaVersion")
        if type(version) is int and version == 1:
            # Validate the old format before preserving it; a URL is never a local path.
            read_config(home)
        else:
            current = _config_from_payload(existing, home)
            if current.repository_path == repository:
                _create_skills_directory(repository)
                return current
        _backup_file(path)
        records = installations_path(home)
        if records.exists():
            _backup_file(records)

    _create_skills_directory(repository)
    config = RepositoryConfig(repository_path=repository, state_home=home)
    atomic_write_json(
        path, {"schemaVersion": LOCAL_SCHEMA_VERSION, "repositoryPath": str(repository)}
    )
    return config


def read_repository_config(state_home: Path | None = None) -> RepositoryConfig:
    home = (get_state_home() if state_home is None else state_home).expanduser().resolve()
    payload = _read_payload(config_path(home))
    config = _config_from_payload(payload, home)
    if config.repository_path.resolve() != config.repository_path:
        msg = f"Configured repository path now resolves to another location. {INIT_HINT}"
        raise SkillSyncError(msg)
    validate_repository(config.repository_path, home)
    return config


def validate_repository(repository: Path, state_home: Path) -> None:
    usage = (Path.home() / ".agents" / "skills").resolve()
    if repository.is_relative_to(usage) or usage.is_relative_to(repository):
        msg = f"Personal repository and agent skills directory must be separate: {repository}"
        raise SkillSyncError(msg)
    if state_home.is_relative_to(repository):
        msg = f"Personal repository must not contain the skill-sync state directory: {repository}"
        raise SkillSyncError(msg)
    if not repository.is_dir():
        msg = f"Local repository directory does not exist: {repository}"
        raise SkillSyncError(msg)
    inside = run_git(
        ["--no-optional-locks", "rev-parse", "--is-inside-work-tree"],
        cwd=repository,
    ).strip()
    if inside != "true":
        msg = f"Personal repository must be a Git working directory: {repository}"
        raise SkillSyncError(msg)
    root = run_git(
        ["--no-optional-locks", "rev-parse", "--show-toplevel"],
        cwd=repository,
    ).strip()
    if Path(root).resolve() != repository:
        msg = f"Use the Git repository root directory: {root}"
        raise SkillSyncError(msg)
    skills = repository / "skills"
    if skills.is_symlink() or (skills.exists() and not skills.is_dir()):
        msg = f"Personal skills path must be a real directory: {skills}"
        raise SkillSyncError(msg)


def _create_skills_directory(repository: Path) -> None:
    try:
        (repository / "skills").mkdir(exist_ok=True)
    except OSError as exc:
        msg = f"Unable to prepare skills directory: {exc}"
        raise SkillSyncError(msg) from exc


def _config_from_payload(payload: dict[str, object], home: Path) -> RepositoryConfig:
    version = payload.get("schemaVersion")
    if type(version) is int and version == 1:
        msg = (
            f"Legacy registry configuration detected. {INIT_HINT} "
            "Existing files have not been moved."
        )
        raise LegacyConfigError(msg)
    if type(version) is not int or version != LOCAL_SCHEMA_VERSION:
        msg = f"Unsupported configuration schemaVersion: {version}"
        raise SkillSyncError(msg)
    value = payload.get("repositoryPath")
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        msg = "Configuration repositoryPath must be an absolute local directory path."
        raise SkillSyncError(msg)
    return RepositoryConfig(repository_path=Path(value), state_home=home)


def _read_payload(path: Path) -> dict[str, object]:
    if not path.exists():
        raise SkillSyncError(INIT_HINT)
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        msg = f"Unable to read configuration JSON: {path}: {exc}"
        raise SkillSyncError(msg) from exc
    if not isinstance(payload, dict):
        msg = f"Configuration must contain a JSON object: {path}"
        raise SkillSyncError(msg)
    return cast("dict[str, object]", payload)


def _backup_file(path: Path) -> None:
    try:
        contents = path.read_bytes()
        with NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".bak",
            delete=False,
        ) as backup:
            backup.write(contents)
    except OSError as exc:
        msg = f"Unable to back up existing state: {path}: {exc}"
        raise SkillSyncError(msg) from exc
