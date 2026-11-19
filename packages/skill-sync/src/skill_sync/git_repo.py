from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from stat import S_IXUSR
from typing import TYPE_CHECKING

from skill_sync.errors import SkillSyncError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from skill_sync.config import Config


@dataclass(frozen=True, slots=True)
class RepositoryStatus:
    exists: bool
    commit: str | None
    dirty: bool | None


def sync_repository(config: Config) -> str:
    checkout = config.checkout
    if not checkout.exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        run_git(["clone", "--branch", config.branch, config.repository, str(checkout)], cwd=None)
        apply_git_identity(config)
        return current_commit(checkout)

    ensure_git_repository(checkout)
    apply_git_identity(config)
    origin = remote_url(checkout)
    if origin != config.repository:
        msg = f"Registry checkout origin does not match config: {origin} != {config.repository}"
        raise SkillSyncError(msg)
    if is_dirty(checkout):
        msg = f"Registry checkout has uncommitted changes: {checkout}"
        raise SkillSyncError(msg)
    ensure_branch(checkout, config.branch)
    run_git(["pull", "--rebase", "--autostash"], cwd=checkout)
    return current_commit(checkout)


def repository_status(config: Config) -> RepositoryStatus:
    checkout = config.checkout
    if not checkout.exists():
        return RepositoryStatus(exists=False, commit=None, dirty=None)
    ensure_git_repository(checkout)
    return RepositoryStatus(exists=True, commit=current_commit(checkout), dirty=is_dirty(checkout))


def validate_registry_repository(config: Config) -> None:
    ensure_git_repository(config.checkout)
    if remote_url(config.checkout) != config.repository:
        msg = "Registry checkout origin does not match config."
        raise SkillSyncError(msg)
    if current_branch(config.checkout) != config.branch:
        msg = f"Registry checkout must be on configured branch: {config.branch}"
        raise SkillSyncError(msg)


def ensure_git_repository(path: Path) -> None:
    if not path.is_dir() or not (path / ".git").exists():
        msg = f"Registry checkout exists but is not a Git repository: {path}"
        raise SkillSyncError(msg)


def apply_git_identity(config: Config) -> None:
    if config.git_identity is None:
        return
    run_git(["config", "user.name", config.git_identity.name], cwd=config.checkout)
    run_git(["config", "user.email", config.git_identity.email], cwd=config.checkout)


def ensure_branch(checkout: Path, branch: str) -> None:
    current = current_branch(checkout)
    if current == branch:
        return
    run_git(["checkout", branch], cwd=checkout)


def remote_url(checkout: Path) -> str:
    return run_git(["remote", "get-url", "origin"], cwd=checkout).strip()


def current_branch(checkout: Path) -> str:
    return run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=checkout).strip()


def current_commit(checkout: Path) -> str:
    return run_git(["rev-parse", "--short", "HEAD"], cwd=checkout).strip()


def is_dirty(checkout: Path) -> bool:
    return bool(run_git(["status", "--porcelain"], cwd=checkout).strip())


def commit_path(
    checkout: Path,
    relative_path: Path,
    message: str,
    *,
    files: Sequence[Path] | None = None,
) -> bool:
    paths = [str(path) for path in files] if files is not None else [str(relative_path)]
    if files is None:
        run_git(["add", "--", *paths], cwd=checkout)
    else:
        _stage_files(checkout, files)
    if not has_staged_changes(checkout, relative_path, files=files):
        return False
    run_git(["commit", "--only", "-m", message, "--", *paths], cwd=checkout)
    return True


def _stage_files(checkout: Path, files: Sequence[Path]) -> None:
    indexed = set(run_git(["ls-files", "-z"], cwd=checkout).split("\0"))
    available = {
        path for path in files if (checkout / path).exists() or (checkout / path).is_symlink()
    }
    stage = [str(path) for path in files if path in available or str(path) in indexed]
    if stage:
        run_git(["add", "--", *stage], cwd=checkout)
    indexed = set(run_git(["ls-files", "-z"], cwd=checkout).split("\0"))
    # Git add can silently skip new files beneath an embedded .git directory.
    # Register those files explicitly so the skill is tracked as files, not a gitlink.
    for path in sorted(available):
        if str(path) in indexed:
            continue
        absolute = checkout / path
        if absolute.is_symlink():
            mode = "120000"
            blob = run_git(
                ["hash-object", "-w", "--stdin"],
                cwd=checkout,
                input_text=str(absolute.readlink()),
            ).strip()
        else:
            mode = "100755" if absolute.stat().st_mode & S_IXUSR else "100644"
            blob = run_git(["hash-object", "-w", "--", str(path)], cwd=checkout).strip()
        run_git(["update-index", "--add", "--cacheinfo", mode, blob, str(path)], cwd=checkout)


def has_staged_changes(
    checkout: Path,
    relative_path: Path,
    *,
    files: Sequence[Path] | None = None,
) -> bool:
    paths = [str(path) for path in files] if files is not None else [str(relative_path)]
    result = run_git_result(
        ["diff", "--cached", "--quiet", "--", *paths],
        cwd=checkout,
    )
    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True
    stderr = result.stderr.strip() or result.stdout.strip()
    msg = f"Git diff failed: {stderr}"
    raise SkillSyncError(msg)


def push_with_retry(checkout: Path, branch: str) -> None:
    try:
        run_git(["push", "origin", branch], cwd=checkout)
    except SkillSyncError:
        run_git(["pull", "--rebase", "--autostash"], cwd=checkout)
        try:
            run_git(["push", "origin", branch], cwd=checkout)
        except SkillSyncError as exc:
            msg = f"Git push failed; local commit was kept in {checkout}: {exc}"
            raise SkillSyncError(msg) from exc


def run_git(args: Sequence[str], *, cwd: Path | None, input_text: str | None = None) -> str:
    result = run_git_result(args, cwd=cwd, input_text=input_text)
    if result.returncode == 0:
        return result.stdout
    detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
    msg = f"Git command failed: git {' '.join(args)}: {detail}"
    raise SkillSyncError(msg)


def run_git_result(
    args: Sequence[str],
    *,
    cwd: Path | None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("git")
    if executable is None:
        msg = "git command not found."
        raise SkillSyncError(msg)

    try:
        return subprocess.run(  # noqa: S603
            [executable, *args],
            cwd=None if cwd is None else str(cwd),
            check=False,
            capture_output=True,
            text=True,
            input=input_text,
        )
    except OSError as exc:
        msg = f"Unable to run git: {exc}"
        raise SkillSyncError(msg) from exc
