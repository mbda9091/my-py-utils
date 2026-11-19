from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

from skill_sync.errors import SkillSyncError

if TYPE_CHECKING:
    from collections.abc import Mapping

LEGACY_SCHEMA_VERSION = 1
LOCAL_SCHEMA_VERSION = 2
STATE_HOME_ENV = "SKILL_SYNC_HOME"


def get_state_home(environ: Mapping[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    configured = source.get(STATE_HOME_ENV)
    return Path(configured).expanduser() if configured else Path.home() / ".skill-sync"


def config_path(state_home: Path) -> Path:
    return state_home / "config.json"


def installations_path(state_home: Path) -> Path:
    return state_home / "installations.json"


def legacy_repository_path(payload: Mapping[str, object]) -> Path:
    version = payload.get("schemaVersion")
    if type(version) is not int or version != LEGACY_SCHEMA_VERSION:
        msg = f"Unsupported legacy configuration schemaVersion: {version}"
        raise SkillSyncError(msg)
    checkout = payload.get("checkout")
    if (
        not isinstance(checkout, str)
        or not checkout
        or not Path(checkout).expanduser().is_absolute()
    ):
        msg = "Legacy configuration checkout must be an absolute local directory path."
        raise SkillSyncError(msg)
    # Old remote, branch and identity fields are data, never executable configuration.
    return Path(os.path.normpath(Path(checkout).expanduser()))


def atomic_write_json(path: Path, payload: object) -> None:
    contents = f"{json.dumps(payload, indent=2, sort_keys=True)}\n".encode()
    atomic_write_bytes(path, contents)


def atomic_write_bytes(path: Path, contents: bytes) -> None:
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(contents)
        temporary_path.replace(path)
    except OSError as exc:
        msg = f"Unable to write state file: {path}: {exc}"
        raise SkillSyncError(msg) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
