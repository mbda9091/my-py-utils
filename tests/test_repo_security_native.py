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


@pytest.mark.parametrize("scope", ["local", "worktree"])
def test_legacy_hooks_path_is_cleared_from_its_actual_scope(
    repository: Path, scope: str
) -> None:
    write_legacy(repository)
    git(repository, "config", "extensions.worktreeConfig", "true")
    git(repository, "config", "--local", "review.shared", "preserved")
    git(repository, "config", "--worktree", "review.private", "preserved")
    git(repository, "config", "--" + scope, "core.hooksPath", manage.HOOKS_PATH)
    original_configs = {
        name: (repository / ".git" / name).read_bytes()
        for name in ("config", "config.worktree")
    }
    setup_repository(repository)
    for selected in ("local", "worktree"):
        assert (
            git(repository, "config", "--" + selected, "--get", "core.hooksPath", check=False)
            .returncode
            == 1
        )
    assert git(repository, "config", "--get", "core.hooksPath", check=False).returncode == 1
    assert git(repository, "config", "--get", "review.shared").stdout.strip() == "preserved"
    assert git(repository, "config", "--get", "review.private").stdout.strip() == "preserved"
    assert not (repository / manage.HOOK_ENTRY).exists()
    assert not (repository / manage.HOOK_LINK).is_symlink()
    assert git(repository, "hook", "list", "-z", "pre-commit").stdout == "repo-security\0"
    assert manage.hooks_enabled(repository, manage.hooks_state(repository))
    record = manage.read_record(repository)
    assert record
    assert record["status"] == "ready"
    assert "hook_scope" in record
    assert record["hook_scope"] == "worktree"
    assert "migration_backup" in record
    backup = repository / record["migration_backup"] / "files/.git"
    for name, content in original_configs.items():
        assert (backup / name).read_bytes() == content
    before = installation.snapshot(repository)
    setup_repository(repository)
    assert installation.snapshot(repository) == before


def test_shared_legacy_config_lock_prevents_partial_publication(repository: Path) -> None:
    write_legacy(repository)
    git(repository, "config", "extensions.worktreeConfig", "true")
    git(repository, "config", "--local", "core.hooksPath", manage.HOOKS_PATH)
    lock = repository / ".git/config.lock"
    lock.write_text("another writer\n")
    common = (repository / ".git/config").read_bytes()
    with pytest.raises(RepoSecurityError, match="locked by another writer"):
        setup_repository(repository)
    assert (repository / ".git/config").read_bytes() == common
    assert lock.read_text() == "another writer\n"
    assert not (repository / ".git/config.worktree").exists()
    assert not (repository / ".git/config.worktree.lock").exists()
    record = manage.read_record(repository)
    assert record
    assert record["status"] == "incomplete"
    lock.unlink()
    setup_repository(repository)
    assert git(repository, "config", "--get", "core.hooksPath", check=False).returncode == 1
    assert manage.hooks_enabled(repository, manage.hooks_state(repository))


def test_shared_legacy_config_change_is_preserved_before_publication(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_legacy(repository)
    git(repository, "config", "extensions.worktreeConfig", "true")
    git(repository, "config", "--local", "core.hooksPath", manage.HOOKS_PATH)
    original = hook_config.hooks_state

    def changed(root: Path) -> hook_config.HooksState:
        if (root / ".git/config.lock").exists():
            with (root / ".git/config").open("a") as stream:
                stream.write("[review]\n\texternal = preserved\n")
        return original(root)

    monkeypatch.setattr(hook_config, "hooks_state", changed)
    with pytest.raises(RepoSecurityError, match="changed during setup"):
        setup_repository(repository)
    assert git(repository, "config", "--get", "review.external").stdout.strip() == "preserved"
    assert git(repository, "config", "--get", "core.hooksPath").stdout.strip() == manage.HOOKS_PATH
    assert not (repository / ".git/config.worktree").exists()
    assert not (repository / ".git/config.lock").exists()
    assert not (repository / ".git/config.worktree.lock").exists()


def test_shared_legacy_config_publication_failure_is_retryable(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_legacy(repository)
    git(repository, "config", "extensions.worktreeConfig", "true")
    git(repository, "config", "--local", "core.hooksPath", manage.HOOKS_PATH)
    original = Path.replace
    failed = False

    def replace(source: Path, target: str | Path) -> Path:
        nonlocal failed
        if source == repository / ".git/config.lock" and not failed:
            failed = True
            msg = "Synthetic shared configuration publication failure"
            raise OSError(msg)
        return original(source, target)

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(OSError, match="publication failure"):
        setup_repository(repository)
    record = manage.read_record(repository)
    assert record
    assert record["status"] == "incomplete"
    assert git(repository, "config", "--get", "core.hooksPath").stdout.strip() == manage.HOOKS_PATH
    assert (
        git(repository, "config", "--worktree", "--get", "hook.repo-security.event").stdout.strip()
        == "pre-commit"
    )
    assert not (repository / ".git/config.lock").exists()
    assert not (repository / ".git/config.worktree.lock").exists()
    with pytest.raises(RepoSecurityError, match="not ready"):
        manage.require_commit_ready(repository)
    setup_repository(repository)
    assert git(repository, "config", "--get", "core.hooksPath", check=False).returncode == 1
    assert manage.hooks_enabled(repository, manage.hooks_state(repository))


@pytest.mark.parametrize("source", ["include", "multiple", "multiple-scopes"])
def test_legacy_path_with_unowned_source_values_is_preserved(
    repository: Path, source: str
) -> None:
    write_legacy(repository)
    git(repository, "config", "extensions.worktreeConfig", "true")
    if source == "include":
        included = repository / ".git/included config"
        included.write_text("[core]\n\thooksPath = .security/hooks\n")
        git(repository, "config", "include.path", str(included))
    elif source == "multiple":
        git(repository, "config", "--add", "core.hooksPath", "other-hooks")
        git(repository, "config", "--add", "core.hooksPath", manage.HOOKS_PATH)
    else:
        git(repository, "config", "--local", "core.hooksPath", manage.HOOKS_PATH)
        git(repository, "config", "--worktree", "core.hooksPath", manage.HOOKS_PATH)
    before = installation.snapshot(repository)
    with pytest.raises(RepoSecurityError, match="owned legacy record"):
        setup_repository(repository)
    assert installation.snapshot(repository) == before


@pytest.mark.parametrize("worktree_config", [False, True])
def test_disabled_dispatcher_is_removed_without_enabling_project_checks(
    repository: Path, *, worktree_config: bool
) -> None:
    write_legacy(repository)
    if worktree_config:
        git(repository, "config", "extensions.worktreeConfig", "true")
    (repository / manage.HOOK_ENTRY).chmod(0o644)
    project = repository / ".git/hooks/pre-commit.d/20-project"
    executable(project, "echo must-not-run > .git/disabled-project-trace\nexit 7\n")
    before = project.read_bytes(), project.stat().st_mode, project.stat().st_mtime_ns
    setup_repository(repository)
    assert not (repository / manage.HOOK_ENTRY).exists()
    assert not (repository / manage.HOOK_LINK).is_symlink()
    assert not (repository / hook_config.MIGRATED_CHECKS).exists()
    assert (project.read_bytes(), project.stat().st_mode, project.stat().st_mtime_ns) == before
    assert git(repository, "hook", "list", "-z", "pre-commit").stdout == "repo-security\0"
    # Replace only the fixture's security command; verify real Git runs no project member.
    git(repository, "-c", "hook.repo-security.command=true", "hook", "run", "pre-commit")
    assert not (repository / ".git/disabled-project-trace").exists()


@pytest.mark.parametrize("worktree_config", [False, True])
def test_legacy_path_cleanup_keeps_default_hooks_hidden(
    repository: Path, *, worktree_config: bool
) -> None:
    write_legacy(repository)
    git(repository, "config", "core.hooksPath", manage.HOOKS_PATH)
    if worktree_config:
        git(repository, "config", "extensions.worktreeConfig", "true")
    hidden = repository / ".git/hooks/commit-msg"
    active = repository / ".security/hooks/commit-msg"
    executable(hidden, "echo hidden >> .git/message-trace\n")
    executable(active, 'printf "active:%s\\n" "$1" >> .git/message-trace\n')
    before = hidden.read_bytes(), hidden.stat().st_mode, hidden.stat().st_mtime_ns
    setup_repository(repository)
    assert git(repository, "config", "--get", "core.hooksPath").stdout.strip() == os.devnull
    assert not (repository / manage.HOOK_ENTRY).exists()
    assert not (repository / manage.HOOK_LINK).is_symlink()
    assert git(repository, "hook", "list", "-z", "pre-commit").stdout == "repo-security\0"
    git(repository, "hook", "run", "commit-msg", "--", "message with spaces")
    assert (repository / ".git/message-trace").read_text() == "active:message with spaces\n"
    assert (hidden.read_bytes(), hidden.stat().st_mode, hidden.stat().st_mtime_ns) == before
    snapshot = installation.snapshot(repository)
    setup_repository(repository)
    assert installation.snapshot(repository) == snapshot


def test_legacy_path_cleanup_restores_explicit_global_hook_manager(
    repository: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write_legacy(repository)
    config = tmp_path / "global-config"
    config.write_text("[core]\n\thooksPath = global-hooks\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    git(repository, "config", "core.hooksPath", manage.HOOKS_PATH)
    hidden = repository / "global-hooks/commit-msg"
    executable(hidden, "echo inherited-manager > .git/global-hook-trace\n")
    before = config.read_bytes(), config.stat().st_mtime_ns
    setup_repository(repository)
    assert git(repository, "config", "--get", "core.hooksPath").stdout.strip() == "global-hooks"
    assert (
        git(repository, "config", "--local", "--get", "core.hooksPath", check=False).returncode == 1
    )
    git(repository, "hook", "run", "commit-msg")
    assert (repository / ".git/global-hook-trace").read_text() == "inherited-manager\n"
    assert (config.read_bytes(), config.stat().st_mtime_ns) == before


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


@pytest.mark.parametrize("isolated", [False, True])
def test_worktree_preserves_main_legacy_dispatcher(
    repository: Path, *, isolated: bool
) -> None:
    write_legacy(repository)
    git(repository, "add", ".")
    git(repository, "commit", "--no-verify", "-qm", "Legacy baseline")
    git(repository, "config", "extensions.worktreeConfig", "true")
    worktree = repository.parent / "isolated linked checkout"
    git(repository, "worktree", "add", "--detach", str(worktree))
    private_hooks = worktree.parent / "private hooks"
    if isolated:
        private_hooks.mkdir()
        git(worktree, "config", "--worktree", "core.hooksPath", str(private_hooks))
        # Deploy the reviewed current resources, as after pulling an upgrade.
        for resource in template_files():
            if resource.destination not in manage.CUSTOMIZABLE:
                target = worktree / resource.destination
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(resource.content)
                if resource.executable:
                    target.chmod(0o755)
    before = installation.snapshot(repository)
    if isolated:
        setup_repository(worktree)
        assert git(worktree, "hook", "list", "-z", "pre-commit").stdout == "repo-security\0"
        assert git(worktree, "config", "--get", "core.hooksPath").stdout.strip() == str(
            private_hooks
        )
    else:
        with pytest.raises(RepoSecurityError, match="shared security hook"):
            setup_repository(worktree)
    after = installation.snapshot(repository)
    assert {k: v for k, v in after.items() if not k.startswith(".git/worktrees/")} == {
        k: v for k, v in before.items() if not k.startswith(".git/worktrees/")
    }
    assert (repository / manage.HOOK_ENTRY).read_bytes() == (
        GOLDEN / "dispatcher-current.sh"
    ).read_bytes()
    assert (repository / manage.HOOK_LINK).is_symlink()


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
