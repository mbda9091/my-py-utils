"""Project detection and local worktree environment hooks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn, cast

PROJECT_TYPES = ("auto", "android", "ios", "default")
DYNAMIC_VALUES = ("false", "true", "keep")
SAFE_EXTERNAL_ROOTS = (Path.home() / "code", Path.home() / "Code")


class HookError(RuntimeError):
    """Raised when a project-specific worktree hook cannot complete."""


def raise_hook_error(message: str, *, cause: BaseException | None = None) -> NoReturn:
    if cause is None:
        raise HookError(message)
    raise HookError(message) from cause


@dataclass(frozen=True)
class HookOptions:
    """Options shared by create and cleanup hook dispatch."""

    project_type: str = "auto"
    kkr_target: str | None = None
    dynamic: str = "false"
    clean_project_garbage: bool = False
    allow_external_project_path: bool = False

    def __post_init__(self) -> None:
        if self.project_type not in PROJECT_TYPES:
            raise_hook_error(f"unsupported project type: {self.project_type}")
        if self.dynamic not in DYNAMIC_VALUES:
            raise_hook_error(f"unsupported dynamic value: {self.dynamic}")


def _new_messages() -> list[str]:
    return []


@dataclass
class HookResult:
    """Structured hook output that can be included in JSON CLI responses."""

    project: str
    messages: list[str] = field(default_factory=_new_messages)


def detect_project(path: Path) -> str:
    if (path / "kconfig.json").exists():
        return "android"
    if (path / "Podfile").exists() or any(path.glob("*.xcworkspace")):
        return "ios"
    return "default"


def resolve_project(path: Path, requested: str) -> str:
    if requested == "auto":
        return detect_project(path)
    if requested not in PROJECT_TYPES:
        raise_hook_error(f"unsupported project type: {requested}")
    return requested


def _run_command(
    command: list[str],
    cwd: Path,
    *,
    check: bool = True,
) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - commands come from fixed hook dispatch
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        if check:
            raise_hook_error(f"command not found: {command[0]}", cause=exc)
        return ""
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    if check and result.returncode != 0:
        raise_hook_error(output or f"command failed: {' '.join(command)}")
    return output


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise_hook_error(f"cannot read JSON configuration: {path}: {exc}", cause=exc)
    if not isinstance(data, dict):
        raise_hook_error(f"expected JSON object in {path}")
    return cast("dict[str, Any]", data)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    except OSError as exc:
        raise_hook_error(f"cannot write JSON configuration: {path}: {exc}", cause=exc)


def _post_android(
    source_repo: Path,
    worktree: Path,
    options: HookOptions,
) -> HookResult:
    messages = ["configure local kkr environment"]
    source_asx = source_repo / "asx"
    target_asx = worktree / "asx"
    if not source_asx.is_dir() or not (source_asx / "sourceProjects.sh").is_file():
        raise_hook_error(
            "source asx/sourceProjects.sh not found; run `kkl ason` in the source repo "
            "before creating Android worktrees"
        )
    if os.path.lexists(target_asx):
        if target_asx.is_symlink() and target_asx.resolve(strict=False) == source_asx.resolve():
            messages.append("asx already links to source")
        else:
            if target_asx.is_symlink() or target_asx.is_file():
                target_asx.unlink()
            elif target_asx.is_dir():
                shutil.rmtree(target_asx)
            target_asx.symlink_to(source_asx, target_is_directory=True)
            messages.append(f"linked asx -> {source_asx}")
    else:
        target_asx.symlink_to(source_asx, target_is_directory=True)
        messages.append(f"linked asx -> {source_asx}")

    source_kconfig = source_repo / "kconfig.json"
    if not source_kconfig.exists():
        messages.append("source kconfig.json not found; skipped kkr config")
        return HookResult(project="android", messages=messages)
    config = _read_json(source_kconfig)
    config["email"] = _run_command(
        ["git", "config", "--get", "user.email"], source_repo, check=False
    )
    config["last_update"] = int(time.time())
    remote_value = config.setdefault("remote", {})
    if not isinstance(remote_value, dict):
        raise_hook_error(f"kconfig.json remote section is not an object: {source_kconfig}")
    remote = cast("dict[str, Any]", remote_value)
    remote["server"] = str(remote.get("server", ""))
    remote["target"] = options.kkr_target or f"~/code/{worktree.name}"
    if options.dynamic != "keep":
        remote["dynamic_branch_workspace"] = options.dynamic == "true"
    _write_json(worktree / "kconfig.json", config)
    messages.append(f"kconfig.json target: {remote['target']}")
    messages.append(f"dynamic_branch_workspace: {remote.get('dynamic_branch_workspace')}")
    return HookResult(project="android", messages=messages)


def _mark_skip_worktree(worktree: Path, relative_path: str) -> None:
    tracked = _run_command(["git", "ls-files", "--", relative_path], worktree)
    tracked_files = [line for line in tracked.splitlines() if line]
    if tracked_files:
        _run_command(["git", "update-index", "--skip-worktree", "--", *tracked_files], worktree)


def _post_ios(source_repo: Path, worktree: Path) -> HookResult:
    messages = ["configure local iOS environment"]
    source = source_repo / "PodfileConfig"
    target = worktree / "PodfileConfig"
    if not source.exists():
        messages.append("source PodfileConfig not found; skipped copy")
        return HookResult(project="ios", messages=messages)
    if target.exists() and target.is_dir():
        messages.append("target PodfileConfig is a directory; skipped copy")
        return HookResult(project="ios", messages=messages)
    _mark_skip_worktree(worktree, "PodfileConfig")
    shutil.copy2(source, target)
    messages.append(f"copied PodfileConfig -> {target}")
    return HookResult(project="ios", messages=messages)


def run_post_create_hook(
    source_repo: Path,
    worktree: Path,
    options: HookOptions | None = None,
) -> HookResult:
    options = options or HookOptions()
    project = resolve_project(source_repo, options.project_type)
    if project == "android":
        return _post_android(source_repo, worktree, options)
    if project == "ios":
        return _post_ios(source_repo, worktree)
    return HookResult(project="default", messages=["no local environment setup required"])


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_external_delete_path(
    path: Path,
    worktree: Path,
    *,
    allow_external: bool,
) -> tuple[bool, str]:
    if path in {Path("/"), Path.home()}:
        return False, "refuse root or home directory"
    if path == worktree or _is_relative_to(path, worktree):
        return False, "path is inside the worktree and will be removed by git worktree remove"
    if allow_external:
        return True, "explicitly allowed external project path"
    if path.name != worktree.name:
        return False, "external path basename does not match the worktree directory name"
    if any(path == root or _is_relative_to(path, root) for root in SAFE_EXTERNAL_ROOTS):
        return True, "external path is under ~/code or ~/Code and matches the worktree name"
    return False, "external path is outside the default safe roots"


def _pre_android(  # noqa: PLR0911 - each guard reports a distinct cleanup outcome
    worktree: Path, options: HookOptions
) -> HookResult:
    messages = ["clear remote cache with kkr clear"]
    _run_command(["kkr", "clear"], worktree)
    kconfig = worktree / "kconfig.json"
    if not kconfig.exists():
        messages.append("kconfig.json not found; skipped Android external cleanup")
        return HookResult(project="android", messages=messages)
    try:
        config = _read_json(kconfig)
    except HookError as exc:
        messages.append(str(exc))
        return HookResult(project="android", messages=messages)
    remote_value = config.get("remote", {})
    if not isinstance(remote_value, dict):
        messages.append("kconfig.json remote section is not an object; skipped external cleanup")
        return HookResult(project="android", messages=messages)
    remote = cast("dict[str, Any]", remote_value)
    remote_target_raw = str(remote.get("target", "")).strip()
    messages.append(f"remote.target: {remote_target_raw or '<empty>'}")
    messages.append(f"remote.dynamic_branch_workspace: {remote.get('dynamic_branch_workspace')}")
    if not remote_target_raw:
        return HookResult(project="android", messages=messages)
    remote_target = Path(remote_target_raw).expanduser().resolve()
    if not remote_target.exists():
        messages.append(f"remote target does not exist: {remote_target}")
        return HookResult(project="android", messages=messages)
    if not options.clean_project_garbage:
        messages.append("kept remote target; pass --clean-project-garbage to remove it")
        return HookResult(project="android", messages=messages)
    allowed, reason = _safe_external_delete_path(
        remote_target,
        worktree,
        allow_external=options.allow_external_project_path,
    )
    if not allowed:
        messages.append(f"skipped remote target removal: {reason}: {remote_target}")
        return HookResult(project="android", messages=messages)
    shutil.rmtree(remote_target)
    messages.append(f"removed remote target ({reason}): {remote_target}")
    return HookResult(project="android", messages=messages)


def run_pre_cleanup_hook(
    worktree: Path,
    options: HookOptions | None = None,
) -> HookResult:
    options = options or HookOptions()
    project = resolve_project(worktree, options.project_type)
    if project == "android":
        return _pre_android(worktree, options)
    if project == "ios":
        return HookResult(project="ios", messages=["no project-specific cleanup hook configured"])
    if shutil.which("watchman") is None:
        return HookResult(project="default", messages=["watchman not found; skipped watch-del"])
    _run_command(["watchman", "watch-del", str(worktree)], worktree, check=False)
    return HookResult(project="default", messages=["removed watchman watch"])
