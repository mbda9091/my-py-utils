from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest
from repo_security.resources import write_template

from tests import test_repo_security as fixtures

if TYPE_CHECKING:
    from pathlib import Path

repository = fixtures.repository
ERROR_EXIT = 2
INJECTED_EXIT = 37


@pytest.fixture
def shell_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "shell commands"
    directory.mkdir()
    git_binary = shutil.which("git")
    assert git_binary
    (directory / "git").symlink_to(git_binary)
    dirname = shutil.which("dirname")
    assert dirname
    (directory / "dirname").symlink_to(dirname)
    monkeypatch.setenv("PATH", str(directory))
    return directory


def launch(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- explicit shell and reviewed script in a disposable repo
        ["/bin/sh", str(root / ".security/setup.sh"), "--help"],
        cwd=root.parent,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_missing_uv_has_actionable_error_without_installing_hooks(
    repository: Path, shell_path: Path
) -> None:
    write_template(repository)
    assert not (shell_path / "uv").exists()
    result = launch(repository)
    assert result.returncode == ERROR_EXIT
    assert "uv is required" in result.stderr
    assert "Install uv" in result.stderr
    assert not (repository / ".git/hooks/pre-commit").exists()
    assert not (repository / ".security/.venv").exists()


@pytest.mark.parametrize("status", [0, INJECTED_EXIT])
def test_shell_uses_uv_without_system_python_and_preserves_exit_status(
    repository: Path, shell_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    write_template(repository)
    capture = repository.parent / "uv-arguments.txt"
    monkeypatch.setenv("BOOTSTRAP_ARGUMENTS", str(capture))
    uv = shell_path / "uv"
    uv.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$BOOTSTRAP_ARGUMENTS"\nexit ' + str(status) + "\n"
    )
    uv.chmod(0o755)
    assert shutil.which("python") is None
    assert shutil.which("python3") is None
    result = launch(repository)
    assert result.returncode == status
    arguments = capture.read_text().splitlines()
    assert arguments[0] == "run"
    assert {"--no-project", "--no-config", "--isolated"}.issubset(arguments)
    assert arguments[arguments.index("--python") + 1] == "3.13"
    assert str(repository / ".security/setup.py") in arguments
    assert "--install-hooks" in arguments
    assert arguments[-1] == "--help"


def test_real_uv_bootstrap_ignores_business_project_and_environment(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_template(repository)
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "bootstrap-isolation-test"\nversion = "0.0.0"\n'
        'requires-python = "==3.14.*"\n'
        'dependencies = ["must-not-be-resolved-security-bootstrap"]\n'
    )
    external = repository.parent / "business-environment"
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(external))
    monkeypatch.setenv("VIRTUAL_ENV", str(external))
    result = subprocess.run(  # noqa: S603 -- real uv, help only, disposable repository
        [str(repository / ".security/setup.sh"), "--help"],
        cwd=repository,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "--install-hooks" in result.stdout
    assert not external.exists()
    assert not (repository / ".venv").exists()
    assert not (repository / "uv.lock").exists()
    assert not (repository / ".git/hooks/pre-commit").exists()
