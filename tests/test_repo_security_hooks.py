from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from repo_security.core import setup_repository
from repo_security.templates import hook_config, manage

from tests import test_repo_security as fixtures
from tests import test_repo_security_install as install_fixtures
from tests.test_repo_security import git

if TYPE_CHECKING:
    from pathlib import Path

repository = fixtures.repository
real_scanners = fixtures.real_scanners
fake_install = install_fixtures.fake_install


def executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nset -eu\n" + content)
    path.chmod(0o755)


@pytest.mark.parametrize("reject", [False, True])
def test_independent_check_runs_before_security_without_modifying_it(
    real_scanners: Path, *, reject: bool
) -> None:
    root = real_scanners
    security_before = {
        name: (
            (root / ".security" / name).read_bytes(),
            (root / ".security" / name).stat().st_mtime_ns,
        )
        for name in manage.RUNTIME_NAMES
    }
    executable(
        root / ".project-hooks/20-project",
        "echo 'project-check-ran' >&2\nexit " + str(int(reject)) + "\n",
    )
    register_project(root, "./.project-hooks/20-project")
    (root / "sample.py").write_text("print('safe')\n")
    git(root, "add", "sample.py")
    result = git(root, "commit", "-qm", "Independent checks", check=False)
    output = result.stdout + result.stderr
    assert "PASS: 1 unique snapshots, 0 findings." in output
    assert "project-check-ran" in output
    if not reject:
        assert output.index("project-check-ran") < output.index("Checking staged snapshot")
        assert output.count("Checking staged snapshot") == 1
    assert (result.returncode == 0) is not reject
    assert (git(root, "rev-parse", "--verify", "HEAD", check=False).returncode == 0) is not reject
    assert security_before == {
        name: (
            (root / ".security" / name).read_bytes(),
            (root / ".security" / name).stat().st_mtime_ns,
        )
        for name in manage.RUNTIME_NAMES
    }


@pytest.mark.parametrize("name", ["00-generator", "20-generator", "99-generator"])
def test_index_changes_from_other_hooks_are_scanned_before_commit(
    real_scanners: Path, name: str
) -> None:
    root = real_scanners
    phone = "139" + "12345678"
    (root / "sample.py").write_text("print('safe before generator')\n")
    git(root, "add", "sample.py")
    git(root, "commit", "--no-verify", "-qm", "Generator baseline")
    before = git(root, "rev-parse", "HEAD").stdout
    (root / "ordinary.txt").write_text("safe user change\n")
    git(root, "add", "ordinary.txt")
    executable(
        root / ".project-hooks" / name,
        "printf '%s\\n' 'phone = \"" + phone + "\"' > sample.py\ngit add -- sample.py\n",
    )
    register_project(root, "./.project-hooks/" + name)
    result = git(root, "commit", "-qm", "Generated secret must be blocked", check=False)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "CN_MOBILE_PHONE" in output
    assert phone not in output
    assert output.count("Checking staged snapshot") == 1
    assert git(root, "rev-parse", "HEAD").stdout == before


@pytest.mark.parametrize("damage", ["missing-security", "disabled-security"])
def test_native_registration_blocks_unusable_security_entry(repository: Path, damage: str) -> None:
    path = repository / ".security/hooks/pre-commit"
    executable(path, "exit 0\n")
    git(repository, "config", "hook.repo-security.command", "./.security/hooks/pre-commit")
    git(repository, "config", "hook.repo-security.event", "pre-commit")
    if damage == "missing-security":
        path.unlink()
    else:
        path.chmod(0o644)
    (repository / "safe.txt").write_text("safe\n")
    git(repository, "add", "safe.txt")
    assert git(repository, "commit", "-qm", "Must block", check=False).returncode != 0
    assert git(repository, "rev-parse", "--verify", "HEAD", check=False).returncode != 0


def test_repeat_setup_preserves_independent_hooks(
    repository: Path, fake_install: list[Path]
) -> None:
    setup_repository(repository)
    paths = [
        repository / ".git/hooks/pre-commit",
        repository / ".git/hooks/commit-msg",
        repository / ".git/hooks/pre-push",
    ]
    for path in paths:
        executable(path, "exit 0\n")
    protected = [*paths, repository / ".security/hooks/pre-commit"]
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in protected]
    setup_repository(repository)
    assert before == [(path.read_bytes(), path.stat().st_mtime_ns) for path in protected]
    assert manage.hooks_enabled(repository, manage.hooks_state(repository))
    assert fake_install == [repository, repository]


@pytest.mark.parametrize(
    "relative", [".git/hooks/pre-commit", ".git/hooks/pre-commit.d/10-security"]
)
@pytest.mark.parametrize("kind", ["file", "link", "broken-link", "directory"])
def test_setup_preserves_other_tools_hook_entries(
    repository: Path, relative: str, kind: str, fake_install: list[Path]
) -> None:
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    outside = repository / "external-hook"
    executable(outside, "exit 0\n")
    if kind == "file":
        executable(path, "echo existing-hook\n")
    elif kind in ("link", "broken-link"):
        path.symlink_to(outside if kind == "link" else repository / "missing-hook")
    else:
        path.mkdir()
    before = path.lstat()
    setup_repository(repository)
    after = path.lstat()
    assert (before.st_ino, before.st_mtime_ns, before.st_mode) == (
        after.st_ino,
        after.st_mtime_ns,
        after.st_mode,
    )
    assert outside.read_text().endswith("exit 0\n")
    assert fake_install == [repository]
    assert (repository / ".security").exists()


def register_project(root: Path, command: str) -> None:
    git(root, "config", "hook.project.command", command)
    git(root, "config", "hook.project.event", "pre-commit")
    record = manage.read_record(root)
    assert record
    hook_config.activate_hooks(root, hook_config.plan_hooks(root, record))
