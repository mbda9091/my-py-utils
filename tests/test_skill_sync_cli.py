from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from skill_sync.cli import main
from skill_sync.config import (
    config_path,
)
from skill_sync.repository import read_repository_config

from tests.skill_sync_helpers import (
    make_committed_repository,
    make_local_repository,
    make_personal_config,
    run_git,
    write_legacy_config,
    write_skill,
)

if TYPE_CHECKING:
    from pathlib import Path

ARGPARSE_USAGE_ERROR = 2


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


def test_cli_install_all_uses_personal_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = make_personal_config(tmp_path)
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    write_skill(config.repository_path / "skills", "alpha-skill")
    write_skill(config.repository_path / "skills", "zeta-skill")
    assert main(["install", "--all", "--target", "codex"]) == 0
    output = capsys.readouterr()
    assert "Installed: 2; failed: 0" in output.out
    assert output.err == ""
    assert (tmp_path / "home/.agents/skills/alpha-skill").is_symlink()


@pytest.mark.parametrize("force", [False, True])
def test_cli_install_all_preserves_foreign_content_and_reports_partial_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    force: bool,
) -> None:
    config = make_personal_config(tmp_path)
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    write_skill(config.repository_path / "skills", "alpha-skill")
    write_skill(config.repository_path / "skills", "beta-skill")
    existing = tmp_path / "home/.agents/skills/alpha-skill"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("Keep")
    args = ["install", "--all", "--target", "codex"]
    if force:
        args.append("--force")
    assert main(args) == 1
    output = capsys.readouterr()
    assert "Installed skill: beta-skill" in output.out
    assert "Installed: 1; failed: 1" in output.out
    assert "preserved" in output.err
    assert not existing.is_symlink()
    assert (existing / "keep.txt").read_text() == "Keep"
    assert (existing.parent / "beta-skill").is_symlink()


def test_cli_install_requires_skill_name_or_all(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["install", "--target", "codex"]) == 1

    output = capsys.readouterr()
    assert "Pass a skill name or --all." in output.err


def test_cli_add_and_install_managed_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = make_personal_config(tmp_path)
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    original = write_skill(tmp_path / "home" / ".agents" / "skills")

    assert main(["add", str(original)]) == 0
    output = capsys.readouterr()
    assert "Added skill: demo-skill" in output.out
    assert f"path: {original}" in output.out
    assert original.is_symlink()
    assert main(["add", str(original)]) == 0
    assert "Skill already managed: demo-skill" in capsys.readouterr().out
    assert main(["install", "demo-skill", "--target", "codex"]) == 0
    output = capsys.readouterr()
    assert "Installed skill: demo-skill" in output.out
    assert output.err == ""


def test_cli_add_conflict_returns_error_without_moving_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = make_personal_config(tmp_path)
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    write_skill(config.repository_path / "skills")
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
    absorbed = write_skill(tmp_path / "external", "absorbed-skill")
    assert invoke("add", str(absorbed)).returncode == 0
    assert absorbed.is_symlink()
    result = invoke("install", "demo-skill", "--target", "codex")
    assert result.returncode == 0, result.stderr
    installed = tmp_path / "home/.agents/skills/demo-skill"
    assert installed.is_symlink()
    (installed / "notes.md").write_text("Edited through installed path")
    assert (
        repository / "skills/demo-skill/notes.md"
    ).read_text() == "Edited through installed path"
    assert "installations: 2" in invoke("status").stdout
    assert config_path(state).read_bytes() == config_before
    assert (state / "installations.json").exists()
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
    registry = make_committed_repository(tmp_path)
    write_skill(registry.path / "skills")
    run_git(["add", "skills"], cwd=registry.path)
    run_git(["commit", "-m", "Add skill manually"], cwd=registry.path)
    run_git(["push"], cwd=registry.path)
    clone = tmp_path / "clone"
    run_git(["clone", str(registry.remote), str(clone)], cwd=None)
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
    write_legacy_config(state)
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
