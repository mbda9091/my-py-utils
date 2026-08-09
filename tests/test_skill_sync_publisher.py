from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from skill_sync.config import Config, GitIdentity
from skill_sync.errors import SkillSyncError
from skill_sync.publisher import publish_skill


def write_local_skill(
    root: Path,
    name: str = "demo-skill",
    description: str = "Demo skill.",
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nBody\n",
        encoding="utf-8",
    )
    return skill_dir


def make_config(tmp_path: Path) -> Config:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    return Config(
        repository="git@example.com:me/skills.git",
        branch="main",
        checkout=checkout,
        state_home=tmp_path / "state",
        git_identity=GitIdentity(name="mbda9091", email="bozieg@qq.com"),
    )


def test_publish_writes_skill_and_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_skill = write_local_skill(tmp_path / "local")
    (local_skill / "__pycache__").mkdir()
    (local_skill / "__pycache__" / "ignored.pyc").write_bytes(b"compiled")
    config = make_config(tmp_path)
    sync_mock = Mock(return_value="abc1234")
    commit_mock = Mock(return_value=True)
    push_mock = Mock()
    monkeypatch.setattr("skill_sync.publisher.sync_repository", sync_mock)
    monkeypatch.setattr("skill_sync.publisher.commit_path", commit_mock)
    monkeypatch.setattr("skill_sync.publisher.push_with_retry", push_mock)

    result = publish_skill(
        local_skill,
        config,
        clock=lambda: datetime(2026, 8, 9, tzinfo=UTC),
    )

    published = config.checkout / "skills" / "demo-skill"
    metadata = json.loads((published / "skill-sync.json").read_text(encoding="utf-8"))
    assert result.changed is True
    assert result.pushed is True
    assert (published / "SKILL.md").is_file()
    assert not (published / "__pycache__").exists()
    assert metadata == {
        "schemaVersion": 1,
        "name": "demo-skill",
        "description": "Demo skill.",
        "compatibility": ["codex"],
        "updatedAt": "2026-08-09T00:00:00Z",
    }
    commit_mock.assert_called_once_with(
        config.checkout,
        Path("skills") / "demo-skill",
        "Publish skill: demo-skill",
    )
    push_mock.assert_called_once_with(config.checkout, "main")


def test_publish_rejects_existing_skill_without_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_skill = write_local_skill(tmp_path / "local")
    config = make_config(tmp_path)
    (config.checkout / "skills" / "demo-skill").mkdir(parents=True)
    monkeypatch.setattr("skill_sync.publisher.sync_repository", Mock(return_value="abc1234"))

    with pytest.raises(SkillSyncError, match="Pass --update"):
        publish_skill(local_skill, config)


def test_publish_no_push_leaves_commit_unpushed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_skill = write_local_skill(tmp_path / "local")
    config = make_config(tmp_path)
    monkeypatch.setattr("skill_sync.publisher.sync_repository", Mock(return_value="abc1234"))
    monkeypatch.setattr("skill_sync.publisher.commit_path", Mock(return_value=True))
    push_mock = Mock()
    monkeypatch.setattr("skill_sync.publisher.push_with_retry", push_mock)

    result = publish_skill(local_skill, config, push=False)

    assert result.changed is True
    assert result.pushed is False
    push_mock.assert_not_called()


def test_publish_returns_unchanged_when_registry_content_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_skill = write_local_skill(tmp_path / "local")
    config = make_config(tmp_path)
    existing = config.checkout / "skills" / "demo-skill"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text((local_skill / "SKILL.md").read_text(encoding="utf-8"))
    (existing / "skill-sync.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "name": "demo-skill",
                "description": "Demo skill.",
                "compatibility": ["codex"],
                "updatedAt": "2026-08-01T00:00:00Z",
            },
        ),
        encoding="utf-8",
    )
    commit_mock = Mock(return_value=True)
    monkeypatch.setattr("skill_sync.publisher.sync_repository", Mock(return_value="abc1234"))
    monkeypatch.setattr("skill_sync.publisher.commit_path", commit_mock)

    result = publish_skill(local_skill, config, update=True)

    assert result.changed is False
    commit_mock.assert_not_called()
    metadata = json.loads((existing / "skill-sync.json").read_text(encoding="utf-8"))
    assert metadata["updatedAt"] == "2026-08-01T00:00:00Z"


def test_publish_blocks_high_risk_content_before_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_skill = write_local_skill(tmp_path / "local")
    (local_skill / ".npmrc").write_text("//registry.example/:_authToken=value\n", encoding="utf-8")
    config = make_config(tmp_path)
    sync_mock = Mock(return_value="abc1234")
    monkeypatch.setattr("skill_sync.publisher.sync_repository", sync_mock)

    with pytest.raises(SkillSyncError, match="Publish blocked"):
        publish_skill(local_skill, config)
    sync_mock.assert_not_called()


def test_publish_requires_configured_git_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_skill = write_local_skill(tmp_path / "local")
    checkout = tmp_path / "repository"
    checkout.mkdir()
    config = Config(
        repository="git@example.com:me/skills.git",
        branch="main",
        checkout=checkout,
        state_home=tmp_path / "state",
    )
    sync_mock = Mock(return_value="abc1234")
    monkeypatch.setattr("skill_sync.publisher.sync_repository", sync_mock)

    with pytest.raises(SkillSyncError, match="Git identity is not configured"):
        publish_skill(local_skill, config)
    sync_mock.assert_not_called()
