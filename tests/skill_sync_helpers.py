from __future__ import annotations

from typing import TYPE_CHECKING

from skill_sync.config import Config, GitIdentity
from skill_sync.git_repo import apply_git_identity, run_git

if TYPE_CHECKING:
    from pathlib import Path


def make_local_repository(root: Path) -> Path:
    repository = root / "personal-skills"
    run_git(["init", "--initial-branch=main", str(repository)], cwd=None)
    return repository


def make_registry(tmp_path: Path) -> Config:
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "state" / "repository"
    run_git(["init", "--bare", "--initial-branch=main", str(remote)], cwd=None)
    run_git(["init", "--initial-branch=main", str(checkout)], cwd=None)
    config = Config(
        repository=str(remote),
        branch="main",
        checkout=checkout,
        state_home=tmp_path / "state",
        git_identity=GitIdentity(name="Registry User", email="registry@example.com"),
    )
    apply_git_identity(config)
    run_git(["config", "commit.gpgSign", "false"], cwd=checkout)
    run_git(["config", "core.hooksPath", str(tmp_path / "no-hooks")], cwd=checkout)
    (checkout / "README.md").write_text("Skill registry\n", encoding="utf-8")
    run_git(["add", "README.md"], cwd=checkout)
    run_git(["commit", "-m", "Initialize registry"], cwd=checkout)
    run_git(["remote", "add", "origin", str(remote)], cwd=checkout)
    run_git(["push", "--set-upstream", "origin", "main"], cwd=checkout)
    return config


def write_skill(root: Path, name: str = "demo-skill") -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Demo skill.\n---\n\nBody\n",
        encoding="utf-8",
    )
    return skill
