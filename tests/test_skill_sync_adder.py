from __future__ import annotations

import shutil
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from skill_sync.adder import add_skill
from skill_sync.catalog import list_local_skills
from skill_sync.errors import SkillSyncError
from skill_sync.git_repo import run_git
from skill_sync.installations import (
    ManagedInstallation,
    read_managed_installations,
    write_managed_installations,
)

from tests.skill_sync_helpers import make_personal_config, write_skill

if TYPE_CHECKING:
    from pathlib import Path


def test_add_moves_complete_skill_links_original_and_does_not_commit(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    original = write_skill(tmp_path / "agents" / "skills")
    (original / ".venv").mkdir()
    (original / ".venv" / "keep.txt").write_text("Local data\n", encoding="utf-8")
    (original / "reference.md").write_text("Reference\n", encoding="utf-8")
    (original / "reference-link.md").symlink_to("reference.md")
    before = (config.repository_path / ".git" / "HEAD").read_bytes()

    result = add_skill(original, config)

    destination = config.repository_path / "skills" / "demo-skill"
    assert result.changed
    assert result.path == original
    assert result.registry_path == destination
    assert original.is_symlink()
    assert original.resolve() == destination
    assert not destination.is_symlink()
    assert (destination / ".venv" / "keep.txt").read_text(encoding="utf-8") == "Local data\n"
    assert (destination / "reference-link.md").readlink().as_posix() == "reference.md"
    assert [skill.name for skill in list_local_skills(config.repository_path)] == ["demo-skill"]
    assert read_managed_installations(config.state_home)[str(original)].path == original
    assert (config.repository_path / ".git" / "HEAD").read_bytes() == before
    assert not (config.repository_path / ".git" / "index").exists()
    assert "skills/demo-skill/" in run_git(
        ["status", "--porcelain", "--untracked-files=all"],
        cwd=config.repository_path,
    )
    (original / "reference.md").write_text("Edited\n", encoding="utf-8")
    assert (destination / "reference.md").read_text(encoding="utf-8") == "Edited\n"
    assert not add_skill(original, config).changed


def test_add_rejects_registry_name_conflict_without_changing_source(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    original = write_skill(tmp_path / "local")
    existing = write_skill(config.repository_path / "skills")
    (existing / "keep.txt").write_text("Keep\n", encoding="utf-8")

    with pytest.raises(SkillSyncError, match="already exists"):
        add_skill(original, config)

    assert not original.is_symlink()
    assert (original / "SKILL.md").is_file()
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "Keep\n"


def test_add_rejects_invalid_skill_before_moving(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    original = tmp_path / "local" / "demo-skill"
    original.mkdir(parents=True)

    with pytest.raises(SkillSyncError, match="Missing SKILL"):
        add_skill(original, config)

    assert original.is_dir()
    assert not original.is_symlink()
    assert not (config.repository_path / "skills" / "demo-skill").exists()


def test_add_rejects_source_inside_registry(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    original = write_skill(config.repository_path / "skills")
    assert not add_skill(original, config).changed
    assert (original / "SKILL.md").is_file()


def test_add_rejects_other_symlink_without_moving_its_target(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
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
    config = make_personal_config(tmp_path)
    original = write_skill(tmp_path / "local")
    (original / "skill-sync.json").write_text("Original metadata\n", encoding="utf-8")
    targets = {
        "copy": "skill_sync.adder.shutil.copytree",
        "link": "pathlib.Path.symlink_to",
        "record": "skill_sync.adder.write_managed_installations",
    }
    monkeypatch.setattr(targets[failure], Mock(side_effect=OSError("operation failed")))

    with pytest.raises(SkillSyncError, match="operation failed"):
        add_skill(original, config)

    assert not original.is_symlink()
    assert (original / "SKILL.md").is_file()
    assert (original / "skill-sync.json").read_text(encoding="utf-8") == "Original metadata\n"
    assert not (config.repository_path / "skills" / "demo-skill").exists()
    assert not read_managed_installations(config.state_home)


def test_add_needs_no_remote_identity_or_head(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    original = write_skill(tmp_path / "local")
    assert not run_git(["remote"], cwd=config.repository_path)
    assert add_skill(original, config).changed
    assert original.is_symlink()
    assert not (config.repository_path / ".git" / "index").exists()


def test_add_detects_edits_during_copy_and_preserves_latest_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_personal_config(tmp_path)
    original = write_skill(tmp_path / "local")
    reference = original / "reference.txt"
    reference.write_text("Before")
    copytree = shutil.copytree

    def edit_after_copy(source: Path, destination: Path, *, symlinks: bool) -> str:
        result = copytree(source, destination, symlinks=symlinks)
        reference.write_text("Latest edit")
        return str(result)

    monkeypatch.setattr("skill_sync.adder.shutil.copytree", edit_after_copy)
    with pytest.raises(SkillSyncError, match="changed during migration"):
        add_skill(original, config)
    assert not original.is_symlink()
    assert reference.read_text() == "Latest edit"
    assert not (config.repository_path / "skills/demo-skill").exists()
    assert not read_managed_installations(config.state_home)


def test_add_detects_edits_after_linking_and_rolls_back_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_personal_config(tmp_path)
    original = write_skill(tmp_path / "local")
    (original / "reference.txt").write_text("Before")
    writer = write_managed_installations

    def write_then_edit(records: dict[str, ManagedInstallation], state_home: Path) -> None:
        writer(records, state_home)
        backup = next(original.parent.glob(".skill-sync-backup-*/original"))
        (backup / "reference.txt").write_text("Latest edit")

    monkeypatch.setattr("skill_sync.adder.write_managed_installations", write_then_edit)
    with pytest.raises(SkillSyncError, match="changed during migration"):
        add_skill(original, config)
    assert not original.is_symlink()
    assert (original / "reference.txt").read_text() == "Latest edit"
    assert not read_managed_installations(config.state_home)
    assert not (config.repository_path / "skills/demo-skill").exists()


@pytest.mark.parametrize("kind", ["directory", "file", "nested"])
def test_add_rejects_nested_git_metadata_without_moving_source(tmp_path: Path, kind: str) -> None:
    config = make_personal_config(tmp_path)
    original = write_skill(tmp_path / "local")
    metadata = original / ".git"
    if kind == "nested":
        metadata = original / "nested" / ".git"
        metadata.parent.mkdir()
    if kind == "file":
        metadata.write_text("gitdir: elsewhere")
    else:
        metadata.mkdir()
    with pytest.raises(SkillSyncError, match="Nested Git"):
        add_skill(original, config)
    assert not original.is_symlink()
    assert metadata.exists()
    assert not (config.repository_path / "skills/demo-skill").exists()


def test_add_rejects_repository_ancestor_without_copying_it(tmp_path: Path) -> None:
    container = tmp_path / "container"
    config = make_personal_config(container)
    (container / "SKILL.md").write_text("---\nname: container\ndescription: Demo\n---\n")
    with pytest.raises(SkillSyncError, match="outside the registry"):
        add_skill(container, config)
    assert (container / "SKILL.md").is_file()
    assert config.repository_path.is_dir()


def test_add_rejects_external_descriptor_link(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    original = write_skill(tmp_path / "local")
    descriptor = original / "SKILL.md"
    external = tmp_path / "descriptor.md"
    descriptor.rename(external)
    descriptor.symlink_to(external)
    with pytest.raises(SkillSyncError, match="real file"):
        add_skill(original, config)
    assert not original.is_symlink()
    assert descriptor.is_symlink()
    assert external.is_file()
