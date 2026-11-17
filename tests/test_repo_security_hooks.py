from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from repo_security.core import setup_repository
from repo_security.errors import RepoSecurityError
from repo_security.templates import manage

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
def test_independent_check_runs_after_security_without_modifying_it(
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
        root / ".git/hooks/pre-commit.d/20-project",
        "echo 'project-check-ran' >&2\nexit " + str(int(reject)) + "\n",
    )
    (root / "sample.py").write_text("print('safe')\n")
    git(root, "add", "sample.py")
    result = git(root, "commit", "-qm", "Independent checks", check=False)
    output = result.stdout + result.stderr
    assert "PASS: 1 unique snapshots, 0 findings." in output
    assert "project-check-ran" in output
    assert (result.returncode == 0) is not reject
    assert (git(root, "rev-parse", "--verify", "HEAD", check=False).returncode == 0) is not reject
    assert security_before == {
        name: (
            (root / ".security" / name).read_bytes(),
            (root / ".security" / name).stat().st_mtime_ns,
        )
        for name in manage.RUNTIME_NAMES
    }


@pytest.mark.parametrize(
    "damage",
    ["missing-security", "broken-security", "disabled-security", "disabled-extra", "broken-extra"],
)
def test_dispatcher_blocks_unusable_checks(repository: Path, damage: str) -> None:
    root = repository
    entry = root / manage.HOOK_ENTRY
    entry.write_bytes(manage.HOOK_DISPATCHER)
    entry.chmod(0o755)
    member = root / manage.HOOK_LINK
    member.parent.mkdir()
    security = root / ".security/hooks/pre-commit"
    executable(security, "echo security >> hook-order.txt\n")
    member.symlink_to(manage.HOOK_TARGET)
    if damage == "missing-security":
        member.unlink()
    elif damage == "broken-security":
        security.unlink()
    elif damage == "disabled-security":
        security.chmod(0o644)
    elif damage == "disabled-extra":
        (member.parent / "20-extra").write_text("#!/bin/sh\nexit 0\n")
    else:
        (member.parent / "20-extra").symlink_to("missing-check")
    (root / "safe.txt").write_text("safe\n")
    git(root, "add", "safe.txt")
    result = git(root, "commit", "-qm", "Must be blocked", check=False)
    assert result.returncode != 0
    assert git(root, "rev-parse", "--verify", "HEAD", check=False).returncode != 0


def test_repeat_setup_preserves_independent_hooks(
    repository: Path, fake_install: list[Path]
) -> None:
    setup_repository(repository)
    paths = [
        repository / ".git/hooks/pre-commit.d/20-project",
        repository / ".git/hooks/commit-msg",
        repository / ".git/hooks/pre-push",
    ]
    for path in paths:
        executable(path, "exit 0\n")
    protected = [*paths, repository / manage.HOOK_ENTRY, repository / ".security/hooks/pre-commit"]
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in protected]
    link_before = (repository / manage.HOOK_LINK).lstat().st_mtime_ns
    setup_repository(repository)
    assert before == [(path.read_bytes(), path.stat().st_mtime_ns) for path in protected]
    assert (repository / manage.HOOK_LINK).lstat().st_mtime_ns == link_before
    assert manage.hooks_enabled(repository, manage.hooks_state(repository))
    assert fake_install == [repository, repository]


@pytest.mark.parametrize("relative", [manage.HOOK_ENTRY, manage.HOOK_LINK])
@pytest.mark.parametrize("kind", ["file", "link", "broken-link", "directory"])
def test_setup_preserves_conflicting_hook_entries(
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
    with pytest.raises(RepoSecurityError):
        setup_repository(repository)
    after = path.lstat()
    assert (before.st_ino, before.st_mtime_ns, before.st_mode) == (
        after.st_ino,
        after.st_mtime_ns,
        after.st_mode,
    )
    assert outside.read_text().endswith("exit 0\n")
    assert not fake_install
    assert not (repository / ".security").exists()


def test_setup_migrates_only_legacy_local_hooks_path(
    repository: Path, fake_install: list[Path]
) -> None:
    git(repository, "config", "core.hooksPath", manage.HOOKS_PATH)
    setup_repository(repository)
    state = manage.hooks_state(repository)
    assert state.effective is None
    assert not state.local
    assert manage.hooks_enabled(repository, state)
    assert str((repository / manage.HOOK_LINK).readlink()) == manage.HOOK_TARGET
    assert (repository / manage.HOOK_LINK).resolve() == repository / ".security/hooks/pre-commit"
    record = manage.read_record(repository)
    assert record
    assert record["original_local_hooks"] == [manage.HOOKS_PATH]
    assert fake_install == [repository]


def test_migration_does_not_shadow_inherited_hook_manager(
    repository: Path, fake_install: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = repository / "global.gitconfig"
    config.write_text("[core]\n\thooksPath = .external-hooks\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    git(repository, "config", "core.hooksPath", manage.HOOKS_PATH)
    with pytest.raises(RepoSecurityError, match="did not take effect"):
        setup_repository(repository)
    assert (
        git(repository, "config", "--local", "--get", "core.hooksPath").stdout.strip()
        == manage.HOOKS_PATH
    )
    assert (
        git(repository, "config", "--global", "--get", "core.hooksPath").stdout.strip()
        == ".external-hooks"
    )
    assert not fake_install
