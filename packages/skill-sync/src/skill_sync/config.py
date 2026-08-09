from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, NotRequired, TypedDict, cast

from skill_sync.errors import SkillSyncError

if TYPE_CHECKING:
    from collections.abc import Mapping

SCHEMA_VERSION = 1
STATE_HOME_ENV = "SKILL_SYNC_HOME"


class ConfigPayload(TypedDict):
    schemaVersion: int
    repository: str
    branch: str
    checkout: str
    gitIdentity: NotRequired[GitIdentityPayload]


class GitIdentityPayload(TypedDict):
    name: str
    email: str


class InstallationPayload(TypedDict):
    skill: str
    target: str
    commit: str
    path: str


@dataclass(frozen=True, slots=True)
class GitIdentity:
    name: str
    email: str


@dataclass(frozen=True, slots=True)
class Config:
    repository: str
    branch: str
    checkout: Path
    state_home: Path
    git_identity: GitIdentity | None = None


@dataclass(frozen=True, slots=True)
class InstallationRecord:
    skill: str
    target: str
    commit: str
    path: Path


def get_state_home(environ: Mapping[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    configured = source.get(STATE_HOME_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".skill-sync"


def default_checkout_path(state_home: Path) -> Path:
    return state_home / "repository"


def config_path(state_home: Path) -> Path:
    return state_home / "config.json"


def installations_path(state_home: Path) -> Path:
    return state_home / "installations.json"


def init_config(
    repository: str,
    *,
    branch: str,
    git_name: str | None = None,
    git_email: str | None = None,
    state_home: Path | None = None,
) -> Config:
    if not repository:
        msg = "Git repository URL must not be empty."
        raise SkillSyncError(msg)
    if not branch:
        msg = "Git branch must not be empty."
        raise SkillSyncError(msg)

    resolved_state_home = get_state_home() if state_home is None else state_home.expanduser()
    checkout = default_checkout_path(resolved_state_home)
    requested_identity = _build_git_identity(git_name, git_email)
    path = config_path(resolved_state_home)

    if path.exists():
        existing = read_config(resolved_state_home)
        if (
            existing.repository == repository
            and existing.branch == branch
            and existing.checkout == checkout
        ):
            if requested_identity is None or existing.git_identity == requested_identity:
                return existing
            updated = Config(
                repository=existing.repository,
                branch=existing.branch,
                checkout=existing.checkout,
                state_home=existing.state_home,
                git_identity=requested_identity,
            )
            atomic_write_json(path, config_to_payload(updated))
            return updated
        msg = f"Configuration already exists with different values: {path}"
        raise SkillSyncError(msg)

    config = Config(
        repository=repository,
        branch=branch,
        checkout=checkout,
        state_home=resolved_state_home,
        git_identity=requested_identity,
    )
    payload = config_to_payload(config)
    atomic_write_json(path, payload)
    return config


def read_config(state_home: Path | None = None) -> Config:
    resolved_state_home = get_state_home() if state_home is None else state_home.expanduser()
    path = config_path(resolved_state_home)
    if not path.exists():
        msg = "skill-sync is not initialized. Run 'skill-sync init <git-url>' first."
        raise SkillSyncError(msg)

    try:
        raw_payload: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"Configuration file is not valid JSON: {path}: {exc}"
        raise SkillSyncError(msg) from exc
    except OSError as exc:
        msg = f"Unable to read configuration file: {path}: {exc}"
        raise SkillSyncError(msg) from exc

    if not isinstance(raw_payload, dict):
        msg = f"Configuration file must contain a JSON object: {path}"
        raise SkillSyncError(msg)

    payload = cast("dict[str, object]", raw_payload)
    config_payload = _validate_config_payload(payload, path)
    return Config(
        repository=config_payload["repository"],
        branch=config_payload["branch"],
        checkout=Path(config_payload["checkout"]).expanduser(),
        state_home=resolved_state_home,
        git_identity=_read_git_identity(config_payload),
    )


def config_to_payload(config: Config) -> ConfigPayload:
    payload = ConfigPayload(
        schemaVersion=SCHEMA_VERSION,
        repository=config.repository,
        branch=config.branch,
        checkout=str(config.checkout),
    )
    if config.git_identity is not None:
        payload["gitIdentity"] = GitIdentityPayload(
            name=config.git_identity.name,
            email=config.git_identity.email,
        )
    return payload


def read_installations(state_home: Path | None = None) -> dict[str, InstallationRecord]:
    resolved_state_home = get_state_home() if state_home is None else state_home.expanduser()
    path = installations_path(resolved_state_home)
    if not path.exists():
        return {}

    try:
        raw_payload: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"Installations file is not valid JSON: {path}: {exc}"
        raise SkillSyncError(msg) from exc
    except OSError as exc:
        msg = f"Unable to read installations file: {path}: {exc}"
        raise SkillSyncError(msg) from exc

    if not isinstance(raw_payload, dict):
        msg = f"Installations file must contain a JSON object: {path}"
        raise SkillSyncError(msg)

    payload = cast("dict[str, object]", raw_payload)
    records: dict[str, InstallationRecord] = {}
    for key, value in payload.items():
        if not isinstance(value, dict):
            msg = f"Invalid installation record in {path}: {key}"
            raise SkillSyncError(msg)
        records[key] = _installation_from_payload(cast("dict[str, object]", value), path)
    return records


def write_installations(
    records: Mapping[str, InstallationRecord],
    state_home: Path | None = None,
) -> None:
    resolved_state_home = get_state_home() if state_home is None else state_home.expanduser()
    payload: dict[str, InstallationPayload] = {
        key: {
            "skill": record.skill,
            "target": record.target,
            "commit": record.commit,
            "path": str(record.path),
        }
        for key, record in sorted(records.items())
    }
    atomic_write_json(installations_path(resolved_state_home), payload)


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(payload, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    except OSError as exc:
        msg = f"Unable to write JSON file: {path}: {exc}"
        raise SkillSyncError(msg) from exc


def _validate_config_payload(payload: Mapping[str, object], path: Path) -> ConfigPayload:
    schema_version = payload.get("schemaVersion")
    repository = payload.get("repository")
    branch = payload.get("branch")
    checkout = payload.get("checkout")
    if not isinstance(schema_version, int) or schema_version != SCHEMA_VERSION:
        msg = f"Unsupported configuration schemaVersion in {path}: {schema_version}"
        raise SkillSyncError(msg)
    if not isinstance(repository, str) or not repository:
        msg = f"Configuration is missing repository: {path}"
        raise SkillSyncError(msg)
    if not isinstance(branch, str) or not branch:
        msg = f"Configuration is missing branch: {path}"
        raise SkillSyncError(msg)
    if not isinstance(checkout, str) or not checkout:
        msg = f"Configuration is missing checkout: {path}"
        raise SkillSyncError(msg)

    config_payload = ConfigPayload(
        schemaVersion=schema_version,
        repository=repository,
        branch=branch,
        checkout=checkout,
    )
    git_identity = payload.get("gitIdentity")
    if git_identity is not None:
        if not isinstance(git_identity, dict):
            msg = f"Configuration gitIdentity must be an object: {path}"
            raise SkillSyncError(msg)
        identity_payload = _validate_git_identity_payload(
            cast("dict[str, object]", git_identity),
            path,
        )
        config_payload["gitIdentity"] = identity_payload
    return config_payload


def _build_git_identity(git_name: str | None, git_email: str | None) -> GitIdentity | None:
    if git_name is None and git_email is None:
        return None
    if not git_name or not git_email:
        msg = "--git-name and --git-email must be provided together."
        raise SkillSyncError(msg)
    return GitIdentity(name=git_name, email=git_email)


def _read_git_identity(payload: ConfigPayload) -> GitIdentity | None:
    identity = payload.get("gitIdentity")
    if identity is None:
        return None
    return GitIdentity(name=identity["name"], email=identity["email"])


def _validate_git_identity_payload(
    payload: Mapping[str, object],
    path: Path,
) -> GitIdentityPayload:
    name = payload.get("name")
    email = payload.get("email")
    if not isinstance(name, str) or not name:
        msg = f"Configuration gitIdentity is missing name: {path}"
        raise SkillSyncError(msg)
    if not isinstance(email, str) or not email:
        msg = f"Configuration gitIdentity is missing email: {path}"
        raise SkillSyncError(msg)
    return GitIdentityPayload(name=name, email=email)


def _installation_from_payload(value: Mapping[str, object], path: Path) -> InstallationRecord:
    skill = value.get("skill")
    target = value.get("target")
    commit = value.get("commit")
    install_path = value.get("path")
    if not isinstance(skill, str) or not skill:
        msg = f"Installation record is missing skill in {path}."
        raise SkillSyncError(msg)
    if not isinstance(target, str) or not target:
        msg = f"Installation record is missing target in {path}."
        raise SkillSyncError(msg)
    if not isinstance(commit, str) or not commit:
        msg = f"Installation record is missing commit in {path}."
        raise SkillSyncError(msg)
    if not isinstance(install_path, str) or not install_path:
        msg = f"Installation record is missing path in {path}."
        raise SkillSyncError(msg)

    return InstallationRecord(
        skill=skill,
        target=target,
        commit=commit,
        path=Path(install_path),
    )
