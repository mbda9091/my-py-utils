from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
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


def commit_path(checkout: Path, relative_path: Path, message: str) -> bool:
    run_git(["add", str(relative_path)], cwd=checkout)
    if not has_staged_changes(checkout, relative_path):
        return False
    run_git(["commit", "-m", message], cwd=checkout)
    return True


def has_staged_changes(checkout: Path, relative_path: Path) -> bool:
    result = run_git_result(["diff", "--cached", "--quiet", "--", str(relative_path)], cwd=checkout)
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


def run_git(args: Sequence[str], *, cwd: Path | None) -> str:
    result = run_git_result(args, cwd=cwd)
    if result.returncode == 0:
        return result.stdout
    detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
    msg = f"Git command failed: git {' '.join(args)}: {detail}"
    raise SkillSyncError(msg)


def run_git_result(args: Sequence[str], *, cwd: Path | None) -> subprocess.CompletedProcess[str]:
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
        )
    except OSError as exc:
        msg = f"Unable to run git: {exc}"
        raise SkillSyncError(msg) from exc
