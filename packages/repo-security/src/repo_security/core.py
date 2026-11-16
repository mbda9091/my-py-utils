from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from repo_security.errors import RepoSecurityError
from repo_security.resources import template_files
from repo_security.templates.manage import (
    InstallPlan,
    diagnose,
    execute_setup,
    make_plan,
    repository_root,
)

if TYPE_CHECKING:
    from pathlib import Path


def plan_repository(path: Path, *, github_actions: bool = False) -> InstallPlan:
    return make_plan(
        repository_root(path), template_files(github_actions=True), github_actions=github_actions
    )


def setup_repository(path: Path, *, github_actions: bool = False) -> Path:
    root = repository_root(path)
    execute_setup(root, template_files(github_actions=True), github_actions=github_actions)
    return root


def doctor_repository(path: Path) -> tuple[bool, tuple[str, ...]]:
    return diagnose(repository_root(path), template_files(github_actions=True))


def scan_repository(path: Path, mode: str) -> int:
    root = repository_root(path)
    runner = root / ".security/run"
    if not runner.is_file() or runner.is_symlink() or runner.parent.is_symlink():
        msg = "Security runtime is missing or linked; run setup first"
        raise RepoSecurityError(msg)
    result = subprocess.run([str(runner), mode], cwd=root, check=False)  # noqa: S603
    return result.returncode if result.returncode in (0, 1) else 2
