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


def test_install_copies_skill_without_registry_metadata_and_records_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config(tmp_path)
    write_registry_skill(config.checkout)
    target_root = tmp_path / "agents" / "skills"
    monkeypatch.setattr("skill_sync.installer.current_commit", Mock(return_value="abc1234"))

    result = install_skill("demo-skill", config, target="codex", target_root=target_root)

    installed = target_root / "demo-skill"
    assert result.path == installed.resolve()
    assert (installed / "SKILL.md").read_text(encoding="utf-8") == "Skill body\n"
    assert not (installed / "skill-sync.json").exists()
    records = read_installations(config.state_home)
    assert records["codex:demo-skill"].commit == "abc1234"
    assert records["codex:demo-skill"].path == installed.resolve()


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
