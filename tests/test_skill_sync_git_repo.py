from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

from skill_sync.config import Config, GitIdentity
from skill_sync.git_repo import apply_git_identity, sync_repository

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_apply_git_identity_writes_local_git_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config(
        repository="git@example.com:me/skills.git",
        branch="main",
        checkout=tmp_path / "repository",
        state_home=tmp_path / "state",
        git_identity=GitIdentity(name="mbda9091", email="bozieg@qq.com"),
    )
    run_git = Mock()
    monkeypatch.setattr("skill_sync.git_repo.run_git", run_git)

    apply_git_identity(config)

    assert run_git.call_args_list == [
        ((["config", "user.name", "mbda9091"],), {"cwd": config.checkout}),
        ((["config", "user.email", "bozieg@qq.com"],), {"cwd": config.checkout}),
    ]


def test_sync_repository_applies_identity_after_clone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config(
        repository="git@example.com:me/skills.git",
        branch="main",
        checkout=tmp_path / "repository",
        state_home=tmp_path / "state",
        git_identity=GitIdentity(name="mbda9091", email="bozieg@qq.com"),
    )
    run_git = Mock()
    monkeypatch.setattr("skill_sync.git_repo.run_git", run_git)
    monkeypatch.setattr("skill_sync.git_repo.current_commit", Mock(return_value="abc1234"))

    assert sync_repository(config) == "abc1234"

    assert run_git.call_args_list[:3] == [
        (
            (
                [
                    "clone",
                    "--branch",
                    "main",
                    "git@example.com:me/skills.git",
                    str(config.checkout),
                ],
            ),
            {"cwd": None},
        ),
        ((["config", "user.name", "mbda9091"],), {"cwd": config.checkout}),
        ((["config", "user.email", "bozieg@qq.com"],), {"cwd": config.checkout}),
    ]
