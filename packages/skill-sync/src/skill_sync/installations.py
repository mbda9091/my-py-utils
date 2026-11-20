from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, cast

from skill_sync.config import LOCAL_SCHEMA_VERSION, atomic_write_json, installations_path
from skill_sync.errors import SkillSyncError
from skill_sync.links import link_path
from skill_sync.validation import validate_skill_name

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class ManagedInstallation:
    skill: str
    target: str
    path: Path
    source_path: Path | None


def symlink_target(path: Path) -> Path | None:
    """Return a provable target without following intermediate symlink aliases.

    Parent traversal requires an existing real directory. Missing suffixes are
    allowed so a recorded direct link into a removed repository stays usable.
    """
    if not path.is_symlink():
        return None
    target = path.readlink()
    absolute = path.absolute().parent / target
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        if part == "..":
            if not current.is_dir():
                return None
            current = current.parent
        else:
            current /= part
            if current.is_symlink():
                return None
    return current


def read_managed_installations(
    state_home: Path,
    *,
    legacy_repository: Path | None = None,
) -> dict[str, ManagedInstallation]:
    path = installations_path(state_home)
    if not path.exists():
        return {}
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        msg = f"Unable to read installation records: {path}: {exc}"
        raise SkillSyncError(msg) from exc
    if not isinstance(payload, dict):
        msg = f"Installation records must contain a JSON object: {path}"
        raise SkillSyncError(msg)
    data = cast("dict[str, object]", payload)
    if "schemaVersion" not in data:
        return _convert_legacy(data, state_home, legacy_repository)
    if type(data["schemaVersion"]) is not int or data["schemaVersion"] != LOCAL_SCHEMA_VERSION:
        msg = f"Unsupported installation records schemaVersion: {data['schemaVersion']}"
        raise SkillSyncError(msg)
    raw_records = data.get("installations")
    if not isinstance(raw_records, dict):
        msg = "Installation records must contain an installations object."
        raise SkillSyncError(msg)
    records: dict[str, ManagedInstallation] = {}
    for key, raw in cast("dict[str, object]", raw_records).items():
        record = _read_record(raw)
        if key != str(record.path):
            msg = f"Installation key must match its use path: {key}"
            raise SkillSyncError(msg)
        records[key] = record
    return records


def installations_payload(records: Mapping[str, ManagedInstallation]) -> dict[str, object]:
    return {
        "schemaVersion": LOCAL_SCHEMA_VERSION,
        "installations": {
            key: {
                "skill": record.skill,
                "target": record.target,
                "path": str(record.path),
                "sourcePath": str(record.source_path) if record.source_path is not None else None,
            }
            for key, record in sorted(records.items())
        },
    }


def write_managed_installations(
    records: Mapping[str, ManagedInstallation],
    state_home: Path,
) -> None:
    path = installations_path(state_home)
    if path.exists():
        contents = path.read_bytes()
        payload: object = json.loads(contents)
        if isinstance(payload, dict) and "schemaVersion" not in payload:
            with NamedTemporaryFile(
                "wb",
                dir=state_home,
                prefix="installations.json.",
                suffix=".bak",
                delete=False,
            ) as backup:
                backup.write(contents)
    atomic_write_json(path, installations_payload(records))


def installation_status(record: ManagedInstallation, repository: Path) -> str:
    path = record.path
    if not path.is_symlink():
        return "replaced" if path.exists() else "missing"
    if record.source_path is None:
        return "unresolved"
    if link_path(path) != path or symlink_target(path) != record.source_path:
        return "replaced"
    if not path.exists():
        return "broken"
    expected = repository / "skills" / record.skill
    if record.source_path != expected:
        return "previous-repository"
    return "ok" if path.resolve() == expected else "replaced"


def _read_record(raw: object) -> ManagedInstallation:
    if not isinstance(raw, dict):
        msg = "Invalid installation record."
        raise SkillSyncError(msg)
    record = cast("dict[str, object]", raw)
    name = record.get("skill")
    target = record.get("target")
    if not isinstance(name, str) or not isinstance(target, str) or not target:
        msg = "Installation record requires a skill and target."
        raise SkillSyncError(msg)
    validate_skill_name(name)
    path = _absolute_path(record.get("path"), "path")
    source = record.get("sourcePath")
    source_path = _absolute_path(source, "sourcePath") if source is not None else None
    if source_path is not None and (
        source_path.name != name or source_path.parent.name != "skills"
    ):
        msg = f"Installation source must name the recorded repository skill: {path}"
        raise SkillSyncError(msg)
    return ManagedInstallation(name, target, path, source_path)


def _absolute_path(value: object, field: str) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        msg = f"Installation {field} must be an absolute path."
        raise SkillSyncError(msg)
    return Path(os.path.normpath(value))


def _legacy_roots(state_home: Path, explicit: Path | None) -> set[Path]:
    roots = {explicit} if explicit is not None else set[Path]()
    for backup in state_home.glob("config.json.*.bak"):
        try:
            payload: object = json.loads(backup.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        data = cast("dict[str, object]", payload)
        version = data.get("schemaVersion")
        if type(version) is not int or version not in {1, LOCAL_SCHEMA_VERSION}:
            continue
        value = data.get("checkout") if version == 1 else data.get("repositoryPath")
        if isinstance(value, str) and Path(value).expanduser().is_absolute():
            roots.add(Path(value).expanduser())
    return roots


def _convert_legacy(
    payload: dict[str, object],
    state_home: Path,
    legacy_repository: Path | None,
) -> dict[str, ManagedInstallation]:
    roots = _legacy_roots(state_home, legacy_repository)
    records: dict[str, ManagedInstallation] = {}
    for raw in payload.values():
        legacy = _read_record(raw)
        actual = symlink_target(legacy.path)
        candidates = {root / "skills" / legacy.skill for root in roots}
        source = actual if actual in candidates and link_path(legacy.path) == legacy.path else None
        default = link_path(Path.home() / ".agents" / "skills" / legacy.skill)
        target = "codex" if legacy.path == default else "local-path"
        record = ManagedInstallation(legacy.skill, target, legacy.path, source)
        key = str(record.path)
        if key in records and records[key] != record:
            msg = f"Conflicting legacy installation records for {record.path}"
            raise SkillSyncError(msg)
        records[key] = record
    return records
