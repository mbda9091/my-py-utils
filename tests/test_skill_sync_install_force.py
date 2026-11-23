from __future__ import annotations

import errno
import json
import os
import stat
from pathlib import Path
from unittest.mock import Mock

import pytest
from skill_sync.cli import main
from skill_sync.errors import SkillSyncError
from skill_sync.installations import read_managed_installations
from skill_sync.installer import install_skill

from tests.skill_sync_helpers import make_personal_config, write_skill


@pytest.mark.parametrize("kind", ["directory", "file", "link", "broken"])
def test_force_preserves_original_entry_and_records_new_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    root = Path.home() / ".agents/skills"
    root.mkdir(parents=True)
    path = root / source.name
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep").write_text("Foreign")
    if kind == "directory":
        path.mkdir(mode=0o750)
        (path / "keep").write_text("Original")
        (path / "relative").symlink_to("keep")
    elif kind == "file":
        path.write_text("Original")
    else:
        path.symlink_to(foreign if kind == "link" else tmp_path / "missing")
    before = path.lstat()
    result = install_skill(source.name, config, force=True)
    assert result.path == path
    assert path.readlink() == source
    backup = result.backup_path
    assert backup is not None
    assert backup.lstat().st_ino == before.st_ino
    assert backup.lstat().st_mode == before.st_mode
    assert not backup.is_relative_to(root)
    metadata = json.loads((backup.parent / "backup.json").read_text())
    assert metadata["originalPath"] == str(path)
    assert backup.parent.stat().st_mode & 0o777 == stat.S_IRWXU
    assert (foreign / "keep").read_text() == "Foreign"
    if kind == "directory":
        assert (backup / "keep").read_text() == "Original"
        assert (backup / "relative").readlink() == Path("keep")
    elif kind == "file":
        assert backup.read_text() == "Original"
    assert read_managed_installations(config.state_home)[str(path)].source_path == source
    again = install_skill(source.name, config, force=True)
    assert again.backup_path is None
    assert len(list(backup.parent.parent.iterdir())) == 1
    path.unlink()
    backup.rename(path)
    assert path.lstat().st_ino == before.st_ino


@pytest.mark.parametrize("failure", ["link", "record", "rename"])
def test_force_directory_failure_restores_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    root = tmp_path / "installed"
    path = write_skill(root)
    (path / "keep").write_text("Original")
    if failure == "rename":
        rename = Path.rename

        def fail_move(entry: Path, destination: Path) -> Path:
            if entry == path:
                raise OSError(errno.EXDEV, "Cross-device rename")
            return rename(entry, destination)

        monkeypatch.setattr(Path, "rename", fail_move)
    else:
        target = (
            "pathlib.Path.symlink_to"
            if failure == "link"
            else ("skill_sync.installer.write_managed_installations")
        )
        monkeypatch.setattr(target, Mock(side_effect=OSError("Injected failure")))
    with pytest.raises(SkillSyncError):
        install_skill(source.name, config, force=True, target_root=root)
    assert not path.is_symlink()
    assert (path / "keep").read_text() == "Original"
    assert read_managed_installations(config.state_home) == {}


ARGUMENT_ERROR = 2


def test_force_unique_backups_and_fixed_home_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    source = write_skill(config.repository_path / "skills")
    path = write_skill(Path.home() / ".agents/skills")
    project = tmp_path / "project"
    project_skill = write_skill(project / ".agents/skills")
    monkeypatch.chdir(project)
    assert main(["install", source.name, "--force"]) == 0
    first = next((path.parent.parent / ".skill-sync-backups").iterdir())
    path.unlink()
    path.mkdir()
    (path / "second").write_text("Second")
    assert main(["install", source.name, "--force"]) == 0
    backups = list(first.parent.iterdir())
    assert len(backups) == len(("first", "second"))
    assert (first / "original/SKILL.md").is_file()
    assert project_skill.is_dir()
    assert not project_skill.is_symlink()
    output = capsys.readouterr()
    assert output.out.count("backup:") == len(backups)
    assert "target:" not in output.out
    with pytest.raises(SystemExit) as exc:
        main(["install", source.name, "--target", "codex"])
    assert exc.value.code == ARGUMENT_ERROR


def test_force_rejects_symlink_backup_root(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    root = tmp_path / "installed"
    original = write_skill(root)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / ".skill-sync-backups").symlink_to(elsewhere)
    with pytest.raises(SkillSyncError, match="real directory"):
        install_skill(source.name, config, force=True, target_root=root)
    assert not original.is_symlink()
    assert not list(elsewhere.iterdir())


@pytest.mark.skipif(os.geteuid() == 0, reason="Root bypasses directory write permissions")
def test_force_readonly_parent_preserves_directory(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    root = tmp_path / "installed"
    original = write_skill(root)
    root.chmod(0o500)
    try:
        with pytest.raises(SkillSyncError):
            install_skill(source.name, config, force=True, target_root=root)
        assert (original / "SKILL.md").is_file()
        assert not original.is_symlink()
    finally:
        root.chmod(0o700)


def test_force_metadata_failure_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    original = write_skill(tmp_path / "installed")
    monkeypatch.setattr("skill_sync.installer.atomic_write_json", Mock(side_effect=OSError("full")))
    with pytest.raises(SkillSyncError, match="full"):
        install_skill(source.name, config, force=True, target_root=original.parent)
    assert not original.is_symlink()
    assert (original / "SKILL.md").is_file()
    assert not list((tmp_path / ".skill-sync-backups").iterdir())


def test_force_postcommit_cleanup_warning_keeps_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    original = write_skill(tmp_path / "installed")
    unlink = Path.unlink

    def fail_cleanup(path: Path, *, missing_ok: bool = False) -> None:
        if path.name == "new-link":
            msg = "cleanup denied"
            raise PermissionError(msg)
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    result = install_skill(source.name, config, force=True, target_root=original.parent)
    assert result.cleanup_path is not None
    assert result.backup_path is not None
    assert (result.backup_path / "SKILL.md").is_file()
    assert original.readlink() == source
    assert read_managed_installations(config.state_home)[str(original)].source_path == source


def test_force_skips_fifo_and_invalid_source_leaves_directory(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    root = tmp_path / "installed"
    root.mkdir()
    path = root / source.name
    os.mkfifo(path)
    with pytest.raises(SkillSyncError, match="preserved"):
        install_skill(source.name, config, force=True, target_root=root)
    assert stat.S_ISFIFO(path.lstat().st_mode)
    path.unlink()
    path.mkdir()
    (path / "keep").write_text("Keep")
    (source / "SKILL.md").write_text("Invalid")
    with pytest.raises(SkillSyncError):
        install_skill(source.name, config, force=True, target_root=root)
    assert (path / "keep").read_text() == "Keep"


def test_first_install_reports_postcommit_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    monkeypatch.setattr(
        "skill_sync.links.shutil.rmtree", Mock(side_effect=OSError("cleanup denied"))
    )
    result = install_skill(source.name, config, target_root=tmp_path / "installed")
    assert result.cleanup_path is not None
    assert result.cleanup_path.name.startswith(".skill-sync-backup-")
    assert (result.cleanup_path / "new-link").is_symlink()
    assert result.path.readlink() == source
    assert result.backup_path is None
    assert read_managed_installations(config.state_home)[str(result.path)].source_path == source


def test_force_replaces_parent_directory_symlink_without_touching_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    root = Path.home() / ".agents/skills"
    root.mkdir(parents=True)
    sentinel = root / "keep.txt"
    sentinel.write_text("Preserve the linked directory")
    link = root / source.name
    link.symlink_to(".")
    inode = link.lstat().st_ino

    result = install_skill(source.name, config, force=True)

    assert link.readlink() == source
    assert result.backup_path is not None
    assert result.backup_path.readlink() == Path()
    assert result.backup_path.lstat().st_ino == inode
    assert sentinel.read_text() == "Preserve the linked directory"


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_force_checks_mounts_only_for_real_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    root = tmp_path / "installed"
    root.mkdir()
    path = root / source.name
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("Foreign contents")
    if kind == "directory":
        path.mkdir()
    else:
        path.symlink_to(foreign)
    identity = path.lstat()

    def is_mount(candidate: Path) -> bool:
        if candidate == path and candidate.is_symlink():
            pytest.fail("Mount detection must not follow the symlink target")
        return candidate == path

    monkeypatch.setattr(Path, "is_mount", is_mount)
    if kind == "directory":
        with pytest.raises(SkillSyncError, match="preserved"):
            install_skill(source.name, config, force=True, target_root=root)
        assert path.lstat().st_ino == identity.st_ino
        assert not read_managed_installations(config.state_home)
    else:
        result = install_skill(source.name, config, force=True, target_root=root)
        assert path.readlink() == source
        assert result.backup_path is not None
        assert result.backup_path.readlink() == foreign
        assert result.backup_path.lstat().st_ino == identity.st_ino
    assert (foreign / "keep.txt").read_text() == "Foreign contents"
