from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from skill_sync.config import Config, read_installations
from skill_sync.errors import SkillSyncError
from skill_sync.installer import install_skill

if TYPE_CHECKING:
    from pathlib import Path


def make_config(tmp_path: Path) -> Config:
    checkout = tmp_path / "repository"
    (checkout / ".git").mkdir(parents=True)
    return Config(
        repository="git@example.com:me/skills.git",
        branch="main",
        checkout=checkout,
        state_home=tmp_path / "state",
    )


def write_registry_skill(checkout: Path, name: str = "demo-skill") -> Path:
    skill_dir = checkout / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Skill body\n", encoding="utf-8")
    (skill_dir / "skill-sync.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "name": name,
                "description": "Demo skill.",
                "compatibility": ["codex"],
                "updatedAt": "2026-08-09T00:00:00Z",
            },
        ),
        encoding="utf-8",
    )
    return skill_dir


def test_install_links_skill_and_records_install_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config(tmp_path)
    registry_skill = write_registry_skill(config.checkout)
    target_root = tmp_path / "agents" / "skills"
    monkeypatch.setattr("skill_sync.installer.current_commit", Mock(return_value="abc1234"))

    result = install_skill("demo-skill", config, target="codex", target_root=target_root)

    installed = target_root / "demo-skill"
    assert result.path == installed
    assert installed.is_symlink()
    assert installed.resolve() == registry_skill.resolve()
    assert (installed / "SKILL.md").read_text(encoding="utf-8") == "Skill body\n"
    assert (installed / "skill-sync.json").exists()
    (installed / "SKILL.md").write_text("Edited locally\n", encoding="utf-8")
    assert (registry_skill / "SKILL.md").read_text(encoding="utf-8") == "Edited locally\n"
    records = read_installations(config.state_home)
    assert records["codex:demo-skill"].commit == "abc1234"
    assert records["codex:demo-skill"].path == installed


def test_install_rejects_missing_registry_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config(tmp_path)
    monkeypatch.setattr("skill_sync.installer.current_commit", Mock(return_value="abc1234"))

    with pytest.raises(SkillSyncError, match="Skill not found"):
        install_skill("missing-skill", config, target="codex", target_root=tmp_path / "skills")


def test_install_rejects_existing_target_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config(tmp_path)
    write_registry_skill(config.checkout)
    target_root = tmp_path / "agents" / "skills"
    (target_root / "demo-skill").mkdir(parents=True)
    monkeypatch.setattr("skill_sync.installer.current_commit", Mock(return_value="abc1234"))

    with pytest.raises(SkillSyncError, match="Pass --force"):
        install_skill("demo-skill", config, target="codex", target_root=target_root)


def test_install_only_supports_codex_target(tmp_path: Path) -> None:
    config = make_config(tmp_path)

    with pytest.raises(SkillSyncError, match="codex"):
        install_skill("demo-skill", config, target="other", target_root=tmp_path / "skills")


def test_install_is_idempotent_for_existing_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config(tmp_path)
    registry_skill = write_registry_skill(config.checkout)
    target_root = tmp_path / "skills"
    monkeypatch.setattr("skill_sync.installer.current_commit", Mock(return_value="abc1234"))
    first = install_skill("demo-skill", config, target="codex", target_root=target_root)
    second = install_skill("demo-skill", config, target="codex", target_root=target_root)
    assert first == second
    assert first.path.is_symlink()
    assert registry_skill.is_dir()


@pytest.mark.parametrize("broken", [False, True])
def test_force_replaces_only_link_and_preserves_its_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    broken: bool,
) -> None:
    config = make_config(tmp_path)
    registry_skill = write_registry_skill(config.checkout)
    target_root = tmp_path / "skills"
    target_root.mkdir()
    other = tmp_path / "other-skill"
    if not broken:
        other.mkdir()
        (other / "keep.txt").write_text("Keep\n", encoding="utf-8")
    installed = target_root / "demo-skill"
    installed.symlink_to(other, target_is_directory=True)
    monkeypatch.setattr("skill_sync.installer.current_commit", Mock(return_value="abc1234"))

    with pytest.raises(SkillSyncError, match="Pass --force"):
        install_skill("demo-skill", config, target="codex", target_root=target_root)
    install_skill("demo-skill", config, target="codex", target_root=target_root, force=True)

    assert installed.resolve() == registry_skill
    if not broken:
        assert (other / "keep.txt").read_text(encoding="utf-8") == "Keep\n"


def test_install_restores_existing_directory_when_link_creation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config(tmp_path)
    write_registry_skill(config.checkout)
    target_root = tmp_path / "skills"
    original = target_root / "demo-skill"
    original.mkdir(parents=True)
    (original / "keep.txt").write_text("Keep\n", encoding="utf-8")
    monkeypatch.setattr("skill_sync.installer.current_commit", Mock(return_value="abc1234"))
    monkeypatch.setattr("pathlib.Path.symlink_to", Mock(side_effect=OSError("link failed")))

    with pytest.raises(SkillSyncError, match="link failed"):
        install_skill("demo-skill", config, target="codex", target_root=target_root, force=True)

    assert not original.is_symlink()
    assert (original / "keep.txt").read_text(encoding="utf-8") == "Keep\n"
    assert not read_installations(config.state_home)


def test_install_restores_existing_directory_when_record_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config(tmp_path)
    write_registry_skill(config.checkout)
    target_root = tmp_path / "skills"
    original = target_root / "demo-skill"
    original.mkdir(parents=True)
    (original / "keep.txt").write_text("Keep\n", encoding="utf-8")
    monkeypatch.setattr("skill_sync.installer.current_commit", Mock(return_value="abc1234"))
    monkeypatch.setattr(
        "skill_sync.installer.write_installations",
        Mock(side_effect=SkillSyncError("write failed")),
    )

    with pytest.raises(SkillSyncError, match="write failed"):
        install_skill("demo-skill", config, target="codex", target_root=target_root, force=True)

    assert not original.is_symlink()
    assert (original / "keep.txt").read_text(encoding="utf-8") == "Keep\n"


def test_failed_rollback_keeps_original_data_in_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config(tmp_path)
    write_registry_skill(config.checkout)
    target_root = tmp_path / "skills"
    original = target_root / "demo-skill"
    original.mkdir(parents=True)
    (original / "keep.txt").write_text("Keep\n", encoding="utf-8")
    monkeypatch.setattr("skill_sync.installer.current_commit", Mock(return_value="abc1234"))
    monkeypatch.setattr("pathlib.Path.symlink_to", Mock(side_effect=OSError("link failed")))
    original_rename = type(original).rename

    def fail_restore(path: Path, target: Path) -> Path:
        if path.name == "original":
            message = "restore failed"
            raise OSError(message)
        return original_rename(path, target)

    monkeypatch.setattr("pathlib.Path.rename", fail_restore)

    with pytest.raises(SkillSyncError, match="Original data kept at"):
        install_skill("demo-skill", config, target="codex", target_root=target_root, force=True)

    backups = list(target_root.glob(".skill-sync-backup-*/original/keep.txt"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "Keep\n"
