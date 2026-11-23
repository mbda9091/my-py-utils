from __future__ import annotations

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
from skill_sync.remover import remove_skill
from skill_sync.repository import init_repository

from tests.skill_sync_helpers import (
    make_committed_repository,
    make_personal_config,
    run_git,
    write_skill,
)


def test_remove_deletes_skill_and_known_links_preserving_backup_and_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = make_committed_repository(tmp_path).path
    config = init_repository(repo, state_home=tmp_path / "state")
    original = write_skill(tmp_path / "external")
    add_skill(original, config)
    source = repo / "skills" / original.name
    write_skill(Path.home() / ".agents/skills")
    installed = install_skill(source.name, config, force=True)
    history = install_skill(source.name, config, target_root=tmp_path / "historical").path
    other = write_skill(repo / "skills", "other")
    foreign = write_skill(tmp_path / "foreign", "outside")
    (source / "outside").symlink_to(foreign)
    run_git(["add", "skills"], cwd=repo)
    run_git(["commit", "-m", "Track skills"], cwd=repo)
    index = (repo / ".git/index").read_bytes()
    head = run_git(["rev-parse", "HEAD"], cwd=repo)
    (source / "untracked").write_text("Untracked data")
    result = remove_skill(source.name, config)
    assert result.removed
    assert not result.skipped_paths
    assert set(result.removed_links) == {original, installed.path, history}
    assert not source.exists()
    assert not original.is_symlink()
    assert not installed.path.is_symlink()
    assert not history.is_symlink()
    assert installed.backup_path is not None
    assert (installed.backup_path / "SKILL.md").is_file()
    assert other.is_dir()
    assert foreign.is_dir()
    assert not read_managed_installations(config.state_home)
    assert (repo / ".git/index").read_bytes() == index
    assert run_git(["rev-parse", "HEAD"], cwd=repo) == head
    assert "delete mode" in run_git(["diff", "--summary"], cwd=repo)
    assert not remove_skill(source.name, config).removed


@pytest.mark.parametrize("missing", ["descriptor", "skill", "repository"])
def test_remove_cleans_broken_skills_and_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    link = install_skill(source.name, config).path
    if missing == "descriptor":
        (source / "SKILL.md").unlink()
    else:
        shutil.rmtree(source if missing == "skill" else config.repository_path)
    result = remove_skill(source.name, config)
    assert result.removed == (missing == "descriptor")
    assert not link.is_symlink()
    assert not source.exists()
    assert not read_managed_installations(config.state_home)
    if missing == "repository":
        assert not config.repository_path.exists()


def test_remove_preserves_foreign_paths_old_repository_and_unresolved_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    path = install_skill(source.name, config).path
    path.unlink()
    path.mkdir()
    (path / "keep").write_text("Third party")
    old = write_skill(tmp_path / "old/skills")
    old_link = tmp_path / "old-link"
    old_link.symlink_to(old)
    unknown = tmp_path / "unknown-link"
    unknown.symlink_to(source)
    records = read_managed_installations(config.state_home)
    records[str(old_link)] = ManagedInstallation(source.name, "local-path", old_link, old)
    records[str(unknown)] = ManagedInstallation(source.name, "local-path", unknown, None)
    write_managed_installations(records, config.state_home)
    result = remove_skill(source.name, config)
    assert result.skipped_paths == (path,)
    assert (path / "keep").read_text() == "Third party"
    assert old_link.readlink() == old
    assert unknown.is_symlink()
    assert old.is_dir()
    assert read_managed_installations(config.state_home) == records


@pytest.mark.parametrize("failure", ["record", "directory"])
def test_remove_failure_rolls_back_all_links_and_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    links = [
        install_skill(source.name, config).path,
        install_skill(source.name, config, target_root=tmp_path / "extra").path,
    ]
    before = installations_path(config.state_home).read_bytes()
    if failure == "record":
        monkeypatch.setattr(
            "skill_sync.remover.write_managed_installations", Mock(side_effect=OSError("denied"))
        )
    else:
        rename = Path.rename

        def fail_directory(path: Path, target: Path) -> Path:
            if path == source:
                msg = "directory denied"
                raise OSError(msg)
            return rename(path, target)

        monkeypatch.setattr(Path, "rename", fail_directory)
    with pytest.raises(OSError, match="denied"):
        remove_skill(source.name, config)
    assert source.is_dir()
    for path in links:
        assert path.readlink() == source
    assert installations_path(config.state_home).read_bytes() == before
    assert not list(source.parent.glob(".skill-sync-remove-*"))


def test_remove_rejects_symlink_root_and_mounts_before_detaching_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    link = install_skill(source.name, config).path
    mount = source / "mounted"
    mount.mkdir()
    original_is_mount = Path.is_mount

    def is_mount(path: Path) -> bool:
        return path == mount or original_is_mount(path)

    monkeypatch.setattr(Path, "is_mount", is_mount)
    with pytest.raises(SkillSyncError, match="mounted"):
        remove_skill(source.name, config)
    assert link.readlink() == source
    monkeypatch.undo()
    foreign = tmp_path / "foreign"
    source.rename(foreign)
    source.symlink_to(foreign)
    with pytest.raises(SkillSyncError, match="real directory"):
        remove_skill(source.name, config)
    assert foreign.is_dir()
    assert link.is_symlink()


def test_remove_postcommit_cleanup_failure_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    source = write_skill(config.repository_path / "skills")
    link = install_skill(source.name, config).path
    monkeypatch.setattr("skill_sync.links.shutil.rmtree", Mock(side_effect=OSError("cleanup")))
    assert main(["remove", source.name]) == 0
    output = capsys.readouterr()
    assert "Removed skill" in output.out
    assert "[WARN]" in output.err
    assert ".skill-sync-remove-" in output.err
    assert not source.exists()
    assert not link.is_symlink()
    assert not read_managed_installations(config.state_home)


def test_remove_default_link_without_records_and_missing_matching_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    link = install_skill(source.name, config).path
    write_managed_installations({}, config.state_home)
    remove_skill(source.name, config)
    assert not link.is_symlink()
    source = write_skill(config.repository_path / "skills")
    path = install_skill(source.name, config).path
    path.unlink()
    remove_skill(source.name, config)
    assert not read_managed_installations(config.state_home)


def test_remove_rollback_preserves_new_occupant_and_restores_other_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = make_personal_config(tmp_path)
    source = write_skill(config.repository_path / "skills")
    link = install_skill(source.name, config).path
    extra = install_skill(source.name, config, target_root=tmp_path / "extra").path
    before = installations_path(config.state_home).read_bytes()

    def fail_record(*_args: object) -> None:
        link.mkdir()
        (link / "keep").write_text("New occupant")
        msg = "record failed"
        raise OSError(msg)

    monkeypatch.setattr("skill_sync.remover.write_managed_installations", fail_record)
    with pytest.raises(SkillSyncError, match="recovery files"):
        remove_skill(source.name, config)
    assert source.is_dir()
    assert extra.readlink() == source
    assert (link / "keep").read_text() == "New occupant"
    assert next(link.parent.glob(".skill-sync-remove-*/original")).readlink() == source
    assert installations_path(config.state_home).read_bytes() == before
