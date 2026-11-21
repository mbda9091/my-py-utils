from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest
from skill_sync.adder import add_skill
from skill_sync.cli import main
from skill_sync.config import installations_path
from skill_sync.errors import SkillSyncError
from skill_sync.installations import (
    ManagedInstallation,
    read_managed_installations,
    write_managed_installations,
)
from skill_sync.installer import install_skill

from tests.skill_sync_helpers import make_personal_config, run_git, write_skill


def test_force_add_replaces_complete_skill_and_updates_existing_use_paths(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    destination = write_skill(config.repository_path / "skills")
    (destination / "old-only.txt").write_text("Old only")
    (destination / "reference.txt").write_text("Old reference")
    installed = install_skill(
        "demo-skill", config, target="codex", target_root=tmp_path / "installed"
    ).path
    installed_inode = installed.lstat().st_ino
    run_git(["add", "skills"], cwd=config.repository_path)
    run_git(
        [
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgSign=false",
            "-c",
            f"core.hooksPath={tmp_path / 'no-hooks'}",
            "commit",
            "-m",
            "Record the old skill manually",
        ],
        cwd=config.repository_path,
    )
    git_dir = config.repository_path / ".git"
    git_before = {
        str(p.relative_to(git_dir)): p.read_bytes() for p in git_dir.rglob("*") if p.is_file()
    }
    source = write_skill(tmp_path / "incoming")
    (source / "reference.txt").write_text("New reference")
    (source / "new-only.txt").write_text("New only")
    (source / "reference-link").symlink_to("reference.txt")

    result = add_skill(source, config, force=True)

    assert result.changed
    assert result.cleanup_path is None
    assert result.repository_path == destination
    assert source.is_symlink()
    assert source.readlink() == destination
    assert not destination.is_symlink()
    assert not (destination / "old-only.txt").exists()
    assert (destination / "new-only.txt").read_text() == "New only"
    assert (destination / "reference-link").read_text() == "New reference"
    assert (installed / "reference.txt").read_text() == "New reference"
    assert installed.lstat().st_ino == installed_inode
    records = read_managed_installations(config.state_home)
    assert set(records) == {str(installed), str(source)}
    assert all(record.source_path == destination for record in records.values())
    assert not list(destination.parent.glob(".skill-sync-replaced-*"))
    assert {
        str(p.relative_to(git_dir)): p.read_bytes() for p in git_dir.rglob("*") if p.is_file()
    } == git_before
    assert "D\tskills/demo-skill/old-only.txt" in run_git(
        ["diff", "--name-status"], cwd=config.repository_path
    )


@pytest.mark.parametrize("repeat_path", ["source", "repository"])
def test_force_add_of_already_managed_skill_is_idempotent(tmp_path: Path, repeat_path: str) -> None:
    config = make_personal_config(tmp_path)
    source = write_skill(tmp_path / "incoming")
    first = add_skill(source, config, force=True)
    inode = first.repository_path.stat().st_ino

    result = add_skill(
        source if repeat_path == "source" else first.repository_path, config, force=True
    )

    assert not result.changed
    assert first.repository_path.stat().st_ino == inode
    assert source.readlink() == first.repository_path
    assert len(read_managed_installations(config.state_home)) == 1


@pytest.mark.parametrize("failure", ["copy", "publish", "link", "record", "after-record"])
def test_force_add_restores_source_repository_and_records_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    config = make_personal_config(tmp_path)
    destination = write_skill(config.repository_path / "skills")
    (destination / "old-only.txt").write_text("Old data")
    install_skill("demo-skill", config, target="codex", target_root=tmp_path / "installed")
    old_records = installations_path(config.state_home).read_bytes()
    old_inode = destination.stat().st_ino
    source = write_skill(tmp_path / "incoming")
    (source / "new-only.txt").write_text("New data")
    real_rename = Path.rename

    def fail_publish(path: Path, target: Path) -> Path:
        if target == destination and path.parent.name.startswith(".skill-sync-add-"):
            msg = "publish failed"
            raise OSError(msg)
        return real_rename(path, target)

    def write_then_edit(records: dict[str, ManagedInstallation], state_home: Path) -> None:
        write_managed_installations(records, state_home)
        backup = next(source.parent.glob(".skill-sync-backup-*/original"))
        (backup / "late-edit.txt").write_text("Latest edit")

    if failure == "copy":
        monkeypatch.setattr(
            "skill_sync.adder.shutil.copytree", Mock(side_effect=OSError("copy failed"))
        )
    elif failure == "publish":
        monkeypatch.setattr(Path, "rename", fail_publish)
    elif failure == "link":
        monkeypatch.setattr(Path, "symlink_to", Mock(side_effect=OSError("link failed")))
    elif failure == "record":
        monkeypatch.setattr(
            "skill_sync.adder.write_managed_installations",
            Mock(side_effect=OSError("record failed")),
        )
    else:
        monkeypatch.setattr("skill_sync.adder.write_managed_installations", write_then_edit)

    with pytest.raises(SkillSyncError):
        add_skill(source, config, force=True)

    assert not source.is_symlink()
    assert (source / "new-only.txt").read_text() == "New data"
    assert destination.stat().st_ino == old_inode
    assert (destination / "old-only.txt").read_text() == "Old data"
    assert not (destination / "new-only.txt").exists()
    assert installations_path(config.state_home).read_bytes() == old_records
    assert not list(destination.parent.glob(".skill-sync-replaced-*"))
    if failure == "after-record":
        assert (source / "late-edit.txt").read_text() == "Latest edit"


def test_force_add_reports_preserved_repository_backup_if_restoration_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_personal_config(tmp_path)
    destination = write_skill(config.repository_path / "skills")
    (destination / "old-only.txt").write_text("Old data")
    source = write_skill(tmp_path / "incoming")
    (source / "new-only.txt").write_text("New data")
    real_rename = Path.rename

    def fail_restore(path: Path, target: Path) -> Path:
        if path.name == "original" and path.parent.name.startswith(".skill-sync-replaced-"):
            msg = "restore failed"
            raise OSError(msg)
        return real_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_restore)
    monkeypatch.setattr(
        "skill_sync.adder.write_managed_installations", Mock(side_effect=OSError("record failed"))
    )

    with pytest.raises(SkillSyncError, match="Previous repository content kept at") as caught:
        add_skill(source, config, force=True)

    backup = next(destination.parent.glob(".skill-sync-replaced-*/original"))
    assert str(backup) in str(caught.value)
    assert (backup / "old-only.txt").read_text() == "Old data"
    assert not source.is_symlink()
    assert (source / "new-only.txt").read_text() == "New data"


@pytest.mark.parametrize("kind", ["descriptor", "nested-git", "external-reference"])
def test_force_does_not_bypass_source_validation(tmp_path: Path, kind: str) -> None:
    config = make_personal_config(tmp_path)
    destination = write_skill(config.repository_path / "skills")
    (destination / "keep.txt").write_text("Keep old data")
    source = write_skill(tmp_path / "incoming")
    if kind == "descriptor":
        (source / "SKILL.md").write_text("Invalid skill")
    elif kind == "nested-git":
        (source / ".git").mkdir()
    else:
        (source / "reference").symlink_to("../outside.txt")

    with pytest.raises(SkillSyncError):
        add_skill(source, config, force=True)

    assert not source.is_symlink()
    assert (destination / "keep.txt").read_text() == "Keep old data"
    assert not list(destination.parent.glob(".skill-sync-replaced-*"))


def test_force_add_rejects_repository_skill_symlink_without_touching_target(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    source = write_skill(tmp_path / "incoming")
    foreign = write_skill(tmp_path / "foreign")
    destination = config.repository_path / "skills" / source.name
    destination.symlink_to(foreign)

    with pytest.raises(SkillSyncError, match="real directory"):
        add_skill(source, config, force=True)

    assert destination.readlink() == foreign
    assert (foreign / "SKILL.md").is_file()
    assert not source.is_symlink()


def test_cli_add_force_requires_flag_then_replaces_existing_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = make_personal_config(tmp_path)
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    destination = write_skill(config.repository_path / "skills")
    (destination / "old-only.txt").write_text("Old data")
    source = write_skill(tmp_path / "incoming")
    (source / "new-only.txt").write_text("New data")

    assert main(["add", str(source)]) == 1
    assert "Pass --force" in capsys.readouterr().err
    assert (destination / "old-only.txt").read_text() == "Old data"

    assert main(["add", str(source), "--force"]) == 0

    assert "Added skill: demo-skill" in capsys.readouterr().out
    assert source.readlink() == destination
    assert not (destination / "old-only.txt").exists()
    assert (destination / "new-only.txt").read_text() == "New data"


def test_force_add_can_replace_invalid_repository_contents(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    destination = config.repository_path / "skills/demo-skill"
    destination.write_text("Not a valid stored skill")
    source = write_skill(tmp_path / "incoming")

    add_skill(source, config, force=True)

    assert destination.is_dir()
    assert (destination / "SKILL.md").is_file()
    assert source.readlink() == destination


def test_force_add_returns_retained_path_when_committed_backup_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_personal_config(tmp_path)
    destination = write_skill(config.repository_path / "skills")
    (destination / "old-only.txt").write_text("Old data")
    source = write_skill(tmp_path / "incoming")
    (source / "new-only.txt").write_text("New data")
    filesystem = Mock(wraps=shutil)
    filesystem.rmtree.side_effect = PermissionError("cleanup denied")
    monkeypatch.setattr("skill_sync.adder.shutil", filesystem)

    result = add_skill(source, config, force=True)

    backup_root = next(destination.parent.glob(".skill-sync-replaced-*"))
    assert result.changed
    assert result.cleanup_path == backup_root
    assert source.readlink() == destination
    assert (destination / "new-only.txt").read_text() == "New data"
    assert not (destination / "old-only.txt").exists()
    assert (backup_root / "original/old-only.txt").read_text() == "Old data"
    assert read_managed_installations(config.state_home)[str(source)].source_path == destination


@pytest.mark.skipif(os.geteuid() == 0, reason="Root bypasses directory permission restrictions")
def test_cli_force_add_succeeds_with_warning_for_readonly_old_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = make_personal_config(tmp_path)
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    destination = write_skill(config.repository_path / "skills")
    readonly = destination / "assets"
    readonly.mkdir()
    (readonly / "old-only.txt").write_text("Read-only old content")
    readonly.chmod(0o555)
    source = write_skill(tmp_path / "incoming")
    (source / "new-only.txt").write_text("New data")
    installed = install_skill(
        "demo-skill", config, target="codex", target_root=tmp_path / "installed"
    ).path
    try:
        assert main(["add", str(source), "--force"]) == 0

        output = capsys.readouterr()
        backup_root = next(destination.parent.glob(".skill-sync-replaced-*"))
        assert "Added skill: demo-skill" in output.out
        assert "[WARN] Skill added successfully" in output.err
        assert str(backup_root) in output.err
        assert source.readlink() == destination
        assert (installed / "new-only.txt").read_text() == "New data"
        assert (backup_root / "original/assets/old-only.txt").read_text() == "Read-only old content"
        assert set(read_managed_installations(config.state_home)) == {str(source), str(installed)}
    finally:
        for path in destination.parent.glob(".skill-sync-replaced-*/original/assets"):
            path.chmod(0o755)
