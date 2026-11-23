from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest
from skill_sync.cli import main
from skill_sync.config import installations_path
from skill_sync.errors import SkillSyncError
from skill_sync.installations import (
    ManagedInstallation,
    read_managed_installations,
    write_managed_installations,
)
from skill_sync.installer import install_skill
from skill_sync.repository import init_repository
from skill_sync.uninstaller import uninstall_skill

from tests.skill_sync_helpers import make_personal_config, write_skill


@pytest.mark.parametrize("missing", ["none", "skill", "repository", "record"])
def test_uninstall_preserves_source_and_other_use_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    skill = write_skill(config.repository_path / "skills")
    link = install_skill(skill.name, config).path
    other = install_skill(skill.name, config, target_root=tmp_path / "other").path
    if missing in {"skill", "repository"}:
        shutil.rmtree(skill if missing == "skill" else config.repository_path)
    if missing == "record":
        records = read_managed_installations(config.state_home)
        records.pop(str(link))
        write_managed_installations(records, config.state_home)
    result = uninstall_skill(skill.name, config)
    assert result.changed
    assert not link.is_symlink()
    assert other.is_symlink()
    assert str(link) not in read_managed_installations(config.state_home)
    assert not uninstall_skill(skill.name, config).changed
    assert skill.exists() == (missing not in {"skill", "repository"})
    if missing == "repository":
        assert not config.repository_path.exists()


@pytest.mark.parametrize(
    "kind", ["directory", "file", "foreign", "unknown-broken", "alias", "cycle"]
)
def test_uninstall_preserves_foreign_or_unprovable_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    skill = write_skill(config.repository_path / "skills")
    link = install_skill(skill.name, config).path
    link.unlink()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    if kind == "directory":
        link.mkdir()
    elif kind == "file":
        link.write_text("Keep")
    elif kind == "alias":
        alias = tmp_path / "alias"
        alias.symlink_to(config.repository_path)
        link.symlink_to(alias / "skills" / skill.name)
    else:
        link.symlink_to(
            link
            if kind == "cycle"
            else (tmp_path / "missing" if kind == "unknown-broken" else foreign)
        )
    before = installations_path(config.state_home).read_bytes()
    identity = link.lstat()
    result = uninstall_skill(skill.name, config)
    assert result.skipped
    assert link.lstat().st_ino == identity.st_ino
    assert installations_path(config.state_home).read_bytes() == before


def test_uninstall_verifies_old_source_and_preserves_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    skill = write_skill(config.repository_path / "skills")
    write_skill(Path.home() / ".agents/skills")
    installed = install_skill(skill.name, config, force=True)
    new = make_personal_config(tmp_path / "new")
    config = init_repository(new.repository_path, state_home=config.state_home)
    assert uninstall_skill(skill.name, config).changed
    assert installed.backup_path is not None
    assert (installed.backup_path / "SKILL.md").is_file()
    assert skill.is_dir()


def test_uninstall_record_failure_restores_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    skill = write_skill(config.repository_path / "skills")
    path = install_skill(skill.name, config).path
    before = installations_path(config.state_home).read_bytes()
    monkeypatch.setattr(
        "skill_sync.uninstaller.write_managed_installations", Mock(side_effect=OSError("denied"))
    )
    with pytest.raises(OSError, match="denied"):
        uninstall_skill(skill.name, config)
    assert path.readlink() == skill
    assert installations_path(config.state_home).read_bytes() == before
    assert not list(path.parent.glob(".skill-sync-remove-*"))


def test_uninstall_missing_known_record_and_unknown_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    skill = write_skill(config.repository_path / "skills")
    path = install_skill(skill.name, config).path
    path.unlink()
    assert not uninstall_skill(skill.name, config).changed
    assert not read_managed_installations(config.state_home)
    unknown = ManagedInstallation(skill.name, "codex", path, None)
    write_managed_installations({str(path): unknown}, config.state_home)
    uninstall_skill(skill.name, config)
    assert read_managed_installations(config.state_home)[str(path)] == unknown
    path.symlink_to(skill)
    assert uninstall_skill(skill.name, config).changed
    assert not read_managed_installations(config.state_home)


def test_cli_uninstall_and_invalid_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    skill = write_skill(config.repository_path / "skills")
    link = install_skill(skill.name, config).path
    (skill / "SKILL.md").write_text("Broken descriptor")
    assert main(["uninstall", skill.name]) == 0
    assert "Uninstalled skill" in capsys.readouterr().out
    assert not link.is_symlink()
    assert skill.is_dir()


def test_uninstall_rollback_preserves_new_occupant_and_recovery_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    skill = write_skill(config.repository_path / "skills")
    path = install_skill(skill.name, config).path

    def fail_record(*_args: object) -> None:
        path.mkdir()
        (path / "keep").write_text("New occupant")
        msg = "record failed"
        raise OSError(msg)

    monkeypatch.setattr("skill_sync.uninstaller.write_managed_installations", fail_record)
    with pytest.raises(SkillSyncError, match="recovery files"):
        uninstall_skill(skill.name, config)
    assert (path / "keep").read_text() == "New occupant"
    saved = next(path.parent.glob(".skill-sync-remove-*/original"))
    assert saved.readlink() == skill


def test_uninstall_postcommit_cleanup_failure_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    skill = write_skill(config.repository_path / "skills")
    path = install_skill(skill.name, config).path
    monkeypatch.setattr(
        "skill_sync.links.shutil.rmtree", Mock(side_effect=PermissionError("cleanup"))
    )
    assert main(["uninstall", skill.name]) == 0
    output = capsys.readouterr()
    assert "[WARN]" in output.err
    assert ".skill-sync-remove-" in output.err
    assert not path.is_symlink()
    assert not read_managed_installations(config.state_home)
