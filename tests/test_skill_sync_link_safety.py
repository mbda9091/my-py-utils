from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest
from skill_sync.adder import add_skill
from skill_sync.config import installations_path
from skill_sync.errors import SkillSyncError
from skill_sync.installations import installation_status, read_managed_installations
from skill_sync.installer import install_skill
from skill_sync.repository import init_repository

from tests.skill_sync_helpers import make_personal_config, write_skill


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
def test_rollback_preserves_foreign_entry_replacing_link_before_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, *, force: bool
) -> None:
    config = make_personal_config(tmp_path)
    old_skill = write_skill(config.repository_path / "skills")
    root = tmp_path / "installed"
    destination = root / "demo-skill"
    if force:
        install_skill("demo-skill", config, target_root=root)
        new = make_personal_config(tmp_path / "new")
        write_skill(new.repository_path / "skills")
        config = init_repository(new.repository_path, state_home=config.state_home)
    records_path = installations_path(config.state_home)
    before = records_path.read_bytes() if records_path.exists() else None
    foreign = tmp_path / "foreign.txt"
    foreign.write_text("Foreign data")
    real_rename = Path.rename
    injected = False

    def replace_before_capture(path: Path, target: Path) -> Path:
        nonlocal injected
        if path == destination and target.name == "withdrawn":
            path.unlink()
            if kind == "file":
                path.write_text("Foreign data")
            elif kind == "directory":
                path.mkdir()
                (path / "foreign.txt").write_text("Foreign data")
            else:
                path.symlink_to(foreign)
            injected = True
        return real_rename(path, target)

    monkeypatch.setattr(Path, "rename", replace_before_capture)
    monkeypatch.setattr(
        "skill_sync.installer.write_managed_installations",
        Mock(side_effect=OSError("record failed")),
    )
    with pytest.raises(SkillSyncError, match="preserved") as caught:
        install_skill("demo-skill", config, target_root=root, force=force)

    assert injected
    if kind == "directory":
        saved = next((root.parent / ".skill-sync-backups" if force else root).glob("*/withdrawn"))
        assert (saved / "foreign.txt").read_text() == "Foreign data"
        assert str(saved) in str(caught.value)
    else:
        assert destination.read_text() == "Foreign data"
        if kind == "symlink":
            assert destination.readlink() == foreign
    if force:
        saved_old = next(root.parent.glob(".skill-sync-backups/*/original"))
        assert saved_old.readlink() == old_skill
        assert str(saved_old) in str(caught.value)
    assert (records_path.read_bytes() if records_path.exists() else None) == before


@pytest.mark.parametrize("force", [False, True])
def test_rollback_does_not_unlink_shared_path_after_capturing_own_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, force: bool
) -> None:
    config = make_personal_config(tmp_path)
    write_skill(config.repository_path / "skills")
    root = tmp_path / "installed"
    destination = root / "demo-skill"
    if force:
        install_skill("demo-skill", config, target_root=root)
        new = make_personal_config(tmp_path / "new")
        write_skill(new.repository_path / "skills")
        config = init_repository(new.repository_path, state_home=config.state_home)
    real_rename = Path.rename
    real_unlink = Path.unlink
    injected = False

    def create_after_capture(path: Path, target: Path) -> Path:
        nonlocal injected
        result = real_rename(path, target)
        if path == destination and target.name == "withdrawn":
            path.write_text("New occupant after capture")
            injected = True
        return result

    def unlink_private_only(path: Path, *, missing_ok: bool = False) -> None:
        assert path != destination, "Rollback must not unlink the shared destination"
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "rename", create_after_capture)
    monkeypatch.setattr(Path, "unlink", unlink_private_only)
    monkeypatch.setattr(
        "skill_sync.installer.write_managed_installations",
        Mock(side_effect=OSError("record failed")),
    )
    with pytest.raises(SkillSyncError):
        install_skill("demo-skill", config, target_root=root, force=force)
    assert injected
    assert destination.read_text() == "New occupant after capture"


@pytest.mark.parametrize("force", [False, True])
def test_link_identity_is_established_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, force: bool
) -> None:
    config = make_personal_config(tmp_path)
    write_skill(config.repository_path / "skills")
    root = tmp_path / "installed"
    destination = root / "demo-skill"
    if force:
        install_skill("demo-skill", config, target_root=root)
        new = make_personal_config(tmp_path / "new")
        write_skill(new.repository_path / "skills")
        config = init_repository(new.repository_path, state_home=config.state_home)
    real_link = os.link

    def replace_after_publish(source: Path, target: Path, *, follow_symlinks: bool) -> None:
        real_link(source, target, follow_symlinks=follow_symlinks)
        if source.name == "new-link" and target == destination:
            destination.unlink()
            destination.write_text("Replacement after publication")

    monkeypatch.setattr("skill_sync.links.os.link", replace_after_publish)
    monkeypatch.setattr(
        "skill_sync.installer.write_managed_installations",
        Mock(side_effect=OSError("record failed")),
    )
    with pytest.raises(SkillSyncError, match="preserved"):
        install_skill("demo-skill", config, target_root=root, force=force)
    assert destination.read_text() == "Replacement after publication"


@pytest.mark.parametrize("rebind", [False, True])
def test_force_rejects_lexically_matching_target_through_foreign_symlink(
    tmp_path: Path, *, rebind: bool
) -> None:
    config = make_personal_config(tmp_path)
    old_skill = write_skill(config.repository_path / "skills")
    root = tmp_path / "installed"
    destination = install_skill("demo-skill", config, target_root=root).path
    if rebind:
        new = make_personal_config(tmp_path / "new")
        write_skill(new.repository_path / "skills")
        config = init_repository(new.repository_path, state_home=config.state_home)
    foreign = write_skill(tmp_path / "foreign")
    nested = foreign.parent / "nested"
    nested.mkdir()
    hop = old_skill.parent / "hop"
    hop.symlink_to(nested, target_is_directory=True)
    destination.unlink()
    destination.symlink_to(hop / ".." / "demo-skill", target_is_directory=True)
    assert destination.resolve() == foreign
    before = installations_path(config.state_home).read_bytes()
    raw_link = destination.readlink()

    with pytest.raises(SkillSyncError, match="preserved"):
        install_skill("demo-skill", config, target_root=root, force=False)

    assert destination.readlink() == raw_link
    assert destination.resolve() == foreign
    assert installations_path(config.state_home).read_bytes() == before
    record = read_managed_installations(config.state_home)[str(destination)]
    assert installation_status(record, config.repository_path) == "replaced"


@pytest.mark.parametrize("target_form", ["absolute", "relative"])
@pytest.mark.parametrize("missing", ["none", "skill", "repository"])
def test_force_accepts_provable_old_sources_including_removed_repository(
    tmp_path: Path, target_form: str, missing: str
) -> None:
    config = make_personal_config(tmp_path)
    old_repository = config.repository_path
    old_skill = write_skill(old_repository / "skills")
    root = tmp_path / "installed"
    destination = install_skill("demo-skill", config, target_root=root).path
    if target_form == "relative":
        destination.unlink()
        destination.symlink_to(os.path.relpath(old_skill, destination.parent))
    new = make_personal_config(tmp_path / "new")
    new_skill = write_skill(new.repository_path / "skills")
    config = init_repository(new.repository_path, state_home=config.state_home)
    if missing != "none":
        shutil.rmtree(old_skill if missing == "skill" else old_repository)

    install_skill("demo-skill", config, target_root=root, force=True)

    assert destination.readlink() == new_skill
    assert read_managed_installations(config.state_home)[str(destination)].source_path == new_skill


@pytest.mark.parametrize("target", ["cycle", "missing-parent", "file-parent"])
def test_force_preserves_unverifiable_broken_links(tmp_path: Path, target: str) -> None:
    config = make_personal_config(tmp_path)
    skill = write_skill(config.repository_path / "skills")
    root = tmp_path / "installed"
    destination = install_skill("demo-skill", config, target_root=root).path
    destination.unlink()
    if target == "cycle":
        destination.symlink_to(destination)
    else:
        hop = skill.parent / "hop"
        if target == "file-parent":
            hop.write_text("Regular file")
        destination.symlink_to(hop / ".." / "demo-skill")
    raw = destination.readlink()
    before = installations_path(config.state_home).read_bytes()

    with pytest.raises(SkillSyncError, match="preserved"):
        install_skill("demo-skill", config, target_root=root, force=False)

    assert destination.readlink() == raw
    assert installations_path(config.state_home).read_bytes() == before


@pytest.mark.parametrize("operation", ["install", "add"])
def test_current_personal_skill_alias_can_still_be_normalized(
    tmp_path: Path, operation: str
) -> None:
    config = make_personal_config(tmp_path)
    skill = write_skill(config.repository_path / "skills")
    alias = tmp_path / "repository-alias"
    alias.symlink_to(config.repository_path, target_is_directory=True)
    root = tmp_path / "installed"
    root.mkdir()
    destination = root / "demo-skill"
    destination.symlink_to(alias / "skills" / "demo-skill")

    if operation == "install":
        install_skill("demo-skill", config, target_root=root)
    else:
        add_skill(destination, config)

    assert destination.readlink() == skill
    assert read_managed_installations(config.state_home)[str(destination)].source_path == skill
