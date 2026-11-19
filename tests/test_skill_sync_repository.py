from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from skill_sync.config import config_path, init_config, installations_path
from skill_sync.errors import LegacyConfigError, SkillSyncError
from skill_sync.git_repo import run_git
from skill_sync.repository import init_repository, read_repository_config

from tests.skill_sync_helpers import make_local_repository, make_registry


def test_repository_binding_is_persistent_minimal_and_idempotent(tmp_path: Path) -> None:
    repository = make_local_repository(tmp_path)
    state = tmp_path / "state"
    git_config = (repository / ".git" / "config").read_bytes()

    config = init_repository(repository, state_home=state)

    assert config.repository_path == repository
    assert read_repository_config(state) == config
    assert (repository / "skills").is_dir()
    assert not (repository / ".git" / "index").exists()
    assert (repository / ".git" / "config").read_bytes() == git_config
    assert json.loads(config_path(state).read_text()) == {
        "schemaVersion": 2,
        "repositoryPath": str(repository),
    }
    before = config_path(state).stat().st_mtime_ns
    assert init_repository(repository, state_home=state) == config
    assert config_path(state).stat().st_mtime_ns == before
    assert not list(state.glob("*.bak"))


def test_repository_binding_normalizes_relative_paths_and_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = make_local_repository(tmp_path)
    state = tmp_path / "state"
    alias = tmp_path / "alias"
    alias.symlink_to(repository, target_is_directory=True)
    monkeypatch.chdir(tmp_path)

    config = init_repository(Path("alias"), state_home=state)

    assert config.repository_path == repository
    assert init_repository(repository, state_home=state) == config
    assert not list(state.glob("*.bak"))


def test_rebinding_replaces_single_path_and_preserves_old_state(tmp_path: Path) -> None:
    first = make_local_repository(tmp_path / "first")
    second = make_local_repository(tmp_path / "second")
    state = tmp_path / "state"
    init_repository(first, state_home=state)
    original_config = config_path(state).read_bytes()
    original_records = b'{"old-record": {"opaque": true}}\n'
    installations_path(state).write_bytes(original_records)
    (first / "skills" / "keep.txt").write_text("Keep\n")

    init_repository(second, state_home=state)

    assert read_repository_config(state).repository_path == second
    assert len(list(state.glob("config.json.*.bak"))) == 1
    assert next(state.glob("config.json.*.bak")).read_bytes() == original_config
    assert next(state.glob("installations.json.*.bak")).read_bytes() == original_records
    assert installations_path(state).read_bytes() == original_records
    assert (first / "skills" / "keep.txt").read_text() == "Keep\n"
    assert list(json.loads(config_path(state).read_text())) == ["repositoryPath", "schemaVersion"]


def test_legacy_config_requires_explicit_binding_and_is_backed_up(tmp_path: Path) -> None:
    state = tmp_path / "state"
    legacy = init_config("git@example.com:me/skills.git", branch="custom", state_home=state)
    (legacy.checkout / "skills").mkdir(parents=True)
    (legacy.checkout / "skills" / "keep.txt").write_text("Legacy content\n")
    before = config_path(state).read_bytes()
    records = b'{"codex:foo": {"path": "old-path"}}\n'
    installations_path(state).write_bytes(records)

    with pytest.raises(LegacyConfigError, match="init --repo"):
        read_repository_config(state)
    assert config_path(state).read_bytes() == before
    assert not list(state.glob("*.bak"))
    repository = make_local_repository(tmp_path)
    init_repository(repository, state_home=state)

    assert next(state.glob("config.json.*.bak")).read_bytes() == before
    assert next(state.glob("installations.json.*.bak")).read_bytes() == records
    assert installations_path(state).read_bytes() == records
    assert (legacy.checkout / "skills" / "keep.txt").read_text() == "Legacy content\n"
    assert read_repository_config(state).repository_path == repository


@pytest.mark.parametrize("value", ["{", "[]", '{"schemaVersion": 99}', '{"schemaVersion": true}'])
def test_corrupt_or_unsupported_config_is_not_overwritten(tmp_path: Path, value: str) -> None:
    state = tmp_path / "state"
    state.mkdir()
    config_path(state).write_text(value)
    repository = make_local_repository(tmp_path)

    with pytest.raises(SkillSyncError):
        init_repository(repository, state_home=state)

    assert config_path(state).read_text() == value
    assert not (repository / "skills").exists()
    assert not list(state.glob("*.bak"))


@pytest.mark.parametrize("path", ["relative/repository", "", "git@example.com:me/skills.git"])
def test_v2_config_rejects_nonabsolute_repository_path(tmp_path: Path, path: str) -> None:
    config_path(tmp_path).write_text(json.dumps({"schemaVersion": 2, "repositoryPath": path}))

    with pytest.raises(SkillSyncError, match="absolute local"):
        read_repository_config(tmp_path)


@pytest.mark.parametrize("kind", ["missing", "plain", "nested", "bare"])
def test_invalid_repository_does_not_replace_previous_binding(tmp_path: Path, kind: str) -> None:
    repository = make_local_repository(tmp_path)
    state = tmp_path / "state"
    config = init_repository(repository, state_home=state)
    candidate = tmp_path / "candidate"
    if kind == "plain":
        candidate.mkdir()
    elif kind == "nested":
        candidate = repository / "nested"
        candidate.mkdir()
    elif kind == "bare":
        run_git(["init", "--bare", str(candidate)], cwd=None)
    before = config_path(state).read_bytes()

    with pytest.raises(SkillSyncError):
        init_repository(candidate, state_home=state)

    assert config_path(state).read_bytes() == before
    assert read_repository_config(state) == config


@pytest.mark.parametrize("overlap", ["same", "inside", "ancestor", "alias"])
def test_repository_must_be_separate_from_agent_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overlap: str,
) -> None:
    home = tmp_path / "home"
    usage = home / ".agents" / "skills"
    usage.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    candidate = {
        "same": usage,
        "inside": usage / "mine",
        "ancestor": usage.parent,
        "alias": tmp_path / "alias",
    }[overlap]
    if overlap == "alias":
        candidate.symlink_to(usage, target_is_directory=True)
    state = tmp_path / "state"

    with pytest.raises(SkillSyncError, match="must be separate"):
        init_repository(candidate, state_home=state)
    assert not state.exists()


def test_repository_cannot_contain_local_state(tmp_path: Path) -> None:
    repository = make_local_repository(tmp_path)
    with pytest.raises(SkillSyncError, match="state directory"):
        init_repository(repository, state_home=repository / "state")


@pytest.mark.parametrize("kind", ["file", "symlink", "broken"])
def test_skills_directory_must_be_real(tmp_path: Path, kind: str) -> None:
    repository = make_local_repository(tmp_path)
    skills = repository / "skills"
    if kind == "file":
        skills.write_text("Not a directory")
    else:
        other = tmp_path / "other"
        if kind == "symlink":
            other.mkdir()
        skills.symlink_to(other, target_is_directory=True)

    with pytest.raises(SkillSyncError, match="real directory"):
        init_repository(repository, state_home=tmp_path / "state")


def test_worktree_is_accepted_without_modifying_git_config(tmp_path: Path) -> None:
    legacy = make_registry(tmp_path)
    worktree = tmp_path / "worktree"
    run_git(["worktree", "add", "--detach", str(worktree)], cwd=legacy.checkout)
    git_config = (legacy.checkout / ".git" / "config").read_bytes()

    config = init_repository(worktree, state_home=tmp_path / "local-state")

    assert (worktree / ".git").is_file()
    assert read_repository_config(config.state_home) == config
    assert (legacy.checkout / ".git" / "config").read_bytes() == git_config


def test_failed_config_write_keeps_previous_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = make_local_repository(tmp_path / "first")
    second = make_local_repository(tmp_path / "second")
    state = tmp_path / "state"
    init_repository(first, state_home=state)
    before = config_path(state).read_bytes()
    monkeypatch.setattr(
        "skill_sync.repository.atomic_write_json",
        Mock(side_effect=SkillSyncError("write failed")),
    )

    with pytest.raises(SkillSyncError, match="write failed"):
        init_repository(second, state_home=state)

    assert config_path(state).read_bytes() == before
    assert read_repository_config(state).repository_path == first


def test_missing_repository_is_not_silently_replaced(tmp_path: Path) -> None:
    repository = make_local_repository(tmp_path)
    state = tmp_path / "state"
    init_repository(repository, state_home=state)
    repository.rename(tmp_path / "moved-repository")

    with pytest.raises(SkillSyncError, match="does not exist"):
        read_repository_config(state)
    assert not repository.exists()


def test_binding_does_not_silently_follow_replaced_repository_path(tmp_path: Path) -> None:
    repository = make_local_repository(tmp_path)
    state = tmp_path / "state"
    init_repository(repository, state_home=state)
    repository.rename(tmp_path / "original-repository")
    other = make_local_repository(tmp_path / "other")
    repository.symlink_to(other, target_is_directory=True)
    before = config_path(state).read_bytes()

    with pytest.raises(SkillSyncError, match="another location"):
        read_repository_config(state)
    assert config_path(state).read_bytes() == before
    assert init_repository(repository, state_home=state).repository_path == other


def test_backup_failure_prevents_rebinding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = make_local_repository(tmp_path / "first")
    second = make_local_repository(tmp_path / "second")
    state = tmp_path / "state"
    init_repository(first, state_home=state)
    before = config_path(state).read_bytes()
    monkeypatch.setattr(
        "skill_sync.repository.NamedTemporaryFile",
        Mock(side_effect=OSError("backup failed")),
    )

    with pytest.raises(SkillSyncError, match="back up existing state"):
        init_repository(second, state_home=state)

    assert config_path(state).read_bytes() == before
    assert read_repository_config(state).repository_path == first
    assert not (second / "skills").exists()
