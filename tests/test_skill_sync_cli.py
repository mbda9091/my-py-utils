from __future__ import annotations

from typing import TYPE_CHECKING

from skill_sync.cli import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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
