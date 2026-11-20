from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from skill_sync.cli import main
from skill_sync.config import config_path, installations_path
from skill_sync.installations import read_managed_installations
from skill_sync.installer import install_skill

from tests.skill_sync_helpers import make_personal_config, write_skill

if TYPE_CHECKING:
    from pathlib import Path


def _break_descriptor(skill: Path, kind: str) -> None:
    contents = {
        "description": f"---\nname: {skill.name}\n---\n".encode(),
        "frontmatter": b"Missing frontmatter delimiters\n",
        "encoding": b"\xff\xfe",
    }
    (skill / "SKILL.md").write_bytes(contents[kind])


@pytest.mark.parametrize("kind", ["description", "frontmatter", "encoding"])
@pytest.mark.parametrize("invalid_name", ["alpha-skill", "zeta-skill"])
def test_batch_installs_healthy_skills_before_and_after_invalid_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    invalid_name: str,
) -> None:
    config = make_personal_config(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    invalid = write_skill(config.repository_path / "skills", invalid_name)
    healthy = write_skill(config.repository_path / "skills", "middle-skill")
    _break_descriptor(invalid, kind)
    original = (invalid / "SKILL.md").read_bytes()

    assert main(["install", "--all", "--target", "codex"]) == 1

    output = capsys.readouterr()
    assert "Installed: 1; skipped: 0; failed: 1" in output.out
    assert f"[ERROR] {invalid_name}:" in output.err
    assert (home / ".agents/skills/middle-skill").resolve() == healthy
    assert not (home / ".agents/skills" / invalid_name).exists()
    assert (invalid / "SKILL.md").read_bytes() == original
    assert len(read_managed_installations(config.state_home)) == 1


def test_batch_distinguishes_invalid_descriptor_from_same_name_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = make_personal_config(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    write_skill(config.repository_path / "skills", "alpha-skill")
    invalid = write_skill(config.repository_path / "skills", "beta-skill")
    healthy = write_skill(config.repository_path / "skills", "gamma-skill")
    foreign = write_skill(home / ".agents/skills", "alpha-skill")
    _break_descriptor(invalid, "description")
    original = (foreign / "SKILL.md").read_bytes()

    assert main(["install", "--all", "--target", "codex"]) == 1

    output = capsys.readouterr()
    assert "Installed: 1; skipped: 1; failed: 1" in output.out
    assert "[WARN] Skipped skill: alpha-skill" in output.err
    assert "[ERROR] beta-skill:" in output.err
    assert not foreign.is_symlink()
    assert (foreign / "SKILL.md").read_bytes() == original
    assert (home / ".agents/skills/gamma-skill").resolve() == healthy


def test_status_reports_installed_and_missing_paths_despite_invalid_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = make_personal_config(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    invalid = write_skill(config.repository_path / "skills", "alpha-skill")
    write_skill(config.repository_path / "skills", "beta-skill")
    root = home / ".agents/skills"
    installed = install_skill("alpha-skill", config, target="codex", target_root=root)
    missing = install_skill("beta-skill", config, target="codex", target_root=root)
    missing.path.unlink()
    _break_descriptor(invalid, "description")
    config_before = config_path(config.state_home).read_bytes()
    records_before = installations_path(config.state_home).read_bytes()

    assert main(["status"]) == 0

    output = capsys.readouterr()
    assert "skills: 1\n" in output.out
    assert "invalid skills: 1\n" in output.out
    assert "installations: 2\n" in output.out
    assert f"installation: alpha-skill\tok\t{installed.path}" in output.out
    assert f"installation: beta-skill\tmissing\t{missing.path}" in output.out
    assert "[WARN] Invalid skill: alpha-skill" in output.err
    assert "description" in output.err
    assert config_path(config.state_home).read_bytes() == config_before
    assert installations_path(config.state_home).read_bytes() == records_before


def test_list_reports_healthy_skills_and_warns_about_invalid_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = make_personal_config(tmp_path)
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    invalid = write_skill(config.repository_path / "skills", "alpha-skill")
    healthy = write_skill(config.repository_path / "skills", "beta-skill")
    _break_descriptor(invalid, "frontmatter")

    assert main(["list"]) == 0

    output = capsys.readouterr()
    assert f"beta-skill\tDemo skill.\t{healthy}" in output.out
    assert "alpha-skill" not in output.out
    assert "[WARN] Invalid skill: alpha-skill" in output.err


@pytest.mark.parametrize("command", ["install", "list", "status"])
def test_all_invalid_descriptors_are_reported_individually(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    config = make_personal_config(tmp_path)
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    names = ("alpha-skill", "beta-skill")
    for name in names:
        _break_descriptor(write_skill(config.repository_path / "skills", name), "description")
    args = [command, "--all", "--target", "codex"] if command == "install" else [command]

    assert main(args) == (1 if command == "install" else 0)

    output = capsys.readouterr()
    for name in names:
        assert name in output.err
    if command == "install":
        assert "Installed: 0; skipped: 0; failed: 2" in output.out
    elif command == "list":
        assert "No valid skills found." in output.out
    else:
        assert "skills: 0\n" in output.out
        assert "invalid skills: 2\n" in output.out
        assert "installations: 0\n" in output.out
    assert read_managed_installations(config.state_home) == {}


@pytest.mark.parametrize("command", ["install", "validate"])
def test_explicit_invalid_skill_commands_still_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    config = make_personal_config(tmp_path)
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    invalid = write_skill(config.repository_path / "skills")
    _break_descriptor(invalid, "description")
    args = (
        ["install", invalid.name, "--target", "codex"]
        if command == "install"
        else ["validate", str(invalid)]
    )

    assert main(args) == 1

    assert "description" in capsys.readouterr().err
    assert read_managed_installations(config.state_home) == {}
