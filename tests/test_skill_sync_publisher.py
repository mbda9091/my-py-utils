from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from skill_sync.adder import add_skill
from skill_sync.config import Config, GitIdentity
from skill_sync.errors import SkillSyncError
from skill_sync.git_repo import run_git, sync_repository
from skill_sync.installer import install_skill
from skill_sync.publisher import publish_skill
from skill_sync.repository import RepositoryConfig

from tests.skill_sync_helpers import make_registry, write_skill


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
        git_identity=GitIdentity(name="mbda9091", email="registry@example.com"),
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


def test_add_edit_publish_and_linked_install_with_real_git(tmp_path: Path) -> None:
    config = make_registry(tmp_path)
    original = write_skill(tmp_path / "agents" / "skills")
    (original / ".venv").mkdir()
    (original / ".venv" / "private.txt").write_text("Local environment\n", encoding="utf-8")
    (original / "__pycache__").mkdir()
    (original / "__pycache__" / "cache.pyc").write_bytes(b"compiled")
    (original / ".DS_Store").write_bytes(b"local")
    (original / ".gitignore").write_text("local-cache/\n", encoding="utf-8")
    (original / "local-cache").mkdir()
    (original / "local-cache" / "local.txt").write_text("Local cache\n", encoding="utf-8")
    (original / "nested").mkdir()
    (original / "nested" / ".venv").mkdir()
    (original / "nested" / ".venv" / "private.txt").write_text("Nested\n", encoding="utf-8")
    add_skill(original, RepositoryConfig(config.checkout, config.state_home))
    (original / "reference.md").write_text("Edited via original path\n", encoding="utf-8")
    (config.checkout / "unrelated.txt").write_text("Staged separately\n", encoding="utf-8")
    run_git(["add", "unrelated.txt"], cwd=config.checkout)

    result = publish_skill(original, config)

    assert result.changed
    assert result.pushed
    assert original.is_symlink()
    assert run_git(["show", "HEAD:skills/demo-skill/reference.md"], cwd=config.checkout) == (
        "Edited via original path\n"
    )
    committed = run_git(["ls-tree", "-r", "--name-only", "HEAD"], cwd=config.checkout)
    assert ".venv" not in committed
    assert "__pycache__" not in committed
    assert ".DS_Store" not in committed
    assert "local-cache" not in committed
    assert "unrelated.txt" not in committed
    assert "unrelated.txt" in run_git(["diff", "--cached", "--name-only"], cwd=config.checkout)
    assert (original / ".venv" / "private.txt").is_file()
    assert run_git(["log", "-1", "--format=%an <%ae>"], cwd=config.checkout).strip() == (
        "Registry User <registry@example.com>"
    )
    remote_head = run_git(["rev-parse", "refs/heads/main"], cwd=Path(config.repository))
    assert remote_head == run_git(["rev-parse", "HEAD"], cwd=config.checkout)

    second_install = install_skill(
        "demo-skill",
        RepositoryConfig(config.checkout, config.state_home),
        target="codex",
        target_root=tmp_path / "second" / "skills",
    )
    assert second_install.path.is_symlink()
    (second_install.path / "reference.md").write_text("Second edit\n", encoding="utf-8")
    assert (original / "reference.md").read_text(encoding="utf-8") == "Second edit\n"
    (original / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Updated description.\n---\nBody\n",
        encoding="utf-8",
    )
    (original / "reference.md").unlink()
    assert publish_skill(second_install.path, config).changed
    assert "reference.md" not in run_git(
        ["ls-tree", "-r", "--name-only", "HEAD"], cwd=config.checkout
    )
    assert (
        json.loads((original / "skill-sync.json").read_text())["description"]
        == "Updated description."
    )
    unchanged_head = run_git(["rev-parse", "HEAD"], cwd=config.checkout)
    assert not publish_skill(original, config).changed
    assert run_git(["rev-parse", "HEAD"], cwd=config.checkout) == unchanged_head
    # Unrelated staged work is still present, so sync continues to refuse it.
    with pytest.raises(SkillSyncError, match="uncommitted changes"):
        sync_repository(config)


def test_managed_publish_no_push_can_be_pushed_later_without_new_commit(tmp_path: Path) -> None:
    config = make_registry(tmp_path)
    original = write_skill(tmp_path / "local")
    add_skill(original, RepositoryConfig(config.checkout, config.state_home))
    remote_before = run_git(["rev-parse", "main"], cwd=Path(config.repository))
    assert publish_skill(original, config, push=False).changed
    local_head = run_git(["rev-parse", "HEAD"], cwd=config.checkout)
    assert run_git(["rev-parse", "main"], cwd=Path(config.repository)) == remote_before

    result = publish_skill(original, config)

    assert not result.changed
    assert result.pushed
    assert run_git(["rev-parse", "HEAD"], cwd=config.checkout) == local_head
    assert run_git(["rev-parse", "main"], cwd=Path(config.repository)) == local_head


def test_managed_publish_blocks_credentials_without_creating_commit(tmp_path: Path) -> None:
    config = make_registry(tmp_path)
    original = write_skill(tmp_path / "local")
    add_skill(original, RepositoryConfig(config.checkout, config.state_home))
    (original / ".env").write_text("TOKEN=private\n", encoding="utf-8")
    before = run_git(["rev-parse", "HEAD"], cwd=config.checkout)
    assert not (original / "skill-sync.json").exists()

    with pytest.raises(SkillSyncError, match="Publish blocked"):
        publish_skill(original, config)

    assert run_git(["rev-parse", "HEAD"], cwd=config.checkout) == before
    assert not (original / "skill-sync.json").exists()
    assert original.is_symlink()


def test_managed_publish_rejects_wrong_branch_without_switching(tmp_path: Path) -> None:
    config = make_registry(tmp_path)
    original = write_skill(tmp_path / "local")
    add_skill(original, RepositoryConfig(config.checkout, config.state_home))
    run_git(["checkout", "-b", "other"], cwd=config.checkout)

    with pytest.raises(SkillSyncError, match="configured branch"):
        publish_skill(original, config, push=False)

    assert run_git(["branch", "--show-current"], cwd=config.checkout).strip() == "other"


def test_managed_publish_tracks_files_inside_embedded_git_repository(tmp_path: Path) -> None:
    config = make_registry(tmp_path)
    original = write_skill(config.checkout / "skills")
    run_git(["init", "--initial-branch=main", str(original)], cwd=None)
    (original / "reference.md").symlink_to("SKILL.md")
    script = original / "run.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)

    assert publish_skill(original, config, push=False).changed

    assert (original / ".git").is_dir()
    tracked = run_git(["ls-tree", "-r", "--name-only", "HEAD"], cwd=config.checkout)
    assert "skills/demo-skill/SKILL.md" in tracked
    assert ".git" not in tracked
    entries = run_git(["ls-tree", "-r", "HEAD", "--", "skills/demo-skill"], cwd=config.checkout)
    assert "120000 blob" in entries
    assert "100755 blob" in entries
    assert not publish_skill(original, config, push=False).changed


def test_managed_publish_commits_previously_staged_deletion(tmp_path: Path) -> None:
    config = make_registry(tmp_path)
    original = write_skill(tmp_path / "local")
    (original / "reference.md").write_text("Reference\n", encoding="utf-8")
    add_skill(original, RepositoryConfig(config.checkout, config.state_home))
    publish_skill(original, config, push=False)
    run_git(["rm", "skills/demo-skill/reference.md"], cwd=config.checkout)

    assert publish_skill(original, config, push=False).changed

    assert "reference.md" not in run_git(
        ["ls-tree", "-r", "--name-only", "HEAD"],
        cwd=config.checkout,
    )
