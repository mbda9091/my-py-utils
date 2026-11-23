from __future__ import annotations

import importlib.util
import re
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from skill_sync.cli import main
from skill_sync.repository import init_repository

from tests.skill_sync_helpers import make_local_repository, write_legacy_config, write_skill

if TYPE_CHECKING:
    from pathlib import Path

USAGE_ERROR = 2
REMOVED_COMMANDS = [
    ["publish"],
    ["publish", "missing", "--update", "--no-push"],
    ["sync"],
    ["init", "git@example.com:me/skills.git"],
    ["init", "--repo", "missing", "--branch", "main"],
    ["init", "--repo", "missing", "--git-name", "Old User", "--git-email", "old@example.com"],
    ["install", "--all", "--target", "unknown"],
]


def snapshot(root: Path) -> dict[str, bytes | str]:
    return {
        str(path.relative_to(root)): str(path.readlink())
        if path.is_symlink()
        else path.read_bytes()
        for path in root.rglob("*")
        if path.is_symlink() or path.is_file()
    }


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("args", REMOVED_COMMANDS)
def test_obsolete_commands_and_options_have_no_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    *,
    legacy: bool,
) -> None:
    repository = make_local_repository(tmp_path)
    state = tmp_path / "state"
    monkeypatch.setenv("SKILL_SYNC_HOME", str(state))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    if legacy:
        write_legacy_config(state)
    else:
        init_repository(repository, state_home=state)
    write_skill(repository / "skills")
    before = snapshot(tmp_path)
    process = Mock(side_effect=AssertionError("Rejected syntax must not run any process"))
    monkeypatch.setattr("skill_sync.repository.subprocess.run", process)

    with pytest.raises(SystemExit) as error:
        main(args)

    assert error.value.code == USAGE_ERROR
    assert snapshot(tmp_path) == before
    process.assert_not_called()
    message = capsys.readouterr().err
    if args[0] in {"publish", "sync"}:
        assert "has been removed" in message
        assert "Manage Git commits and remotes yourself" in message
    else:
        assert "error:" in message


def test_help_only_exposes_local_management_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    choices = re.search(r"\{([^}]+)\}", output)
    assert choices is not None
    assert set(choices.group(1).split(",")) == {
        "init",
        "add",
        "install",
        "uninstall",
        "list",
        "status",
        "validate",
    }
    assert "publish" not in output


def test_all_local_commands_only_execute_fixed_readonly_root_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = make_local_repository(tmp_path)
    state = tmp_path / "state"
    home = tmp_path / "home"
    monkeypatch.setenv("SKILL_SYNC_HOME", str(state))
    monkeypatch.setenv("HOME", str(home))
    manual = write_skill(repository / "skills", "manual-skill")
    absorbed = write_skill(tmp_path / "external", "absorbed-skill")
    vendor = write_skill(home / ".agents/skills", "vendor-skill")
    vendor_before = snapshot(vendor)
    git_before = snapshot(repository / ".git")
    execute = subprocess.run
    calls: list[list[str]] = []

    def readonly(
        command: list[str],
        *,
        cwd: Path,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert command[1:] == ["--no-optional-locks", "rev-parse", "--show-toplevel"]
        assert capture_output
        assert text
        assert not check
        calls.append(command)
        return execute(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )

    monkeypatch.setattr("skill_sync.repository.subprocess.run", readonly)
    for args in (
        ["init", "--repo", str(repository)],
        ["add", str(absorbed)],
        ["install", "absorbed-skill"],
        ["install", "--all"],
        ["list"],
        ["status"],
        ["uninstall", "absorbed-skill"],
        ["install", "absorbed-skill"],
        ["validate", str(manual)],
    ):
        assert main(args) == 0
    assert calls
    assert snapshot(repository / ".git") == git_before
    assert snapshot(vendor) == vendor_before
    assert absorbed.is_symlink()
    assert (home / ".agents/skills/absorbed-skill").is_symlink()


@pytest.mark.parametrize("module", ["git_repo", "publisher", "security"])
def test_removed_runtime_modules_are_absent(module: str) -> None:
    assert importlib.util.find_spec(f"skill_sync.{module}") is None
