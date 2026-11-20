from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from skill_sync.adder import add_skill
from skill_sync.errors import SkillSyncError
from skill_sync.installations import read_managed_installations
from skill_sync.installer import install_skill
from skill_sync.links import EntryState, link_directory
from skill_sync.repository import init_repository

from tests.skill_sync_helpers import make_personal_config, run_git, write_skill

if TYPE_CHECKING:
    from contextlib import AbstractContextManager


def test_install_links_plain_skill_and_tracks_multiple_use_paths(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    skill = write_skill(config.repository_path / "skills")
    first = install_skill("demo-skill", config, target="codex", target_root=tmp_path / "first")
    second = install_skill("demo-skill", config, target="codex", target_root=tmp_path / "second")

    assert first.path.is_symlink()
    assert first.path.readlink() == skill
    assert first.source_path == skill
    assert second.path.resolve() == skill
    assert not (skill / "skill-sync.json").exists()
    (first.path / "SKILL.md").write_text("Edited locally\n")
    assert (second.path / "SKILL.md").read_text() == "Edited locally\n"
    records = read_managed_installations(config.state_home)
    assert set(records) == {str(first.path), str(second.path)}
    assert records[str(first.path)].source_path == skill
    assert not (config.repository_path / ".git" / "index").exists()


def test_install_is_idempotent(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    write_skill(config.repository_path / "skills")
    first = install_skill("demo-skill", config, target="codex", target_root=tmp_path / "installed")
    inode = first.path.lstat().st_ino
    second = install_skill("demo-skill", config, target="codex", target_root=tmp_path / "installed")
    assert first == second
    assert first.path.lstat().st_ino == inode
    assert len(read_managed_installations(config.state_home)) == 1


@pytest.mark.parametrize("kind", ["directory", "file", "link", "broken"])
@pytest.mark.parametrize("force", [False, True])
def test_install_preserves_unknown_content(
    tmp_path: Path,
    kind: str,
    *,
    force: bool,
) -> None:
    config = make_personal_config(tmp_path)
    write_skill(config.repository_path / "skills")
    target_root = tmp_path / "installed"
    target_root.mkdir()
    destination = target_root / "demo-skill"
    other = tmp_path / "other"
    if kind == "directory":
        destination.mkdir()
        (destination / "keep.txt").write_text("Keep\n")
    elif kind == "file":
        destination.write_text("Keep\n")
    else:
        if kind == "link":
            other.mkdir()
            (other / "keep.txt").write_text("Keep\n")
        destination.symlink_to(other, target_is_directory=True)
    inode = destination.lstat().st_ino

    with pytest.raises(SkillSyncError, match="preserved"):
        install_skill("demo-skill", config, target="codex", target_root=target_root, force=force)

    assert destination.lstat().st_ino == inode
    if kind in {"directory", "link"}:
        assert (destination / "keep.txt").read_text() == "Keep\n"
    elif kind == "file":
        assert destination.read_text() == "Keep\n"
    else:
        assert destination.readlink() == other
    assert not read_managed_installations(config.state_home)


@pytest.mark.parametrize("broken", [False, True])
def test_force_relinks_verified_previous_repository(
    tmp_path: Path,
    *,
    broken: bool,
) -> None:
    first = make_personal_config(tmp_path / "first")
    original = write_skill(first.repository_path / "skills")
    target_root = tmp_path / "installed"
    installed = install_skill("demo-skill", first, target="codex", target_root=target_root).path
    new_root = make_personal_config(tmp_path / "second").repository_path
    new_skill = write_skill(new_root / "skills")
    current = init_repository(new_root, state_home=first.state_home)
    if broken:
        original.rename(original.with_name("old-copy"))

    with pytest.raises(SkillSyncError, match="Pass --force"):
        install_skill("demo-skill", current, target="codex", target_root=target_root)
    install_skill("demo-skill", current, target="codex", target_root=target_root, force=True)

    assert installed.readlink() == new_skill
    assert read_managed_installations(first.state_home)[str(installed)].source_path == new_skill
    assert (original if not broken else original.with_name("old-copy")).is_dir()


def test_force_does_not_trust_stale_record(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    write_skill(config.repository_path / "skills")
    root = tmp_path / "installed"
    destination = install_skill("demo-skill", config, target="codex", target_root=root).path
    destination.unlink()
    other = tmp_path / "third-party"
    other.mkdir()
    destination.symlink_to(other, target_is_directory=True)

    with pytest.raises(SkillSyncError, match="preserved"):
        install_skill("demo-skill", config, target="codex", target_root=root, force=True)
    assert destination.readlink() == other


@pytest.mark.parametrize("failure", ["link", "record"])
def test_failed_relink_restores_old_link_and_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    first = make_personal_config(tmp_path / "first")
    original = write_skill(first.repository_path / "skills")
    root = tmp_path / "installed"
    destination = install_skill("demo-skill", first, target="codex", target_root=root).path
    new = make_personal_config(tmp_path / "second")
    write_skill(new.repository_path / "skills")
    config = init_repository(new.repository_path, state_home=first.state_home)
    records_before = (first.state_home / "installations.json").read_bytes()
    target = (
        "pathlib.Path.symlink_to"
        if failure == "link"
        else "skill_sync.installer.write_managed_installations"
    )
    monkeypatch.setattr(target, Mock(side_effect=OSError("operation failed")))

    with pytest.raises(SkillSyncError, match="operation failed"):
        install_skill("demo-skill", config, target="codex", target_root=root, force=True)

    assert destination.readlink() == original
    assert (first.state_home / "installations.json").read_bytes() == records_before


def test_install_rejects_missing_skill_and_unsupported_target(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    with pytest.raises(SkillSyncError, match="Skill not found"):
        install_skill("missing", config, target="codex", target_root=tmp_path / "installed")
    with pytest.raises(SkillSyncError, match="codex"):
        install_skill("demo-skill", config, target="other")


def test_failed_rollback_keeps_original_link_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = make_personal_config(tmp_path / "first")
    original = write_skill(first.repository_path / "skills")
    root = tmp_path / "installed"
    install_skill("demo-skill", first, target="codex", target_root=root)
    new = make_personal_config(tmp_path / "second")
    write_skill(new.repository_path / "skills")
    config = init_repository(new.repository_path, state_home=first.state_home)
    monkeypatch.setattr("skill_sync.links.os.link", Mock(side_effect=OSError("restore failed")))
    monkeypatch.setattr(Path, "symlink_to", Mock(side_effect=OSError("link failed")))
    with pytest.raises(SkillSyncError, match="Original data kept at"):
        install_skill("demo-skill", config, target="codex", target_root=root, force=True)
    backups = list(root.glob(".skill-sync-backup-*/original"))
    assert len(backups) == 1
    assert backups[0].readlink() == original


@pytest.mark.parametrize("force", [False, True])
def test_install_preserves_directory_created_after_initial_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, force: bool
) -> None:
    config = make_personal_config(tmp_path)
    write_skill(config.repository_path / "skills")
    root = tmp_path / "installed"
    destination = root / "demo-skill"

    def create_then_link(
        path: Path, source: Path, *, expected: EntryState | None
    ) -> AbstractContextManager[Path | None]:
        path.mkdir(parents=True)
        (path / "foreign.txt").write_text("Keep foreign data")
        return link_directory(path, source, expected=expected)

    monkeypatch.setattr("skill_sync.installer.link_directory", create_then_link)
    with pytest.raises(SkillSyncError, match="Failed to link"):
        install_skill("demo-skill", config, target="codex", target_root=root, force=force)
    assert not destination.is_symlink()
    assert (destination / "foreign.txt").read_text() == "Keep foreign data"
    assert not read_managed_installations(config.state_home)


@pytest.mark.parametrize("moment", ["before-helper", "during-rename"])
def test_force_preserves_replacement_of_previously_verified_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, moment: str
) -> None:
    old = make_personal_config(tmp_path / "old")
    write_skill(old.repository_path / "skills")
    root = tmp_path / "installed"
    destination = install_skill("demo-skill", old, target="codex", target_root=root).path
    new = make_personal_config(tmp_path / "new")
    write_skill(new.repository_path / "skills")
    config = init_repository(new.repository_path, state_home=old.state_home)
    records_before = (config.state_home / "installations.json").read_bytes()

    def replace() -> None:
        destination.unlink()
        destination.mkdir()
        (destination / "foreign.txt").write_text("Keep replacement")

    original_rename = Path.rename

    def replace_then_rename(path: Path, target: Path) -> Path:
        if path == destination:
            replace()
        return original_rename(path, target)

    def replace_then_link(
        path: Path, source: Path, *, expected: EntryState | None
    ) -> AbstractContextManager[Path | None]:
        replace()
        return link_directory(path, source, expected=expected)

    if moment == "during-rename":
        monkeypatch.setattr(Path, "rename", replace_then_rename)
    else:
        monkeypatch.setattr("skill_sync.installer.link_directory", replace_then_link)
    with pytest.raises(SkillSyncError, match="changed during operation"):
        install_skill("demo-skill", config, target="codex", target_root=root, force=True)
    assert not destination.is_symlink()
    assert (destination / "foreign.txt").read_text() == "Keep replacement"
    assert (config.state_home / "installations.json").read_bytes() == records_before


def test_force_rollback_preserves_new_occupant_and_old_link_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = make_personal_config(tmp_path / "old")
    old_skill = write_skill(old.repository_path / "skills")
    root = tmp_path / "installed"
    destination = install_skill("demo-skill", old, target="codex", target_root=root).path
    new = make_personal_config(tmp_path / "new")
    write_skill(new.repository_path / "skills")
    config = init_repository(new.repository_path, state_home=old.state_home)
    before = (config.state_home / "installations.json").read_bytes()

    real_link = os.link

    def create_occupant(source: Path, target: Path, *, follow_symlinks: bool) -> None:
        if target == destination:
            target.write_text("New occupant")
        real_link(source, target, follow_symlinks=follow_symlinks)

    monkeypatch.setattr("skill_sync.links.os.link", create_occupant)
    with pytest.raises(SkillSyncError, match="Original data kept at"):
        install_skill("demo-skill", config, target="codex", target_root=root, force=True)
    assert destination.read_text() == "New occupant"
    assert next(root.glob(".skill-sync-backup-*/original")).readlink() == old_skill
    assert (config.state_home / "installations.json").read_bytes() == before


def test_local_add_install_edit_reflects_in_git_diff_without_git_metadata_writes(
    tmp_path: Path,
) -> None:
    config = make_personal_config(tmp_path)
    original = write_skill(tmp_path / "external")
    add_skill(original, config)
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
            "Track skill manually",
        ],
        cwd=config.repository_path,
    )
    run_git(["checkout", "-b", "editing"], cwd=config.repository_path)
    (config.repository_path / "unrelated.txt").write_text("Unrelated untracked work")
    git_dir = config.repository_path / ".git"
    before = {
        str(path.relative_to(git_dir)): path.read_bytes()
        for path in git_dir.rglob("*")
        if path.is_file()
    }

    result = install_skill("demo-skill", config, target="codex", target_root=tmp_path / "installed")
    descriptor = result.path / "SKILL.md"
    descriptor.write_text(descriptor.read_text() + "\nRevision through link\n")
    diff = run_git(
        ["--no-optional-locks", "diff", "--", "skills/demo-skill/SKILL.md"],
        cwd=config.repository_path,
    )

    assert "+Revision through link" in diff
    assert "Revision through link" in (original / "SKILL.md").read_text()
    after = {
        str(path.relative_to(git_dir)): path.read_bytes()
        for path in git_dir.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert (config.repository_path / "unrelated.txt").read_text() == "Unrelated untracked work"
