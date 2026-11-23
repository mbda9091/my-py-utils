from __future__ import annotations

import shutil
import subprocess
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import pytest
from skill_sync.cli import main
from skill_sync.config import config_path, installations_path
from skill_sync.errors import SkillSyncError
from skill_sync.installations import (
    ManagedInstallation,
    read_managed_installations,
    write_managed_installations,
)
from skill_sync.installer import install_skill
from skill_sync.repository import RepositoryConfig, init_repository, read_repository_config
from skill_sync.uninstaller import uninstall_skill
from skill_sync.updater import update_repository

from tests.skill_sync_helpers import (
    assert_process_waiting,
    expect_process_line,
    finish_mutation,
    make_committed_repository,
    make_personal_config,
    run_git,
    start_mutation,
    write_legacy_config,
    write_skill,
)


@dataclass(frozen=True)
class UpdateSetup:
    config: RepositoryConfig
    upstream: Path
    remote: Path


def push(upstream: Path, message: str) -> None:
    run_git(["add", "--all"], cwd=upstream)
    run_git(["commit", "-m", message], cwd=upstream)
    run_git(["push"], cwd=upstream)


@pytest.fixture
def update_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> UpdateSetup:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    upstream = make_committed_repository(tmp_path / "upstream")
    write_skill(upstream.path / "skills")
    (upstream.path / ".gitignore").write_text(".cache/\n")
    push(upstream.path, "Initial skills")
    local = tmp_path / "local"
    run_git(["clone", str(upstream.remote), str(local)], cwd=None)
    for key, value in (
        ("user.name", "Test User"),
        ("user.email", "test@example.com"),
        ("commit.gpgSign", "false"),
        ("core.hooksPath", str(tmp_path / "no-hooks")),
    ):
        run_git(["config", key, value], cwd=local)
    config = init_repository(local, state_home=tmp_path / "state")
    monkeypatch.setenv("SKILL_SYNC_HOME", str(config.state_home))
    return UpdateSetup(config, upstream.path, upstream.remote)


def test_update_fast_forwards_existing_links_and_preserves_state(update_setup: UpdateSetup) -> None:
    setup = update_setup
    installed = install_skill("demo-skill", setup.config).path
    records = installations_path(setup.config.state_home).read_bytes()
    config_bytes = config_path(setup.config.state_home).read_bytes()
    git_config = (setup.config.repository_path / ".git/config").read_bytes()
    descriptor = setup.upstream / "skills/demo-skill/SKILL.md"
    descriptor.write_text(descriptor.read_text() + "\nNew revision\n")
    push(setup.upstream, "Update skill")
    result = update_repository(setup.config)
    assert result.before_head != result.after_head
    assert result.after_head == run_git(["rev-parse", "HEAD"], cwd=setup.upstream).strip()
    assert "New revision" in (installed / "SKILL.md").read_text()
    assert not result.warnings
    assert installations_path(setup.config.state_home).read_bytes() == records
    assert config_path(setup.config.state_home).read_bytes() == config_bytes
    assert (setup.config.repository_path / ".git/config").read_bytes() == git_config
    again = update_repository(setup.config)
    assert again.before_head == again.after_head


@pytest.mark.parametrize("kind", ["staged", "unstaged", "untracked"])
def test_update_refuses_local_changes_before_pull(
    update_setup: UpdateSetup, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    repo = update_setup.config.repository_path
    path = repo / ("untracked" if kind != "unstaged" else "README.md")
    path.write_text("Local work")
    if kind == "staged":
        run_git(["add", "untracked"], cwd=repo)
    before = path.read_bytes()

    def no_pull(*_args: object) -> None:
        pytest.fail("Preflight must reject dirty work before pull")

    monkeypatch.setattr("skill_sync.updater._pull", no_pull)
    with pytest.raises(SkillSyncError, match="local changes"):
        update_repository(update_setup.config)
    assert path.read_bytes() == before


def test_update_allows_ignored_cache_and_overrides_auto_rebase(update_setup: UpdateSetup) -> None:
    repo = update_setup.config.repository_path
    cache = repo / ".cache/local"
    cache.parent.mkdir()
    cache.write_text("Ignored cache")
    for key in ("pull.rebase", "rebase.autoStash", "merge.autoStash", "submodule.recurse"):
        run_git(["config", key, "true"], cwd=repo)
    (update_setup.upstream / "README.md").write_text("Updated")
    push(update_setup.upstream, "Update readme")
    result = update_repository(update_setup.config)
    assert result.before_head != result.after_head
    assert cache.read_text() == "Ignored cache"
    assert not run_git(["stash", "list"], cwd=repo)


@pytest.mark.parametrize(
    "condition", ["detached", "no-upstream", "merge", "rebase", "cherry-pick", "revert"]
)
def test_update_rejects_missing_preconditions(
    update_setup: UpdateSetup, monkeypatch: pytest.MonkeyPatch, condition: str
) -> None:
    repo = update_setup.config.repository_path
    if condition == "detached":
        run_git(["checkout", "--detach"], cwd=repo)
    elif condition == "no-upstream":
        run_git(["branch", "--unset-upstream"], cwd=repo)
    else:
        marker = {
            "merge": "MERGE_HEAD",
            "rebase": "rebase-merge",
            "cherry-pick": "CHERRY_PICK_HEAD",
            "revert": "REVERT_HEAD",
        }[condition]
        path = Path(run_git(["rev-parse", "--absolute-git-dir"], cwd=repo).strip()) / marker
        if condition == "rebase":
            path.mkdir()
        else:
            path.write_text(run_git(["rev-parse", "HEAD"], cwd=repo))

    def no_pull(*_args: object) -> None:
        pytest.fail("Preflight should reject before pull")

    monkeypatch.setattr("skill_sync.updater._pull", no_pull)
    with pytest.raises(SkillSyncError):
        update_repository(update_setup.config)


def test_update_rejects_unborn_repository(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    with pytest.raises(SkillSyncError, match="committed HEAD"):
        update_repository(config)


@pytest.mark.parametrize("diverged", [False, True])
def test_update_handles_local_ahead_and_diverged_branches(
    update_setup: UpdateSetup, *, diverged: bool
) -> None:
    repo = update_setup.config.repository_path
    (repo / "local.txt").write_text("Local commit")
    run_git(["add", "local.txt"], cwd=repo)
    run_git(["commit", "-m", "Local work"], cwd=repo)
    before = run_git(["rev-parse", "HEAD"], cwd=repo).strip()
    index = (repo / ".git/index").read_bytes()
    if diverged:
        (update_setup.upstream / "remote.txt").write_text("Remote work")
        push(update_setup.upstream, "Remote work")
        with pytest.raises(SkillSyncError, match="Git pull failed"):
            update_repository(update_setup.config)
    else:
        result = update_repository(update_setup.config)
        assert result.before_head == result.after_head == before
    assert run_git(["rev-parse", "HEAD"], cwd=repo).strip() == before
    assert (repo / ".git/index").read_bytes() == index
    assert not run_git(["stash", "list"], cwd=repo)


def test_update_reports_inaccessible_remote(update_setup: UpdateSetup) -> None:
    repo = update_setup.config.repository_path
    run_git(["remote", "set-url", "origin", str(repo / "nonexistent.git")], cwd=repo)
    before = run_git(["rev-parse", "HEAD"], cwd=repo)
    with pytest.raises(SkillSyncError, match="Git pull failed"):
        update_repository(update_setup.config)
    assert run_git(["rev-parse", "HEAD"], cwd=repo) == before


def test_update_catalog_changes_warn_without_auto_install_or_record_rewrite(
    update_setup: UpdateSetup,
) -> None:
    setup = update_setup
    installed = install_skill("demo-skill", setup.config).path
    before = installations_path(setup.config.state_home).read_bytes()
    old = setup.upstream / "skills/demo-skill"
    renamed = old.with_name("renamed-skill")
    old.rename(renamed)
    descriptor = renamed / "SKILL.md"
    descriptor.write_text(descriptor.read_text().replace("demo-skill", "renamed-skill"))
    write_skill(setup.upstream / "skills", "fresh-skill")
    invalid = write_skill(setup.upstream / "skills", "invalid-skill")
    (invalid / "SKILL.md").write_text("Broken")
    push(setup.upstream, "Rename, add and break skills")
    result = update_repository(setup.config)
    assert any("broken" in warning and str(installed) in warning for warning in result.warnings)
    assert any("invalid-skill" in warning for warning in result.warnings)
    assert installed.is_symlink()
    assert not installed.exists()
    assert not (installed.parent / "fresh-skill").exists()
    assert installations_path(setup.config.state_home).read_bytes() == before
    assert uninstall_skill("demo-skill", setup.config).changed


@pytest.mark.parametrize("entrypoint", ["api", "cli"])
def test_update_warns_when_installed_skill_loses_descriptor(
    update_setup: UpdateSetup, capsys: pytest.CaptureFixture[str], entrypoint: str
) -> None:
    setup = update_setup
    installed = install_skill("demo-skill", setup.config).path
    install_skill("demo-skill", setup.config, target_root=setup.config.state_home / "extra")
    before_records = installations_path(setup.config.state_home).read_bytes()
    before_config = config_path(setup.config.state_home).read_bytes()
    upstream_skill = setup.upstream / "skills/demo-skill"
    (upstream_skill / "helper.py").write_text("# Retained skill resource\n")
    (upstream_skill / "SKILL.md").unlink()
    push(setup.upstream, "Remove descriptor but keep skill resources")

    if entrypoint == "api":
        result = update_repository(setup.config)
        assert result.before_head != result.after_head
        warnings = result.warnings
    else:
        assert main(["update"]) == 0
        output = capsys.readouterr()
        assert "Updated repository:" in output.out
        warnings = tuple(output.err.splitlines())
    invalid = [warning for warning in warnings if "Invalid skill after Git update" in warning]
    assert len(invalid) == 1
    assert "demo-skill" in invalid[0]
    assert "Missing SKILL.md" in invalid[0]
    assert installed.is_symlink()
    assert (installed / "helper.py").is_file()
    assert not (installed / "SKILL.md").exists()
    assert installations_path(setup.config.state_home).read_bytes() == before_records
    assert config_path(setup.config.state_home).read_bytes() == before_config


@pytest.mark.parametrize("provenance", ["unknown", "previous-repository"])
def test_update_does_not_validate_drafts_from_unrelated_installation_records(
    update_setup: UpdateSetup, provenance: str
) -> None:
    setup = update_setup
    draft = setup.upstream / "skills/draft-skill"
    draft.mkdir()
    (draft / "helper.py").write_text("# Draft without a descriptor\n")
    push(setup.upstream, "Add draft directory")
    path = setup.config.state_home / "unrelated-link"
    source = (
        setup.config.state_home / "old-repository/skills/draft-skill"
        if provenance == "previous-repository"
        else None
    )
    record = ManagedInstallation("draft-skill", "local-path", path, source)
    records = read_managed_installations(setup.config.state_home)
    records[str(path)] = record
    write_managed_installations(records, setup.config.state_home)
    before = installations_path(setup.config.state_home).read_bytes()

    result = update_repository(setup.config)

    assert result.before_head != result.after_head
    assert not result.warnings
    assert installations_path(setup.config.state_home).read_bytes() == before


@pytest.mark.parametrize("kind", ["symlink", "file"])
def test_update_invalid_catalog_root_still_reports_successful_pull(
    update_setup: UpdateSetup, kind: str
) -> None:
    setup = update_setup
    skills = setup.upstream / "skills"
    shutil.rmtree(skills)
    if kind == "symlink":
        skills.symlink_to("elsewhere")
    else:
        skills.write_text("Not a directory")
    push(setup.upstream, "Invalid catalog root")
    result = update_repository(setup.config)
    assert result.before_head != result.after_head
    assert any("catalog is invalid" in warning for warning in result.warnings)


def test_update_supports_git_worktree(update_setup: UpdateSetup, tmp_path: Path) -> None:
    repo = update_setup.config.repository_path
    linked = tmp_path / "worktree"
    run_git(["worktree", "add", "-b", "worktree-update", str(linked)], cwd=repo)
    run_git(["branch", "--set-upstream-to=origin/main"], cwd=linked)
    config = init_repository(linked, state_home=tmp_path / "worktree-state")
    (update_setup.upstream / "README.md").write_text("Worktree update")
    push(update_setup.upstream, "Update worktree")
    result = update_repository(config)
    assert result.before_head != result.after_head
    assert (linked / "README.md").read_text() == "Worktree update"
    assert (repo / "README.md").read_text() != "Worktree update"


def test_update_only_executes_fixed_git_commands(
    update_setup: UpdateSetup, monkeypatch: pytest.MonkeyPatch
) -> None:
    execute = subprocess.run
    calls: list[tuple[str, ...]] = []
    allowed = {
        ("--no-optional-locks", "rev-parse", "--show-toplevel"),
        ("--no-optional-locks", "rev-parse", "--verify", "HEAD"),
        ("--no-optional-locks", "symbolic-ref", "--quiet", "HEAD"),
        ("--no-optional-locks", "rev-parse", "--symbolic-full-name", "@{upstream}"),
        ("--no-optional-locks", "rev-parse", "--absolute-git-dir"),
        (
            "--no-optional-locks",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=normal",
            "--ignore-submodules=none",
        ),
        ("pull", "--ff-only", "--no-rebase", "--no-autostash", "--no-recurse-submodules"),
    }

    def traced(  # noqa: PLR0913 - mirrors the subprocess keyword contract
        command: list[str],
        *,
        cwd: Path,
        capture_output: bool,
        text: bool,
        check: bool,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == update_setup.config.repository_path
        args = tuple(command[1:])
        assert args in allowed
        calls.append(args)
        return execute(
            command,
            cwd=cwd,
            capture_output=capture_output,
            text=text,
            check=check,
            encoding=encoding,
            errors=errors,
        )

    monkeypatch.setattr("skill_sync.updater.subprocess.run", traced)
    update_repository(update_setup.config)
    assert sum(args[0] == "pull" for args in calls) == 1


def test_update_auth_failure_redacts_remote_and_reports_current_head(
    update_setup: UpdateSetup, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    credential = "example-credential"

    def denied(_executable: str, _repository: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["git", "pull"],
            128,
            "",
            f"fatal: Authentication failed for 'https://user:{credential}@example.invalid/skills.git'",
        )

    monkeypatch.setattr("skill_sync.updater._pull", denied)
    assert main(["update"]) == 1
    output = capsys.readouterr()
    assert str(update_setup.config.repository_path) in output.out
    error = output.err
    assert "Git pull failed" in error
    assert "current HEAD:" in error
    assert credential not in error
    assert "example.invalid" not in error


@pytest.mark.parametrize("second_action", ["install", "init"])
def test_update_serializes_with_other_mutations(
    update_setup: UpdateSetup, tmp_path: Path, second_action: str
) -> None:
    setup = update_setup
    beta = write_skill(setup.upstream / "skills", "beta")
    push(setup.upstream, "Add beta")
    if second_action == "init":
        second_source = make_personal_config(tmp_path / "other").repository_path
    else:
        second_source = setup.config.repository_path / "skills" / beta.name
    with ExitStack() as stack:
        first = start_mutation(
            stack, "update", setup.config, setup.config.repository_path, pause=True
        )
        expect_process_line(first, "READY")
        second = start_mutation(stack, second_action, setup.config, second_source, pause=False)
        assert_process_waiting(second)
        finish_mutation(first)
        finish_mutation(second)
    assert (setup.config.repository_path / "skills/beta").is_dir()
    if second_action == "init":
        assert read_repository_config(setup.config.state_home).repository_path == second_source
    else:
        installed = setup.config.state_home.parent / "installed/beta"
        assert installed.readlink() == second_source


@pytest.mark.parametrize("operation", ["merge", "rebase"])
def test_update_rejects_real_unfinished_git_operation(
    update_setup: UpdateSetup, operation: str
) -> None:
    setup = update_setup
    repo = setup.config.repository_path
    (repo / "README.md").write_text("Local conflicting edit")
    run_git(["add", "README.md"], cwd=repo)
    run_git(["commit", "-m", "Local conflict"], cwd=repo)
    (setup.upstream / "README.md").write_text("Remote conflicting edit")
    push(setup.upstream, "Remote conflict")
    run_git(["fetch"], cwd=repo)
    with pytest.raises(subprocess.CalledProcessError):
        run_git([operation, "origin/main"], cwd=repo)
    with pytest.raises(SkillSyncError, match="in-progress Git operation"):
        update_repository(setup.config)


@pytest.mark.parametrize("command", ["uninstall", "remove", "update"])
def test_new_commands_preserve_legacy_config_until_explicit_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    state = tmp_path / "state"
    write_legacy_config(state)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SKILL_SYNC_HOME", str(state))
    config_before = config_path(state).read_bytes()
    records = installations_path(state)
    records.write_text('{"old-record": {"skill": "demo-skill"}}')
    records_before = records.read_bytes()
    args = [command] if command == "update" else [command, "demo-skill"]
    assert main(args) == 1
    assert config_path(state).read_bytes() == config_before
    assert records.read_bytes() == records_before
