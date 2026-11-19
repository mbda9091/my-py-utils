from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from skill_sync.config import (
    atomic_write_bytes,
    atomic_write_json,
    config_path,
    get_state_home,
    installations_path,
    legacy_repository_path,
)
from skill_sync.errors import SkillSyncError


def test_state_home_defaults_and_explicit_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert get_state_home({}) == tmp_path / ".skill-sync"
    assert get_state_home({"SKILL_SYNC_HOME": "~/custom"}) == tmp_path / "custom"


def test_state_file_paths_are_local_to_selected_state_home(tmp_path: Path) -> None:
    assert config_path(tmp_path) == tmp_path / "config.json"
    assert installations_path(tmp_path) == tmp_path / "installations.json"


def test_legacy_reader_only_uses_local_checkout(tmp_path: Path) -> None:
    payload = {
        "schemaVersion": 1,
        "checkout": str(tmp_path / "old-repository"),
        "repository": "unreachable-remote",
        "branch": "unused",
        "gitIdentity": {"unused": True},
    }
    assert legacy_repository_path(payload) == tmp_path / "old-repository"
    assert not (tmp_path / "old-repository").exists()


@pytest.mark.parametrize("value", ["relative/path", "", "git@example.com:me/skills.git", None])
def test_legacy_reader_refuses_ambiguous_checkout(value: object) -> None:
    with pytest.raises(SkillSyncError, match="absolute local"):
        legacy_repository_path({"schemaVersion": 1, "checkout": value})


@pytest.mark.parametrize("version", [True, 99, None])
def test_legacy_reader_refuses_unsupported_version(tmp_path: Path, version: object) -> None:
    with pytest.raises(SkillSyncError, match="schemaVersion"):
        legacy_repository_path({"schemaVersion": version, "checkout": str(tmp_path)})


def test_atomic_state_preserves_original_bytes_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = config_path(tmp_path)
    original = b'{"original": true}\n'
    path.write_bytes(original)
    monkeypatch.setattr(Path, "replace", Mock(side_effect=OSError("replace failed")))
    with pytest.raises(SkillSyncError, match="replace failed"):
        atomic_write_json(path, {"replacement": True})
    assert path.read_bytes() == original
    assert sorted(tmp_path.iterdir()) == [path]


def test_atomic_state_restores_exact_bytes(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.json"
    original = b'{ "text": "\\u4f60\\u597d" }\n\n'
    atomic_write_bytes(path, original)
    assert path.read_bytes() == original
