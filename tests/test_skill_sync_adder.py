from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from skill_sync.adder import add_skill
from skill_sync.catalog import list_skills
from skill_sync.config import read_installations
from skill_sync.errors import SkillSyncError
from skill_sync.git_repo import run_git

from tests.skill_sync_helpers import make_registry, write_skill

if TYPE_CHECKING:
    from pathlib import Path


def test_add_moves_complete_skill_links_original_and_does_not_commit(tmp_path: Path) -> None:
    config = make_registry(tmp_path)
    original = write_skill(tmp_path / "agents" / "skills")
    (original / ".venv").mkdir()
    (original / ".venv" / "keep.txt").write_text("Local data\n", encoding="utf-8")
    (original / "reference.md").write_text("Reference\n", encoding="utf-8")
    (original / "reference-link.md").symlink_to("reference.md")
    before = run_git(["rev-parse", "HEAD"], cwd=config.checkout)

    result = add_skill(original, config)

    destination = config.checkout / "skills" / "demo-skill"
    assert result.changed
    assert result.path == original
    assert result.registry_path == destination
    assert original.is_symlink()
    assert original.resolve() == destination
    assert not destination.is_symlink()
    assert (destination / ".venv" / "keep.txt").read_text(encoding="utf-8") == "Local data\n"
    assert (destination / "reference-link.md").readlink().as_posix() == "reference.md"
    assert [skill.name for skill in list_skills(config.checkout)] == ["demo-skill"]
    assert read_installations(config.state_home)["codex:demo-skill"].path == original
    assert run_git(["rev-parse", "HEAD"], cwd=config.checkout) == before
    assert not run_git(["diff", "--cached", "--name-only"], cwd=config.checkout)
    assert "skills/demo-skill/" in run_git(
        ["status", "--porcelain", "--untracked-files=all"],
        cwd=config.checkout,
    )
    (original / "reference.md").write_text("Edited\n", encoding="utf-8")
    assert (destination / "reference.md").read_text(encoding="utf-8") == "Edited\n"
    assert not add_skill(original, config).changed


def test_add_rejects_registry_name_conflict_without_changing_source(tmp_path: Path) -> None:
    config = make_registry(tmp_path)
    original = write_skill(tmp_path / "local")
    existing = write_skill(config.checkout / "skills")
    (existing / "keep.txt").write_text("Keep\n", encoding="utf-8")

    with pytest.raises(SkillSyncError, match="already exists"):
        add_skill(original, config)

    assert not original.is_symlink()
    assert (original / "SKILL.md").is_file()
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "Keep\n"


def test_add_rejects_invalid_skill_before_moving(tmp_path: Path) -> None:
    config = make_registry(tmp_path)
    original = tmp_path / "local" / "demo-skill"
    original.mkdir(parents=True)

    with pytest.raises(SkillSyncError, match="Missing SKILL"):
        add_skill(original, config)

    assert original.is_dir()
    assert not original.is_symlink()
    assert not (config.checkout / "skills" / "demo-skill").exists()


def test_add_rejects_source_inside_registry(tmp_path: Path) -> None:
    config = make_registry(tmp_path)
    original = write_skill(config.checkout / "skills")
    with pytest.raises(SkillSyncError, match="outside the registry"):
        add_skill(original, config)
    assert (original / "SKILL.md").is_file()


def test_add_rejects_other_symlink_without_moving_its_target(tmp_path: Path) -> None:
    config = make_registry(tmp_path)
    actual = write_skill(tmp_path / "other")
    original = tmp_path / "demo-skill"
    original.symlink_to(actual, target_is_directory=True)

    with pytest.raises(SkillSyncError, match="symlink to another location"):
        add_skill(original, config)

    assert original.resolve() == actual
    assert (actual / "SKILL.md").is_file()


@pytest.mark.parametrize("failure", ["copy", "link", "record"])
def test_add_restores_original_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    config = make_registry(tmp_path)
    original = write_skill(tmp_path / "local")
    (original / "skill-sync.json").write_text("Original metadata\n", encoding="utf-8")
    targets = {
        "copy": "skill_sync.adder.shutil.copytree",
        "link": "pathlib.Path.symlink_to",
        "record": "skill_sync.adder.write_installations",
    }
    monkeypatch.setattr(targets[failure], Mock(side_effect=OSError("operation failed")))

    with pytest.raises(SkillSyncError, match="operation failed"):
        add_skill(original, config)

    assert not original.is_symlink()
    assert (original / "SKILL.md").is_file()
    assert (original / "skill-sync.json").read_text(encoding="utf-8") == "Original metadata\n"
    assert not (config.checkout / "skills" / "demo-skill").exists()
    assert not read_installations(config.state_home)


def test_add_requires_synced_registry(tmp_path: Path) -> None:
    config = make_registry(tmp_path)
    original = write_skill(tmp_path / "local")
    run_git(["remote", "set-url", "origin", "wrong-registry"], cwd=config.checkout)

    with pytest.raises(SkillSyncError, match="origin does not match"):
        add_skill(original, config)
    assert not original.is_symlink()
