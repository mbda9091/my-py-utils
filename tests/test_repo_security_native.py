from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from repo_security.core import setup_repository
from repo_security.errors import RepoSecurityError
from repo_security.main import main
from repo_security.resources import template_files, write_template
from repo_security.templates import hook_config, manage

from tests import test_repo_security as fixtures
from tests import test_repo_security_install as installation
from tests.test_repo_security import git

repository = fixtures.repository
fake_install = installation.fake_install
GOLDEN = Path(__file__).parent / "fixtures/repo-security/v0.2.0"
ERROR_EXIT = 2
PROJECT_FAILURE = 7
pytestmark = pytest.mark.usefixtures("fake_install")


def executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nset -eu\n" + body)
    path.chmod(0o755)


def write_legacy(root: Path) -> manage.InstallRecord:
    write_template(root)
    (root / ".gitignore").write_text("\n".join(manage.IGNORE_ENTRIES) + "\n")
    for destination, source in (
        ("manage.py", "manage.py.txt"),
        ("setup.sh", "setup.sh.template"),
        ("setup.py", "setup.py.template"),
        ("hooks/pre-commit", "hooks/pre-commit.template"),
    ):
        (root / ".security" / destination).write_bytes((GOLDEN / source).read_bytes())
    (root / ".security/hook_config.py").unlink()
    record: manage.InstallRecord = {
        "schema_version": 1,
        "template_version": "0.2.0",
        "status": "ready",
        "phase": "ready",
        "files": {},
        "original_local_hooks": [],
        "gitleaks": {},
    }
    for resource in template_files():
        path = root / resource.destination
        if path.exists():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            record["files"][resource.destination] = {
                "template_sha256": digest,
                "installed_sha256": digest,
                "executable": resource.executable,
            }
    entry = root / manage.HOOK_ENTRY
    entry.write_bytes((GOLDEN / "dispatcher-current.sh").read_bytes())
    entry.chmod(0o755)
    member = root / manage.HOOK_LINK
    member.parent.mkdir()
    member.symlink_to(manage.HOOK_TARGET)
    manage.save_record(root, record)
    return record


def test_native_install_is_unique_and_does_not_create_dispatcher(repository: Path) -> None:
    setup_repository(repository)
    assert not (repository / manage.HOOK_ENTRY).exists()
    assert not (repository / manage.HOOK_LINK).exists()
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


def test_old_dispatcher_migrates_to_native_and_preserves_checks(repository: Path) -> None:
    write_legacy(repository)
    checks: list[Path] = []
    for name in ("00 first", "90-第二个"):
        path = repository / ".git/hooks/pre-commit.d" / name
        executable(path, "echo " + name.split()[0] + " >> .git/project-order\n")
        checks.append(path)
    before = [(path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns) for path in checks]
    setup_repository(repository)
    assert not (repository / manage.HOOK_ENTRY).exists()
    assert not (repository / manage.HOOK_LINK).is_symlink()
    assert before == [
        (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns) for path in checks
    ]
    assert (
        git(repository, "hook", "list", "-z", "pre-commit").stdout
        == "repo-security-project-checks\0repo-security\0"
    )
    result = subprocess.run(  # noqa: S603 -- reviewed generated script, disposable fixture
        [str(repository / hook_config.MIGRATED_CHECKS)], cwd=repository, check=False
    )
    assert result.returncode == 0
    assert (repository / ".git/project-order").read_text().splitlines() == ["00", "90-第二个"]
    after = installation.snapshot(repository)
    setup_repository(repository)
    assert installation.snapshot(repository) == after


def test_migrated_checks_preserve_failure_short_circuit(repository: Path) -> None:
    write_legacy(repository)
    executable(repository / ".git/hooks/pre-commit.d/00-fail", "echo first >> .git/order\nexit 7\n")
    executable(repository / ".git/hooks/pre-commit.d/90-later", "echo later >> .git/order\n")
    setup_repository(repository)
    result = subprocess.run(  # noqa: S603 -- generated migration script in disposable fixture
        [str(repository / hook_config.MIGRATED_CHECKS)], cwd=repository, check=False
    )
    assert result.returncode == PROJECT_FAILURE
    assert (repository / ".git/order").read_text() == "first\n"


def test_legacy_hooks_path_migration_preserves_other_events(repository: Path) -> None:
    write_legacy(repository)
    git(repository, "config", "core.hooksPath", manage.HOOKS_PATH)
    script = repository / ".security/hooks/commit-msg"
    executable(script, 'printf "%s\\n" "$@" > .git/message-args\n')
    executable(repository / ".security/hooks/helper.sh", "exit 88\n")
    (repository / ".security/hooks/post-commit").write_text("#!/bin/sh\nexit 89\n")
    before = script.read_bytes(), script.stat().st_mode, script.stat().st_mtime_ns
    setup_repository(repository)
    assert git(repository, "config", "--get", "core.hooksPath", check=False).returncode == 1
    assert (
        git(repository, "hook", "list", "-z", "commit-msg").stdout
        == "repo-security-project-commit-msg\0"
    )
    git(repository, "hook", "run", "commit-msg", "--", "message with spaces")
    assert (repository / ".git/message-args").read_text() == "message with spaces\n"
    assert (script.read_bytes(), script.stat().st_mode, script.stat().st_mtime_ns) == before
    record = manage.read_record(repository)
    assert record
    assert "native_hooks" in record
    assert [item["event"] for item in record["native_hooks"]] == ["commit-msg", "pre-commit"]


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


def test_inactive_legacy_project_checks_are_not_enabled_by_migration(repository: Path) -> None:
    write_legacy(repository)
    executable(repository / ".git/hooks/pre-commit.d/20-inactive", "exit 9\n")
    git(repository, "config", "core.hooksPath", "other-hooks")
    executable(repository / "other-hooks/pre-commit", "exit 0\n")
    setup_repository(repository)
    assert (repository / ".git/hooks/pre-commit.d/20-inactive").is_file()
    assert not (repository / hook_config.MIGRATED_CHECKS).exists()
    record = manage.read_record(repository)
    assert record
    assert "migration_members" in record
    assert record["migration_members"] == []
    assert git(repository, "config", "--get", "core.hooksPath").stdout.strip() == "other-hooks"


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


def test_repeat_setup_moves_only_security_not_migrated_project_checks(repository: Path) -> None:
    write_legacy(repository)
    executable(repository / ".git/hooks/pre-commit.d/20-project", "exit 0\n")
    setup_repository(repository)
    git(repository, "config", "hook.later-project.command", ":")
    git(repository, "config", "hook.later-project.event", "pre-commit")
    setup_repository(repository)
    assert git(repository, "hook", "list", "-z", "pre-commit").stdout.split("\0")[:-1] == [
        "repo-security-project-checks",
        "later-project",
        "repo-security",
    ]


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
