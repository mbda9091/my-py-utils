from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from skill_sync.cli import main
from skill_sync.config import (
    atomic_write_json,
    config_path,
    config_to_payload,
    init_config,
    read_installations,
)
from skill_sync.git_repo import run_git
from skill_sync.repository import read_repository_config

from tests.skill_sync_helpers import make_local_repository, make_registry, write_skill

if TYPE_CHECKING:
    from pathlib import Path

ARGPARSE_USAGE_ERROR = 2


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
    repository = make_local_repository(tmp_path)

    assert main(["init", "--repo", str(repository)]) == 0
    init_output = capsys.readouterr()
    assert f"Configured personal repository: {repository}" in init_output.out

    assert main(["status"]) == 0
    status_output = capsys.readouterr()
    assert f"repository: {repository}" in status_output.out
    assert "skills: 0" in status_output.out


def test_cli_list_reuses_config_from_another_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SKILL_SYNC_HOME", str(tmp_path / "state"))
    repository = make_local_repository(tmp_path)
    assert main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    write_skill(repository / "skills")
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    assert main(["list"]) == 0
    output = capsys.readouterr()
    assert f"demo-skill\tDemo skill.\t{repository / 'skills' / 'demo-skill'}" in output.out
    assert read_repository_config().repository_path == repository


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
    assert "skill structure is valid:" in output.out
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
    init_config("git@example.com:me/skills.git", branch="main")
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
    assert (skills_root / "alpha-skill").is_symlink()
    assert (skills_root / "zeta-skill").is_symlink()
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
    init_config("git@example.com:me/skills.git", branch="main")
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
    init_config("git@example.com:me/skills.git", branch="main")
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
    assert existing.is_symlink()
    assert (existing / "SKILL.md").read_text(encoding="utf-8") == "Registry alpha\n"


def test_cli_install_requires_skill_name_or_all(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["install", "--target", "codex"]) == 1

    output = capsys.readouterr()
    assert "Pass a skill name or --all." in output.err


def test_cli_add_and_publish_managed_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = make_registry(tmp_path)
    atomic_write_json(config_path(config.state_home), config_to_payload(config))
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    original = write_skill(tmp_path / "home" / ".agents" / "skills")

    assert main(["add", str(original)]) == 0
    output = capsys.readouterr()
    assert "Added skill: demo-skill" in output.out
    assert f"path: {original}" in output.out
    assert original.is_symlink()
    assert main(["add", str(original)]) == 0
    assert "Skill already managed: demo-skill" in capsys.readouterr().out
    assert main(["publish", str(original), "--no-push"]) == 0
    output = capsys.readouterr()
    assert "Published skill: demo-skill" in output.out
    assert "pushed: false" in output.out
    assert output.err == ""


def test_cli_add_conflict_returns_error_without_moving_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = make_registry(tmp_path)
    atomic_write_json(config_path(config.state_home), config_to_payload(config))
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    write_skill(config.checkout / "skills")
    original = write_skill(tmp_path / "local")

    assert main(["add", str(original)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert not original.is_symlink()


def test_cli_reuses_single_repository_across_processes(tmp_path: Path) -> None:
    repository = make_local_repository(tmp_path)
    state = tmp_path / "state"
    other = tmp_path / "other"
    other.mkdir()
    env = dict(os.environ, SKILL_SYNC_HOME=str(state), HOME=str(tmp_path / "home"))
    code = "import sys; from skill_sync.cli import main; sys.exit(main())"
    git_before = {
        path.relative_to(repository): path.read_bytes()
        for path in (repository / ".git").rglob("*")
        if path.is_file()
    }

    def invoke(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [sys.executable, "-c", code, *args],
            cwd=other,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    initialized = invoke("init", "--repo", str(repository))
    assert initialized.returncode == 0, initialized.stderr
    write_skill(repository / "skills")
    config_before = config_path(state).read_bytes()
    listed = invoke("list")
    status = invoke("status")

    assert listed.returncode == 0, listed.stderr
    assert "demo-skill\tDemo skill." in listed.stdout
    assert status.returncode == 0, status.stderr
    assert f"repository: {repository}" in status.stdout
    assert "skills: 1" in status.stdout
    assert config_path(state).read_bytes() == config_before
    assert not (state / "installations.json").exists()
    git_after = {
        path.relative_to(repository): path.read_bytes()
        for path in (repository / ".git").rglob("*")
        if path.is_file()
    }
    assert git_after == git_before


def test_cli_local_catalog_survives_dirty_branch_and_clone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = make_registry(tmp_path)
    write_skill(registry.checkout / "skills")
    run_git(["add", "skills"], cwd=registry.checkout)
    run_git(["commit", "-m", "Add skill manually"], cwd=registry.checkout)
    run_git(["push"], cwd=registry.checkout)
    clone = tmp_path / "clone"
    run_git(["clone", registry.repository, str(clone)], cwd=None)
    monkeypatch.setenv("SKILL_SYNC_HOME", str(tmp_path / "local-state"))
    assert main(["init", "--repo", str(clone)]) == 0
    capsys.readouterr()
    run_git(["checkout", "-b", "editing"], cwd=clone)
    descriptor = clone / "skills" / "demo-skill" / "SKILL.md"
    descriptor.write_text(descriptor.read_text().replace("Demo skill.", "Edited description."))
    index_before = (clone / ".git" / "index").read_bytes()
    config_before = (clone / ".git" / "config").read_bytes()

    assert main(["list"]) == 0
    assert "Edited description." in capsys.readouterr().out
    assert main(["status"]) == 0
    assert "skills: 1" in capsys.readouterr().out
    assert (clone / ".git" / "index").read_bytes() == index_before
    assert (clone / ".git" / "config").read_bytes() == config_before
    assert not (clone / "skills" / "demo-skill" / "skill-sync.json").exists()


def test_cli_legacy_status_and_list_require_explicit_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("SKILL_SYNC_HOME", str(state))
    init_config("git@example.com:me/skills.git", branch="main")
    original = config_path(state).read_bytes()

    assert main(["status"]) == 0
    assert "Legacy registry configuration" in capsys.readouterr().out
    assert main(["list"]) == 1
    assert "init --repo" in capsys.readouterr().err
    assert config_path(state).read_bytes() == original
    assert not (state / "repository").exists()


@pytest.mark.parametrize(
    "args", [["init"], ["init", "git@example.com:me/skills.git"], ["list", "--repo", "other"]]
)
def test_cli_requires_configuration_once_and_rejects_repository_selection(args: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == ARGPARSE_USAGE_ERROR


def test_cli_missing_config_explains_initial_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SKILL_SYNC_HOME", str(tmp_path / "state"))
    assert main(["list"]) == 1
    assert "init --repo" in capsys.readouterr().err
