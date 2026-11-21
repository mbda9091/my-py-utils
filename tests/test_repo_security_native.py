from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest
from repo_security.core import setup_repository
from repo_security.errors import RepoSecurityError
from repo_security.main import main
from repo_security.resources import write_template
from repo_security.templates import hook_config, manage

from tests import test_repo_security as fixtures
from tests import test_repo_security_install as installation
from tests.test_repo_security import git

if TYPE_CHECKING:
    from pathlib import Path

repository = fixtures.repository
fake_install = installation.fake_install
ERROR_EXIT = 2
pytestmark = pytest.mark.usefixtures("fake_install")


def executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nset -eu\n" + body)
    path.chmod(0o755)


def test_native_install_is_unique_and_does_not_create_dispatcher(repository: Path) -> None:
    setup_repository(repository)
    assert not (repository / ".git/hooks/pre-commit").exists()
    assert not (repository / ".git/hooks/pre-commit.d/10-security").exists()
    assert git(repository, "hook", "list", "-z", "pre-commit").stdout == "repo-security\0"
    before = installation.snapshot(repository)
    setup_repository(repository)
    assert installation.snapshot(repository) == before
    record = manage.read_record(repository)
    assert record
    assert record["schema_version"] == hook_config.SCHEMA_VERSION
    assert record["status"] == "ready"


def test_setup_appends_our_registration_and_preserves_other_order(repository: Path) -> None:
    for name in ("first", "second"):
        git(repository, "config", f"hook.{name}.command", ":")
        git(repository, "config", "--add", f"hook.{name}.event", "pre-commit")
    setup_repository(repository)
    git(repository, "config", "hook.later.command", ":")
    git(repository, "config", "--add", "hook.later.event", "pre-commit")
    assert git(repository, "hook", "list", "-z", "pre-commit").stdout.split("\0")[:-1] == [
        "first",
        "second",
        "repo-security",
        "later",
    ]
    setup_repository(repository)
    assert git(repository, "hook", "list", "-z", "pre-commit").stdout.split("\0")[:-1] == [
        "first",
        "second",
        "later",
        "repo-security",
    ]
    before = installation.snapshot(repository)
    setup_repository(repository)
    assert installation.snapshot(repository) == before


@pytest.mark.parametrize("custom_path", [False, True])
def test_existing_traditional_hook_and_hooks_path_are_preserved(
    repository: Path, *, custom_path: bool
) -> None:
    directory = repository / ("project hooks" if custom_path else ".git/hooks")
    if custom_path:
        git(repository, "config", "core.hooksPath", "project hooks")
    entry = directory / "pre-commit"
    executable(entry, "echo project >> .git/trace\n")
    before = entry.read_bytes(), entry.stat().st_mode, entry.stat().st_mtime_ns
    setup_repository(repository)
    assert (entry.read_bytes(), entry.stat().st_mode, entry.stat().st_mtime_ns) == before
    assert git(repository, "config", "--get", "core.hooksPath", check=False).stdout.strip() == (
        "project hooks" if custom_path else ""
    )
    assert git(repository, "hook", "list", "pre-commit").returncode == 0


def test_other_tools_name_collision_gets_stable_independent_registration(repository: Path) -> None:
    git(repository, "config", "hook.repo-security.command", "echo other-tool")
    git(repository, "config", "hook.repo-security.event", "pre-commit")
    setup_repository(repository)
    setup_repository(repository)
    assert (
        git(repository, "config", "--get", "hook.repo-security.command").stdout.strip()
        == "echo other-tool"
    )
    assert (
        git(repository, "hook", "list", "-z", "pre-commit").stdout
        == "repo-security\0repo-security-2\0"
    )


@pytest.mark.parametrize("key", ["hook.pre-commit.enabled", "hook.repo-security.enabled"])
def test_disabled_event_or_owned_hook_is_preserved_and_reported(repository: Path, key: str) -> None:
    setup_repository(repository)
    git(repository, "config", key, "false")
    before = installation.snapshot(repository)
    with pytest.raises(RepoSecurityError, match="disabled"):
        setup_repository(repository)
    assert installation.snapshot(repository) == before


def test_global_hooks_path_and_unrelated_disabled_hook_are_preserved(
    repository: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text(
        '[core]\n\thooksPath = global-hooks\n[hook "disabled-project"]\n'
        "\tcommand = echo must-not-run\n\tevent = pre-commit\n\tenabled = false\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    before = global_config.read_bytes(), global_config.stat().st_mtime_ns
    setup_repository(repository)
    assert (global_config.read_bytes(), global_config.stat().st_mtime_ns) == before
    assert git(repository, "config", "--get", "core.hooksPath").stdout.strip() == "global-hooks"
    assert (
        git(repository, "config", "--get", "hook.disabled-project.enabled").stdout.strip()
        == "false"
    )


def test_config_drift_during_publication_preserves_external_changes(
    repository: Path, monkeypatch: pytest.MonkeyPatch, fake_install: list[Path]
) -> None:
    original = hook_config.hooks_state

    def state(root: Path) -> hook_config.HooksState:
        # Git's config lock exists only during the publication checkpoint.
        if (root / ".git/config.lock").exists():
            with (root / ".git/config").open("a") as stream:
                stream.write('[hook "external"]\n\tcommand = :\n\tevent = pre-commit\n')
        return original(root)

    monkeypatch.setattr(hook_config, "hooks_state", state)
    with pytest.raises(RepoSecurityError, match="changed during setup"):
        setup_repository(repository)
    assert git(repository, "config", "--get", "hook.external.command").stdout.strip() == ":"
    assert not git(repository, "config", "--get", "hook.repo-security.command", check=False).stdout
    assert not fake_install


def test_unrelated_branch_metadata_change_during_install_is_preserved(
    repository: Path, monkeypatch: pytest.MonkeyPatch, fake_install: list[Path]
) -> None:
    original = manage.install_runtime

    def install(root: Path, record: manage.InstallRecord) -> None:
        original(root, record)
        git(root, "config", "branch.main.vscode-merge-base", "origin/main")

    monkeypatch.setattr(manage, "install_runtime", install)
    setup_repository(repository)
    record = manage.read_record(repository)
    assert record
    assert record["status"] == "ready"
    assert (
        git(repository, "config", "--get", "branch.main.vscode-merge-base").stdout.strip()
        == "origin/main"
    )
    assert fake_install == [repository]


def test_worktree_configuration_is_isolated(repository: Path) -> None:
    git(repository, "commit", "--allow-empty", "-qm", "baseline")
    git(repository, "config", "extensions.worktreeConfig", "true")
    worktree = repository.parent / "linked checkout"
    git(repository, "worktree", "add", "--detach", str(worktree))
    common_before = (repository / ".git/config").read_bytes()
    setup_repository(worktree)
    assert (repository / ".git/config").read_bytes() == common_before
    assert git(repository, "hook", "list", "pre-commit", check=False).returncode == 1
    assert git(worktree, "hook", "list", "-z", "pre-commit").stdout == "repo-security\0"
    assert manage.read_record(worktree)


@pytest.mark.parametrize(
    "reported", ["git version 2.53.0", "bad version", "git version 2.54.0.rc1"]
)
def test_git_version_rejection_is_read_only(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    reported: str,
    capsys: pytest.CaptureFixture[str],
    fake_install: list[Path],
) -> None:
    actual = hook_config.git

    def version(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        if args == ("--version",):
            return subprocess.CompletedProcess(args, 0, reported + "\n", "")
        return actual(root, *args)

    monkeypatch.setattr(hook_config, "git", version)
    before = installation.snapshot(repository)
    assert main(["setup", str(repository), "--dry-run"]) == ERROR_EXIT
    assert "Git >= 2.54.0" in capsys.readouterr().err
    assert installation.snapshot(repository) == before
    assert not fake_install


@pytest.mark.parametrize("reported", ["2.53.0", "2.54", "2.54.0junk", "unrecognized"])
def test_shell_rejects_old_git_before_uv(repository: Path, tmp_path: Path, reported: str) -> None:
    write_template(repository)
    commands = tmp_path / "bin"
    commands.mkdir()
    executable(commands / "git", "echo 'git version " + reported + "'\n")
    marker = tmp_path / "uv-ran"
    executable(commands / "uv", f"touch '{marker}'\n")
    before = installation.snapshot(repository)
    result = subprocess.run(  # noqa: S603 -- disposable bootstrap with controlled PATH
        ["/bin/sh", str(repository / ".security/setup.sh")],
        cwd=repository,
        env={**os.environ, "PATH": str(commands)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == ERROR_EXIT
    assert "2.54.0" in result.stderr
    assert not marker.exists()
    assert installation.snapshot(repository) == before


@pytest.mark.parametrize("duplicate_event", [False, True])
def test_lost_manifest_adopts_the_same_native_registration(
    repository: Path, *, duplicate_event: bool
) -> None:
    setup_repository(repository)
    (repository / manage.MANIFEST).unlink()
    if duplicate_event:
        git(repository, "config", "--add", "hook.repo-security.event", "pre-commit")
    setup_repository(repository)
    assert git(repository, "hook", "list", "-z", "pre-commit").stdout == "repo-security\0"
    record = manage.read_record(repository)
    assert record
    assert record["status"] == "ready"


@pytest.mark.parametrize("release", ["253", "254"])
def test_real_git_release_gate_and_native_setup(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    release: str,
    tmp_path: Path,
) -> None:
    source = os.environ.get("REPO_SECURITY_GIT_" + release)
    if not source:
        pytest.skip("Set REPO_SECURITY_GIT_" + release + " to the real Git executable")
    directory = tmp_path / "git-version"
    directory.mkdir()
    (directory / "git").symlink_to(source)
    monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ["PATH"])
    before = installation.snapshot(repository)
    if release == "253":
        with pytest.raises(RepoSecurityError, match=r"2\.54\.0"):
            setup_repository(repository)
        assert installation.snapshot(repository) == before
    else:
        setup_repository(repository)
        assert git(repository, "hook", "list", "-z", "pre-commit").stdout == "repo-security\0"


@pytest.mark.parametrize("entry", ["cli", "preview", "shell", "python"])
def test_real_old_git_rejects_all_configuration_entries_without_writes(
    repository: Path,
    tmp_path: Path,
    entry: str,
) -> None:
    source = os.environ.get("REPO_SECURITY_GIT_253")
    if not source:
        pytest.skip("Set REPO_SECURITY_GIT_253 to the real Git 2.53 executable")
    import sys  # noqa: PLC0415 -- interpreter belongs to this isolated CLI test environment

    write_template(repository)
    commands = tmp_path / "old-git-bin"
    commands.mkdir()
    (commands / "git").symlink_to(source)
    env = dict(os.environ)
    env["PATH"] = str(commands) + os.pathsep + env["PATH"]
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    if entry in ("cli", "preview"):
        command = [
            sys.executable,
            "-c",
            "from repo_security.main import main; raise SystemExit(main())",
            "setup",
            str(repository),
        ]
        if entry == "preview":
            command.append("--dry-run")
    elif entry == "shell":
        command = ["/bin/sh", str(repository / ".security/setup.sh")]
    else:
        command = [sys.executable, str(repository / ".security/setup.py"), "--install-hooks"]
    before = installation.snapshot(repository)
    result = subprocess.run(  # noqa: S603 -- fixed entry points in disposable repository
        command,
        cwd=repository,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == ERROR_EXIT
    assert "2.54.0" in result.stderr
    assert "2.53.0" in result.stderr
    assert installation.snapshot(repository) == before
    assert not (repository / ".security/__pycache__").exists()


@pytest.mark.parametrize("executable_member", [False, True])
def test_setup_does_not_inspect_or_migrate_other_hook_directories(
    repository: Path, *, executable_member: bool
) -> None:
    entry = repository / ".git/hooks/pre-commit"
    executable(entry, "echo external >> .git/external-hook\n")
    directory = repository / ".git/hooks/pre-commit.d"
    directory.mkdir()
    members = [directory / "20-project", directory / ".inactive"]
    for path in members:
        executable(path, "exit 7\n")
        if not executable_member:
            path.chmod(0o644)
    extra = repository / ".security/hooks/pre-push"
    executable(extra, "exit 9\n")
    protected = [entry, *members, extra]
    before = [(p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns) for p in protected]
    setup_repository(repository)
    assert before == [(p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns) for p in protected]
    assert not (repository / ".security/hooks/migrated-project-checks").exists()
    assert not (repository / ".git/repo-security-backups").exists()
    record = manage.read_record(repository)
    assert record
    assert len(record["native_hooks"]) == 1
    assert "migration_members" not in record
    assert "migration_backup" not in record
    # Exercise the preserved external entry without invoking this fixture's fake scanners.
    git(repository, "-c", "hook.repo-security.enabled=false", "hook", "run", "pre-commit")
    assert (repository / ".git/external-hook").read_text() == "external\n"


@pytest.mark.parametrize("scope", ["local", "worktree"])
def test_setup_preserves_conflicting_runtime_hooks_path_without_cleanup(
    repository: Path, scope: str
) -> None:
    if scope == "worktree":
        git(repository, "config", "extensions.worktreeConfig", "true")
    git(repository, "config", "--" + scope, "core.hooksPath", ".security/hooks")
    before = installation.snapshot(repository)
    with pytest.raises(RepoSecurityError, match="would invoke it twice"):
        setup_repository(repository)
    assert installation.snapshot(repository) == before
