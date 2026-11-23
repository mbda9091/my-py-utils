from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from skill_sync.config import (
    LOCAL_SCHEMA_VERSION,
    atomic_write_bytes,
    atomic_write_json,
    config_path,
    get_state_home,
    installations_path,
    legacy_repository_path,
)
from skill_sync.errors import LegacyConfigError, SkillSyncError
from skill_sync.installations import installations_payload, read_managed_installations
from skill_sync.locking import mutation_lock

INIT_HINT = "Run 'skill-sync init --repo <local-repo>' to configure a local repository."


@dataclass(frozen=True, slots=True)
class RepositoryConfig:
    repository_path: Path
    state_home: Path


def init_repository(repository_path: Path, *, state_home: Path | None = None) -> RepositoryConfig:
    home = (get_state_home() if state_home is None else state_home).expanduser().resolve()
    repository = repository_path.expanduser().resolve()
    # Reject invalid locations before creating the state directory for the lock.
    validate_repository(repository, home)
    with mutation_lock(home):
        return _init_repository(repository, home)


def _init_repository(repository: Path, home: Path) -> RepositoryConfig:
    validate_repository(repository, home)
    path = config_path(home)
    existing = _read_payload(path) if path.exists() else None
    legacy_repository: Path | None = None
    config_changed = True
    if existing is not None:
        version = existing.get("schemaVersion")
        if type(version) is int and version == 1:
            # Validate the old format before preserving it; a URL is never a local path.
            legacy_repository = legacy_repository_path(existing)
        else:
            current = _config_from_payload(existing, home)
            if current.repository_path == repository:
                config_changed = False

    records_path = installations_path(home)
    old_records = records_path.read_bytes() if records_path.exists() else None
    records = read_managed_installations(home, legacy_repository=legacy_repository)
    records_payload = installations_payload(records)
    records_changed = old_records is not None and json.loads(old_records) != records_payload
    old_config = path.read_bytes() if path.exists() else None
    if existing is not None and (config_changed or records_changed):
        _backup_file(path)
        if records_path.exists():
            _backup_file(records_path)

    _create_skills_directory(repository)
    config = RepositoryConfig(repository_path=repository, state_home=home)
    try:
        if records_changed:
            atomic_write_json(records_path, records_payload)
        if config_changed:
            atomic_write_json(
                path, {"schemaVersion": LOCAL_SCHEMA_VERSION, "repositoryPath": str(repository)}
            )
    except (OSError, SkillSyncError) as exc:
        try:
            _restore_file(path, old_config)
            _restore_file(records_path, old_records)
        except (OSError, SkillSyncError) as restore_error:
            msg = f"Cannot restore binding; original state retained in *.bak: {restore_error}"
            raise SkillSyncError(msg) from exc
        raise
    return config


def require_current_binding(config: RepositoryConfig, *, allow_missing: bool = False) -> None:
    current = read_repository_config(config.state_home, allow_missing=allow_missing)
    if current != config:
        msg = "Personal repository binding changed. Reload configuration and retry."
        raise SkillSyncError(msg)


def _restore_file(path: Path, contents: bytes | None) -> None:
    if contents is None:
        path.unlink(missing_ok=True)
    elif not path.exists() or path.read_bytes() != contents:
        atomic_write_bytes(path, contents)


def read_repository_config(
    state_home: Path | None = None, *, allow_missing: bool = False
) -> RepositoryConfig:
    home = (get_state_home() if state_home is None else state_home).expanduser().resolve()
    payload = _read_payload(config_path(home))
    config = _config_from_payload(payload, home)
    try:
        resolved = config.repository_path.resolve()
    except (OSError, RuntimeError) as exc:
        msg = f"Cannot resolve configured repository path: {exc}"
        raise SkillSyncError(msg) from exc
    if resolved != config.repository_path or config.repository_path.is_symlink():
        msg = f"Configured repository path now resolves to another location. {INIT_HINT}"
        raise SkillSyncError(msg)
    if allow_missing and not config.repository_path.exists():
        _validate_locations(config.repository_path, home)
    else:
        validate_repository(config.repository_path, home)
    return config


def _validate_locations(repository: Path, state_home: Path) -> None:
    usage = (Path.home() / ".agents" / "skills").resolve()
    if repository.is_relative_to(usage) or usage.is_relative_to(repository):
        msg = f"Personal repository and agent skills directory must be separate: {repository}"
        raise SkillSyncError(msg)
    if state_home.is_relative_to(repository):
        msg = f"Personal repository must not contain the skill-sync state directory: {repository}"
        raise SkillSyncError(msg)


def validate_repository(repository: Path, state_home: Path) -> None:
    _validate_locations(repository, state_home)
    if not repository.is_dir():
        msg = f"Local repository directory does not exist: {repository}"
        raise SkillSyncError(msg)
    root = _git_worktree_root(repository)
    if Path(root).resolve() != repository:
        msg = f"Use the Git repository root directory: {root}"
        raise SkillSyncError(msg)
    skills = repository / "skills"
    if skills.is_symlink() or (skills.exists() and not skills.is_dir()):
        msg = f"Personal skills path must be a real directory: {skills}"
        raise SkillSyncError(msg)


def _git_worktree_root(repository: Path) -> str:
    executable = shutil.which("git")
    if executable is None:
        msg = "git command not found."
        raise SkillSyncError(msg)
    try:
        result = subprocess.run(  # noqa: S603
            [executable, "--no-optional-locks", "rev-parse", "--show-toplevel"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        msg = f"Unable to inspect local Git working directory {repository}: {exc}"
        raise SkillSyncError(msg) from exc
    if result.returncode != 0:
        msg = f"Cannot inspect local Git working directory {repository}: {result.stderr.strip()}"
        raise SkillSyncError(msg)
    return result.stdout.strip()


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
