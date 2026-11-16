from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from repo_security.errors import RepoSecurityError
from repo_security.resources import template_files, write_template

IGNORE_ENTRIES = ("/.security/.venv/", "/.security/bin/", "/.security/__pycache__/")


def repository_root(path: Path) -> Path:
    if not path.is_dir():
        msg = "Target must be an existing Git repository directory"
        raise RepoSecurityError(msg)
    result = subprocess.run(  # noqa: S603 -- fixed git subcommand, no shell
        ["git", "-C", str(path.resolve()), "rev-parse", "--show-toplevel"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        msg = "Target must be a non-bare Git repository"
        raise RepoSecurityError(msg)
    root = Path(result.stdout.strip())
    if not (root / ".git").is_dir() or (root / ".git").is_symlink():
        msg = "Linked worktrees and submodule checkouts are not supported for setup"
        raise RepoSecurityError(msg)
    return root


def setup_repository(path: Path) -> Path:
    root = repository_root(path)
    if shutil.which("uv") is None:
        msg = "Install uv before running setup"
        raise RepoSecurityError(msg)
    security = root / ".security"
    if security.exists() or security.is_symlink():
        msg = "M1 setup only accepts a fresh .security directory; existing files were not changed"
        raise RepoSecurityError(msg)
    ignore = root / ".gitignore"
    if ignore.is_symlink() or (ignore.exists() and not ignore.is_file()):
        msg = ".gitignore must be a regular file"
        raise RepoSecurityError(msg)
    # Preflight using the same standalone installer before any target writes.
    installer = next(
        resource.content.decode("utf-8")
        for resource in template_files()
        if resource.destination == ".security/setup.py"
    )
    preflight = subprocess.run(  # noqa: S603 -- bundled installer, no shell
        [sys.executable, "-c", installer, "--check-hooks", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if preflight.returncode:
        raise RepoSecurityError(preflight.stderr.strip() or "Hooks preflight failed")
    original = ignore.read_bytes().decode("utf-8") if ignore.exists() else ""
    additions = [entry for entry in IGNORE_ENTRIES if entry not in original.splitlines()]
    write_template(root)
    if additions:
        separator = "\n" if original and not original.endswith("\n") else ""
        ignore.write_text(original + separator + "\n".join(additions) + "\n", encoding="utf-8")
    subprocess.run(  # noqa: S603 -- repository-local copy of the verified bundled installer
        [sys.executable, str(security / "setup.py"), "--install-hooks"],
        check=True,
    )
    return root


def scan_repository(path: Path, mode: str) -> int:
    root = repository_root(path)
    runner = root / ".security/run"
    if not runner.is_file() or runner.is_symlink():
        msg = "Security runtime is missing; run setup first"
        raise RepoSecurityError(msg)
    result = subprocess.run([str(runner), mode], cwd=root, check=False)  # noqa: S603
    return result.returncode if result.returncode in (0, 1) else 2
