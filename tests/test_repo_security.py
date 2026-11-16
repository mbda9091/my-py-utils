from __future__ import annotations

import hashlib
import json
import os
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest
from repo_security import resources
from repo_security.core import repository_root, setup_repository
from repo_security.errors import RepoSecurityError
from repo_security.main import main
from repo_security.resources import template_files, write_template

ERROR_EXIT = 2


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    root = tmp_path / "测试 repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Security Tests")
    git(root, "config", "user.email", "test@example.com")
    return root


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- tests use a throwaway repository, no shell
        ["git", "-C", str(root), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=check,
        timeout=120,
    )


def test_bundled_resources_are_complete_and_checksums_match() -> None:
    root = files("repo_security").joinpath("templates")
    manifest = json.loads(root.joinpath("manifest.json").read_text())
    destinations = {resource.destination for resource in template_files(github_actions=True)}
    assert ".security/uv.lock" in destinations
    assert ".github/workflows/repo-security.yml" in destinations
    assert len(destinations) == len(manifest["files"])
    for entry in manifest["files"]:
        content = root.joinpath(*entry["resource"].split("/")).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
        if entry["destination"].endswith(".py"):
            compile(content, entry["destination"], "exec")
    assert all(not name.endswith((".venv", "/bin")) for name in destinations)


def test_template_export_is_exclusive_and_preserves_existing_files(tmp_path: Path) -> None:
    write_template(tmp_path)
    custom = tmp_path / ".security/policy.json"
    custom.write_text("custom\n")
    with pytest.raises(FileExistsError):
        write_template(tmp_path)
    assert custom.read_text() == "custom\n"
    assert not (tmp_path / ".github").exists()
    assert os.access(tmp_path / ".security/run", os.X_OK)


def test_corrupt_resource_is_rejected_before_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / "bundle"
    templates = bundle / "templates"
    templates.mkdir(parents=True)
    source = files("repo_security").joinpath("templates")
    templates.joinpath("manifest.json").write_bytes(source.joinpath("manifest.json").read_bytes())
    templates.joinpath("setup.py.template").write_bytes(b"corrupt")

    def bundled_files(_package: str) -> Path:
        return bundle

    monkeypatch.setattr(resources, "files", bundled_files)
    destination = tmp_path / "target"
    destination.mkdir()
    with pytest.raises(RepoSecurityError, match="checksum mismatch"):
        write_template(destination)
    assert not (destination / ".security").exists()


def test_repository_root_accepts_subdirectory(repository: Path) -> None:
    subdirectory = repository / "src"
    subdirectory.mkdir()
    assert repository_root(subdirectory) == repository


def test_invalid_repository_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RepoSecurityError, match="non-bare"):
        repository_root(tmp_path)


@pytest.mark.parametrize(
    "kind",
    ["existing", "symlink", "ignore-symlink", "hook", "hooks-path", "global-hooks-path"],
)
def test_setup_conflicts_do_not_write(
    repository: Path,
    tmp_path: Path,
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    security = repository / ".security"
    ignore = repository / ".gitignore"
    ignore.write_text("existing\n")
    if kind == "existing":
        security.mkdir()
    elif kind == "symlink":
        security.symlink_to(tmp_path / "missing", target_is_directory=True)
    elif kind == "ignore-symlink":
        original = tmp_path / "ignore"
        original.write_text("existing\n")
        ignore.unlink()
        ignore.symlink_to(original)
    elif kind == "hook":
        (repository / ".git/hooks/pre-commit").write_text("#!/bin/sh\nexit 0\n")
    elif kind == "hooks-path":
        git(repository, "config", "core.hooksPath", ".husky")
    else:
        config = tmp_path / "global.gitconfig"
        config.write_text("[core]\n\thooksPath = .husky\n")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    before = ignore.read_bytes()
    with pytest.raises((RepoSecurityError, subprocess.CalledProcessError)):
        setup_repository(repository)
    assert ignore.read_bytes() == before
    if kind not in ("existing", "symlink"):
        assert not security.exists()


def test_cli_missing_runtime_returns_error(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["scan", str(repository)]) == ERROR_EXIT
    assert "runtime is missing" in capsys.readouterr().err


@pytest.fixture
def real_scanners(repository: Path) -> Path:
    location = os.environ.get("REPO_SECURITY_TEST_RUNTIME")
    if not location:
        pytest.skip("Set REPO_SECURITY_TEST_RUNTIME to a real installed .security directory")
    runtime = Path(location)
    assert (runtime / ".venv/bin/python").is_file()
    assert (runtime / "bin/gitleaks").is_file()
    write_template(repository)
    security = repository / ".security"
    (security / ".venv").symlink_to(runtime / ".venv", target_is_directory=True)
    (security / "bin").symlink_to(runtime / "bin", target_is_directory=True)
    git(repository, "config", "core.hooksPath", ".security/hooks")
    return repository


@pytest.mark.parametrize("category", ["clean", "gitleaks", "semgrep", "presidio"])
def test_real_scanners_and_commit_hooks(real_scanners: Path, category: str) -> None:
    root = real_scanners
    secret = "ghp_" + "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY3zA5"
    phone = "139" + "12345678"
    samples = {
        "clean": ("print('safe')\n", "PASS"),
        "gitleaks": (f'token = "{secret}"\n', "Gitleaks:"),
        "semgrep": ("result = eval(user_input)  # nosemgrep\n", "Semgrep:"),
        "presidio": (f'phone = "{phone}"\n', "Presidio:"),
    }
    sample, expected = samples[category]
    path = root / "sample.py"
    path.write_text(sample)
    git(root, "add", "sample.py")
    # The staged version, rather than this safe working copy, must be scanned.
    path.write_text("print('safe working copy')\n")
    result = git(root, "commit", "-m", "Synthetic sample", check=False)
    output = result.stdout + result.stderr
    assert (result.returncode == 0) == (category == "clean"), output
    assert expected in output
    assert secret not in output
    assert phone not in output
    assert "eval(user_input)" not in output
    assert path.read_text() == "print('safe working copy')\n"
    assert git(root, "show", ":sample.py").stdout == sample


def test_real_scanner_configuration_failure_blocks(real_scanners: Path) -> None:
    root = real_scanners
    (root / "sample.py").write_text("print('safe')\n")
    git(root, "add", "sample.py")
    (root / ".security/semgrep.yml").write_text("rules: [invalid]\n")
    assert main(["scan", str(root)]) == ERROR_EXIT
