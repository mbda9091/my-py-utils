from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from skill_sync.config import (
    GitIdentity,
    InstallationRecord,
    config_path,
    init_config,
    installations_path,
    read_config,
    read_installations,
    write_installations,
)
from skill_sync.errors import SkillSyncError

if TYPE_CHECKING:
    from pathlib import Path


def test_init_config_writes_config_and_is_idempotent(tmp_path: Path) -> None:
    config = init_config("git@example.com:me/skills.git", branch="main", state_home=tmp_path)

    assert config.repository == "git@example.com:me/skills.git"
    assert config.branch == "main"
    assert config.checkout == tmp_path / "repository"
    assert read_config(tmp_path) == config

    same_config = init_config("git@example.com:me/skills.git", branch="main", state_home=tmp_path)

    assert same_config == config


def test_init_config_writes_git_identity(tmp_path: Path) -> None:
    config = init_config(
        "git@example.com:me/skills.git",
        branch="main",
        git_name="mbda9091",
        git_email="bozieg@qq.com",
        state_home=tmp_path,
    )

    assert config.git_identity == GitIdentity(name="mbda9091", email="bozieg@qq.com")
    payload = json.loads(config_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["gitIdentity"] == {"name": "mbda9091", "email": "bozieg@qq.com"}
    assert read_config(tmp_path).git_identity == config.git_identity


def test_init_config_updates_identity_for_existing_same_registry(tmp_path: Path) -> None:
    init_config("git@example.com:me/skills.git", branch="main", state_home=tmp_path)

    updated = init_config(
        "git@example.com:me/skills.git",
        branch="main",
        git_name="mbda9091",
        git_email="bozieg@qq.com",
        state_home=tmp_path,
    )

    assert updated.git_identity == GitIdentity(name="mbda9091", email="bozieg@qq.com")
    assert read_config(tmp_path).git_identity == updated.git_identity


def test_init_config_rejects_partial_git_identity(tmp_path: Path) -> None:
    with pytest.raises(SkillSyncError, match="provided together"):
        init_config(
            "git@example.com:me/skills.git",
            branch="main",
            git_name="mbda9091",
            state_home=tmp_path,
        )


def test_init_config_rejects_different_existing_config(tmp_path: Path) -> None:
    init_config("git@example.com:me/skills.git", branch="main", state_home=tmp_path)

    with pytest.raises(SkillSyncError, match="already exists"):
        init_config("git@example.com:me/other.git", branch="main", state_home=tmp_path)


def test_read_config_rejects_invalid_json(tmp_path: Path) -> None:
    config_path(tmp_path).write_text("{", encoding="utf-8")

    with pytest.raises(SkillSyncError, match="not valid JSON"):
        read_config(tmp_path)


def test_read_config_accepts_legacy_config_without_git_identity(tmp_path: Path) -> None:
    config_path(tmp_path).write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "repository": "git@example.com:me/skills.git",
                "branch": "main",
                "checkout": str(tmp_path / "repository"),
            },
        ),
        encoding="utf-8",
    )

    assert read_config(tmp_path).git_identity is None


def test_write_installations_round_trips_records(tmp_path: Path) -> None:
    records = {
        "codex:demo-skill": InstallationRecord(
            skill="demo-skill",
            target="codex",
            commit="abc1234",
            path=tmp_path / "skills/demo-skill",
        ),
    }

    write_installations(records, tmp_path)

    assert read_installations(tmp_path) == records
    payload = json.loads(installations_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["codex:demo-skill"]["path"] == str(tmp_path / "skills/demo-skill")


def test_read_installations_returns_empty_when_file_missing(tmp_path: Path) -> None:
    assert read_installations(tmp_path) == {}
