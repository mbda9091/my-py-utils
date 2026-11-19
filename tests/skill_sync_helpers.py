from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from skill_sync.config import config_path
from skill_sync.repository import RepositoryConfig, init_repository

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def run_git(args: Sequence[str], *, cwd: Path | None) -> str:
    executable = shutil.which("git")
    if executable is None:
        message = "Git is required for skill-sync integration tests."
        raise RuntimeError(message)
    result = subprocess.run(  # noqa: S603
        [executable, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def make_local_repository(root: Path) -> Path:
    repository = root / "personal-skills"
    run_git(["init", "--initial-branch=main", str(repository)], cwd=None)
    return repository


def make_personal_config(root: Path) -> RepositoryConfig:
    return init_repository(make_local_repository(root), state_home=root / "local-state")


@dataclass(frozen=True, slots=True)
class GitRepository:
    path: Path
    remote: Path


def make_committed_repository(root: Path) -> GitRepository:
    remote = root / "remote.git"
    checkout = root / "source"
    run_git(["init", "--bare", "--initial-branch=main", str(remote)], cwd=None)
    run_git(["init", "--initial-branch=main", str(checkout)], cwd=None)
    for key, value in (
        ("user.name", "Test User"),
        ("user.email", "test@example.com"),
        ("commit.gpgSign", "false"),
        ("core.hooksPath", str(root / "no-hooks")),
    ):
        run_git(["config", key, value], cwd=checkout)
    (checkout / "README.md").write_text("Personal skill repository\n", encoding="utf-8")
    run_git(["add", "README.md"], cwd=checkout)
    run_git(["commit", "-m", "Initialize repository manually"], cwd=checkout)
    run_git(["remote", "add", "origin", str(remote)], cwd=checkout)
    run_git(["push", "--set-upstream", "origin", "main"], cwd=checkout)
    return GitRepository(checkout, remote)


def write_legacy_config(state_home: Path, *, branch: str = "main") -> Path:
    state_home.mkdir(parents=True, exist_ok=True)
    checkout = state_home / "repository"
    config_path(state_home).write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "repository": "git@example.com:me/skills.git",
                "branch": branch,
                "checkout": str(checkout),
            }
        ),
        encoding="utf-8",
    )
    return checkout


def write_skill(root: Path, name: str = "demo-skill") -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Demo skill.\n---\n\nBody\n",
        encoding="utf-8",
    )
    return skill
