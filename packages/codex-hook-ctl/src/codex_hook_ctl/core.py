# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
# ruff: noqa: C901,EM101,EM102,FBT001,FBT002,PLR0912,S105,S607,TRY003

from __future__ import annotations

import copy
import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codex_hook_ctl.errors import HookCtlError

_HOOK_FILE_TOKEN = "{hook_file}"


@dataclass(frozen=True)
class SourceLayer:
    """A Codex configuration layer and the directory containing its hooks."""

    config_root: Path
    code_root: Path
    source_marker: str

    @property
    def config_path(self) -> Path:
        return self.config_root / "hooks.json"

    @property
    def source_hooks_root(self) -> Path:
        return self.config_root / "hooks"


@dataclass
class HookRecord:
    name: str
    executable: str
    source: str
    registrations: list[dict[str, Any]]
    source_file: Path

    def as_json(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "name": self.name,
            "executable": self.executable,
            "source": self.source,
            "registrations": self.registrations,
        }


def resolve_source(directory: Path | None) -> SourceLayer:
    if directory is None:
        config_root = Path.home() / ".codex"
        return SourceLayer(config_root, config_root, "global")

    project_root = directory.expanduser().resolve()
    config_root = project_root / ".codex"
    return SourceLayer(config_root, project_root, _git_origin(project_root) or project_root.name)


def record_hooks(
    repository_root: Path,
    directory: Path | None,
    force: bool = False,
) -> list[HookRecord]:
    """Scan one Codex layer and write one record per custom command hook."""
    layer = resolve_source(directory)
    config = _read_config(layer.config_path)
    records = _scan_records(config, layer)
    record_root = repository_root / "codex-hooks"

    conflicts = [record.name for record in records if (record_root / record.name).exists()]
    if conflicts and not force:
        names = ", ".join(conflicts)
        raise HookCtlError(
            f"Hook record already exists: {names}. Pass -f to overwrite it.",
        )

    for record in records:
        _write_record(record_root, record, force=force)
    return records


def inject_hook(
    repository_root: Path,
    directory: Path | None,
    name: str,
    force: bool = False,
) -> tuple[Path, Path]:
    """Copy one record into a target layer and merge its registrations."""
    record = _read_record(repository_root, name)
    layer = resolve_source(directory)
    config = _read_config(layer.config_path, missing_ok=True)
    target_file = layer.code_root / "codex-hooks" / record.executable
    existing_hook = target_file.exists() or _config_references_file(config, target_file, layer)
    if existing_hook and not force:
        raise HookCtlError(
            f"Target hook already exists: {name}. Pass -f to overwrite it.",
        )

    if force:
        _remove_config_references(config, target_file, layer)

    target_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(record.source_file, target_file)
    _merge_registrations(config, record.registrations, target_file)
    _write_config(layer.config_path, config)
    return target_file, layer.config_path


def _scan_records(config: dict[str, Any], layer: SourceLayer) -> list[HookRecord]:
    hooks = config.get("hooks")
    if hooks is None:
        return []
    if not isinstance(hooks, dict):
        raise HookCtlError(f"Invalid hooks config: {layer.config_path}")

    by_name: dict[str, HookRecord] = {}
    for event, groups in hooks.items():
        if not isinstance(event, str) or not isinstance(groups, list):
            raise HookCtlError(f"Invalid hooks config: {layer.config_path}")
        for group in groups:
            if not isinstance(group, dict):
                raise HookCtlError(f"Invalid hooks config: {layer.config_path}")
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                raise HookCtlError(f"Invalid hooks config: {layer.config_path}")
            for handler in handlers:
                if not isinstance(handler, dict) or handler.get("type") != "command":
                    continue
                command = handler.get("command")
                if not isinstance(command, str):
                    continue
                command_parts = _command_parts(command)
                source_file, command_index = _find_source_file(command_parts, layer)
                if source_file is None or command_index is None:
                    continue

                name = source_file.stem
                registration: dict[str, Any] = {
                    "event": event,
                    "command": [
                        _HOOK_FILE_TOKEN if index == command_index else part
                        for index, part in enumerate(command_parts)
                    ],
                    "handler": {
                        key: copy.deepcopy(value)
                        for key, value in handler.items()
                        if key != "command"
                    },
                }
                if "matcher" in group:
                    registration["matcher"] = copy.deepcopy(group["matcher"])

                current = by_name.get(name)
                if current is None:
                    current = HookRecord(
                        name=name,
                        executable=source_file.name,
                        source=layer.source_marker,
                        registrations=[],
                        source_file=source_file,
                    )
                    by_name[name] = current
                elif current.source_file != source_file:
                    message = (
                        f"Multiple executable files resolve to hook name '{name}' in "
                        f"{layer.config_path}."
                    )
                    raise HookCtlError(
                        message,
                    )
                current.registrations.append(registration)

    return list(by_name.values())


def _find_source_file(
    command_parts: list[str],
    layer: SourceLayer,
) -> tuple[Path | None, int | None]:
    source_root = layer.source_hooks_root.resolve()
    for index, part in enumerate(command_parts):
        if not part or part == _HOOK_FILE_TOKEN:
            continue
        raw = Path(part).expanduser()
        candidates = [raw] if raw.is_absolute() else [layer.config_root / raw, source_root / raw]
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved == source_root or not resolved.is_relative_to(source_root):
                continue
            if resolved.is_file():
                return resolved, index
    return None, None


def _command_parts(command: str) -> list[str]:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        raise HookCtlError(f"Invalid command in hooks config: {command}") from exc
    if not parts:
        raise HookCtlError("Invalid empty command in hooks config.")
    return parts


def _read_record(repository_root: Path, name: str) -> HookRecord:
    if not name or Path(name).name != name or name in {".", ".."}:
        raise HookCtlError(f"Invalid hook name: {name}")
    record_root = repository_root / "codex-hooks" / name
    metadata_path = record_root / "hook.json"
    if not metadata_path.is_file():
        raise HookCtlError(f"Hook record not found: {name}")
    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HookCtlError(f"Invalid hook record: {metadata_path}") from exc
    if not isinstance(raw, dict):
        raise HookCtlError(f"Invalid hook record: {metadata_path}")
    executable = raw.get("executable")
    source = raw.get("source")
    registrations = raw.get("registrations")
    if (
        raw.get("schemaVersion") != 1
        or raw.get("name") != name
        or not isinstance(executable, str)
        or Path(executable).name != executable
        or not isinstance(source, str)
        or not isinstance(registrations, list)
    ):
        raise HookCtlError(f"Invalid hook record: {metadata_path}")
    source_file = record_root / executable
    if not source_file.is_file():
        raise HookCtlError(f"Hook executable not found in record: {source_file}")
    return HookRecord(name, executable, source, registrations, source_file)


def _read_config(path: Path, *, missing_ok: bool = False) -> dict[str, Any]:
    if not path.exists():
        if missing_ok:
            return {"hooks": {}}
        raise HookCtlError(f"Codex hooks config not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HookCtlError(f"Invalid Codex hooks config: {path}") from exc
    if not isinstance(raw, dict):
        raise HookCtlError(f"Invalid Codex hooks config: {path}")
    if "hooks" in raw and not isinstance(raw["hooks"], dict):
        raise HookCtlError(f"Invalid Codex hooks config: {path}")
    raw.setdefault("hooks", {})
    return raw


def _write_record(record_root: Path, record: HookRecord, *, force: bool) -> None:
    record_dir = record_root / record.name
    if record_dir.exists() and force:
        if record_dir.is_dir():
            shutil.rmtree(record_dir)
        else:
            record_dir.unlink()
    record_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(record.source_file, record_dir / record.executable)
    _write_json(record_dir / "hook.json", record.as_json())


def _config_references_file(config: dict[str, Any], target_file: Path, layer: SourceLayer) -> bool:
    target = target_file.resolve()
    hooks = config.get("hooks", {})
    if not isinstance(hooks, dict):
        return False
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            for handler in group["hooks"]:
                if isinstance(handler, dict) and _handler_references_file(handler, target, layer):
                    return True
    return False


def _handler_references_file(handler: dict[str, Any], target: Path, layer: SourceLayer) -> bool:
    command = handler.get("command")
    if not isinstance(command, str):
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    target_root = layer.code_root.resolve()
    for part in parts:
        raw = Path(part).expanduser()
        candidates = [raw] if raw.is_absolute() else [target_root / raw, layer.config_root / raw]
        if any(candidate.resolve() == target for candidate in candidates):
            return True
    return False


def _remove_config_references(
    config: dict[str, Any],
    target_file: Path,
    layer: SourceLayer,
) -> None:
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue
        retained_groups: list[Any] = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                retained_groups.append(group)
                continue
            retained_hooks = [
                handler
                for handler in group["hooks"]
                if not isinstance(handler, dict)
                or not _handler_references_file(handler, target_file.resolve(), layer)
            ]
            if retained_hooks:
                updated = dict(group)
                updated["hooks"] = retained_hooks
                retained_groups.append(updated)
        hooks[event] = retained_groups


def _merge_registrations(
    config: dict[str, Any],
    registrations: list[dict[str, Any]],
    target_file: Path,
) -> None:
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise HookCtlError("Invalid Codex hooks config: hooks must be an object.")
    for registration in registrations:
        event = registration.get("event")
        command_parts = registration.get("command")
        handler_metadata = registration.get("handler")
        if (
            not isinstance(event, str)
            or not isinstance(command_parts, list)
            or not all(isinstance(part, str) for part in command_parts)
            or not isinstance(handler_metadata, dict)
        ):
            raise HookCtlError("Invalid hook record registration.")
        if _HOOK_FILE_TOKEN not in command_parts:
            raise HookCtlError("Invalid hook record registration: missing hook file token.")
        command = shlex.join(
            [str(target_file) if part == _HOOK_FILE_TOKEN else part for part in command_parts],
        )
        handler = copy.deepcopy(handler_metadata)
        handler["command"] = command
        matcher_present = "matcher" in registration
        matcher = registration.get("matcher")
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise HookCtlError("Invalid Codex hooks config: event registrations must be arrays.")
        matching_group: dict[str, Any] | None = None
        for group in groups:
            if not isinstance(group, dict):
                continue
            if ("matcher" in group) != matcher_present:
                continue
            if matcher_present and group.get("matcher") != matcher:
                continue
            if not isinstance(group.get("hooks"), list):
                continue
            matching_group = group
            break
        if matching_group is None:
            matching_group = {"hooks": []}
            if matcher_present:
                matching_group["matcher"] = copy.deepcopy(matcher)
            groups.append(matching_group)
        matching_group["hooks"].append(handler)


def _write_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, config)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        raise HookCtlError(f"Could not write {path}") from exc


def _git_origin(directory: Path) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603 - git executable and arguments are fixed
            ["git", "-C", str(directory), "remote", "get-url", "origin"],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return None
    origin = result.stdout.strip()
    return origin if result.returncode == 0 and origin else None
