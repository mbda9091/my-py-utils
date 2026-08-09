from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import Mock

from skill_sync.cli import main
from skill_sync.config import read_installations

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def write_registry_skill(checkout: Path, name: str, body: str = "Skill body\n") -> Path:
    skill_dir = checkout / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
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


def test_cli_init_and_status_use_configured_state_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SKILL_SYNC_HOME", str(tmp_path / "state"))

    assert main(["init", "git@example.com:me/skills.git", "--branch", "main"]) == 0
    init_output = capsys.readouterr()
    assert "Configured registry: git@example.com:me/skills.git" in init_output.out

    assert main(["status"]) == 0
    status_output = capsys.readouterr()
    assert "registry: git@example.com:me/skills.git" in status_output.out
    assert "git identity: not configured" in status_output.out
    assert "cache exists: false" in status_output.out


def test_cli_init_accepts_git_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SKILL_SYNC_HOME", str(tmp_path / "state"))

    assert (
        main(
            [
                "init",
                "git@example.com:me/skills.git",
                "--branch",
                "main",
                "--git-name",
                "mbda9091",
                "--git-email",
                "bozieg@qq.com",
            ],
        )
        == 0
    )

    init_output = capsys.readouterr()
    assert "git identity: mbda9091 <bozieg@qq.com>" in init_output.out


def test_cli_validate_reports_metadata(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Demo skill.\n---\n",
        encoding="utf-8",
    )

    assert main(["validate", str(skill_dir)]) == 0

    output = capsys.readouterr()
    assert "skill can be published:" in output.out
    assert "name: demo-skill" in output.out


def test_cli_install_all_installs_each_registry_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_home = tmp_path / "state"
    home = tmp_path / "home"
    monkeypatch.setenv("SKILL_SYNC_HOME", str(state_home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("skill_sync.installer.current_commit", Mock(return_value="abc1234"))
    assert main(["init", "git@example.com:me/skills.git", "--branch", "main"]) == 0
    capsys.readouterr()
    checkout = state_home / "repository"
    (checkout / ".git").mkdir(parents=True)
    write_registry_skill(checkout, "zeta-skill", "Zeta body\n")
    write_registry_skill(checkout, "alpha-skill", "Alpha body\n")

    assert main(["install", "--all", "--target", "codex"]) == 0

    output = capsys.readouterr()
    assert "Installed skill: alpha-skill" in output.out
    assert "Installed skill: zeta-skill" in output.out
    assert output.err == ""
    skills_root = home / ".agents" / "skills"
    assert (skills_root / "alpha-skill" / "SKILL.md").read_text(encoding="utf-8") == "Alpha body\n"
    assert (skills_root / "zeta-skill" / "SKILL.md").read_text(encoding="utf-8") == "Zeta body\n"


def test_cli_install_all_skips_existing_skill_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_home = tmp_path / "state"
    home = tmp_path / "home"
    monkeypatch.setenv("SKILL_SYNC_HOME", str(state_home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("skill_sync.installer.current_commit", Mock(return_value="abc1234"))
    assert main(["init", "git@example.com:me/skills.git", "--branch", "main"]) == 0
    capsys.readouterr()
    checkout = state_home / "repository"
    (checkout / ".git").mkdir(parents=True)
    write_registry_skill(checkout, "alpha-skill", "Registry alpha\n")
    write_registry_skill(checkout, "beta-skill", "Registry beta\n")
    existing = home / ".agents" / "skills" / "alpha-skill"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("Existing alpha\n", encoding="utf-8")

    assert main(["install", "--all", "--target", "codex"]) == 0

    output = capsys.readouterr()
    assert "Installed skill: beta-skill" in output.out
    assert "Installed skill: alpha-skill" not in output.out
    assert "[ERROR] Target skill already exists:" in output.err
    assert "Pass --force to overwrite it." in output.err
    assert (existing / "SKILL.md").read_text(encoding="utf-8") == "Existing alpha\n"
    records = read_installations(state_home)
    assert "codex:alpha-skill" not in records
    assert "codex:beta-skill" in records


def test_cli_install_all_force_overwrites_existing_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_home = tmp_path / "state"
    home = tmp_path / "home"
    monkeypatch.setenv("SKILL_SYNC_HOME", str(state_home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("skill_sync.installer.current_commit", Mock(return_value="abc1234"))
    assert main(["init", "git@example.com:me/skills.git", "--branch", "main"]) == 0
    capsys.readouterr()
    checkout = state_home / "repository"
    (checkout / ".git").mkdir(parents=True)
    write_registry_skill(checkout, "alpha-skill", "Registry alpha\n")
    existing = home / ".agents" / "skills" / "alpha-skill"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("Existing alpha\n", encoding="utf-8")

    assert main(["install", "--all", "--target", "codex", "--force"]) == 0

    output = capsys.readouterr()
    assert "Installed skill: alpha-skill" in output.out
    assert output.err == ""
    assert (existing / "SKILL.md").read_text(encoding="utf-8") == "Registry alpha\n"


def test_cli_install_requires_skill_name_or_all(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["install", "--target", "codex"]) == 1

    output = capsys.readouterr()
    assert "Pass a skill name or --all." in output.err
