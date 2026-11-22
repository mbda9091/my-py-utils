from __future__ import annotations

import json
import os
from importlib.metadata import PackageNotFoundError
from typing import TYPE_CHECKING

import pytest
from repo_security.errors import RepoSecurityError
from repo_security.resources import template_files
from repo_security.templates import manage

from tests import test_repo_security as fixtures
from tests.test_repo_security import git

if TYPE_CHECKING:
    from pathlib import Path

repository = fixtures.repository
real_scanners = fixtures.real_scanners


@pytest.mark.parametrize(
    "damage",
    [
        "missing_manifest",
        "corrupt_manifest",
        "outdated_manifest",
        "incomplete",
        "setup_lock",
        "missing_policy",
        "changed_policy",
        "changed_runtime",
        "missing_run",
        "missing_python",
        "missing_semgrep",
        "missing_gitleaks",
        "changed_gitleaks",
        "nonexecutable_gitleaks",
    ],
)
def test_unready_installation_blocks_real_commit(  # noqa: C901, PLR0912, PLR0915 -- explicit failure matrix
    real_scanners: Path,
    damage: str,
) -> None:
    root = real_scanners
    security = root / ".security"
    sample = root / "sample.py"
    sample.write_text("print('baseline')\n")
    git(root, "add", "sample.py")
    # Establish HEAD in this disposable fixture before damaging its installation.
    git(root, "commit", "--no-verify", "-qm", "Fixture baseline")
    before = git(root, "rev-parse", "HEAD").stdout.strip()
    sample.write_text("print('safe change')\n")
    git(root, "add", "sample.py")
    record_path = security / "install-manifest.json"
    record = json.loads(record_path.read_text())
    if damage == "missing_manifest":
        record_path.unlink()
    elif damage == "corrupt_manifest":
        record_path.write_text("{invalid")
    elif damage == "outdated_manifest":
        record["template_version"] = "old-version"
        record_path.write_text(json.dumps(record))
    elif damage == "incomplete":
        record["status"] = "incomplete"
        record["phase"] = "deployed"
        record_path.write_text(json.dumps(record))
    elif damage == "setup_lock":
        (root / ".git/repo-security-setup.lock").write_text("interrupted fixture")
    elif damage == "missing_policy":
        (security / "policy.json").unlink()
    elif damage == "changed_policy":
        with (security / "policy.json").open("a") as stream:
            stream.write("\n")
    elif damage == "changed_runtime":
        with (security / "scan.py").open("a") as stream:
            stream.write("\n# changed after installation\n")
    elif damage == "missing_run":
        (security / "run").unlink()
    elif damage in ("missing_python", "missing_semgrep"):
        interpreter = (security / ".venv/bin/python").resolve()
        # Replace only the fixture's environment link; never alter shared tools.
        (security / ".venv").unlink()
        (security / ".venv/bin").mkdir(parents=True)
        if damage == "missing_semgrep":
            (security / ".venv/bin/python").symlink_to(interpreter)
    elif damage in ("missing_gitleaks", "changed_gitleaks", "nonexecutable_gitleaks"):
        binary = security / "bin/gitleaks"
        content = binary.read_bytes()
        binary.unlink()  # The fixture originally hard-links the shared, verified binary.
        if damage == "changed_gitleaks":
            binary.write_bytes(b"invalid executable")
            binary.chmod(0o755)
        elif damage == "nonexecutable_gitleaks":
            binary.write_bytes(content)
            binary.chmod(0o644)
    result = git(root, "commit", "-qm", "Must be blocked", check=False)
    assert result.returncode != 0
    assert git(root, "rev-parse", "HEAD").stdout.strip() == before
    assert "Checking staged snapshot" not in result.stdout + result.stderr


@pytest.mark.parametrize("missing", [False, True])
def test_dependency_version_guard_fails_closed(
    real_scanners: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    missing: bool,
) -> None:
    def installed_version(name: str) -> str:
        if missing:
            raise PackageNotFoundError(name)
        return "0.0"

    monkeypatch.setattr(manage, "version", installed_version)
    with pytest.raises(RepoSecurityError, match="Security dependency"):
        manage.require_commit_ready(real_scanners)


def test_ready_guard_requires_security_member(
    real_scanners: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(manage, "version", manage.SCANNER_VERSIONS.__getitem__)
    git(real_scanners, "config", "--unset-all", "hook.repo-security.event")
    with pytest.raises(RepoSecurityError, match="hooks are not enabled"):
        manage.require_commit_ready(real_scanners)
    assert (
        git(
            real_scanners, "config", "--get-all", "hook.repo-security.event", check=False
        ).returncode
        == 1
    )


def test_partial_commit_scans_its_own_index(real_scanners: Path) -> None:
    root = real_scanners
    first, second = root / "first.py", root / "second.py"
    first.write_text("print('original')\n")
    second.write_text("print('original')\n")
    git(root, "add", "first.py", "second.py")
    git(root, "commit", "--no-verify", "-qm", "Fixture baseline")
    first.write_text("result = eval(user_input)\n")
    git(root, "add", "first.py")
    second.write_text("print('safe partial commit')\n")
    clean = git(
        root, "commit", "--only", "-qm", "Safe partial commit", "--", "second.py", check=False
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert git(root, "show", "HEAD:first.py").stdout == "print('original')\n"
    before = git(root, "rev-parse", "HEAD").stdout
    blocked = git(
        root, "commit", "--only", "-qm", "Unsafe partial commit", "--", "first.py", check=False
    )
    assert blocked.returncode != 0
    assert "Semgrep:" in blocked.stdout + blocked.stderr
    assert git(root, "rev-parse", "HEAD").stdout == before


def test_unrelated_pre_push_script_is_preserved(
    repository: Path,
) -> None:
    path = repository / ".security/hooks/pre-push"
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    plan = manage.make_plan(repository, template_files())
    assert not plan.conflicts
    assert path.read_text() == "#!/bin/sh\nexit 0\n"


@pytest.mark.parametrize("worktree_config", [False, True])
def test_shared_hook_uses_linked_worktrees_own_root_and_commit_index(
    real_scanners: Path, *, worktree_config: bool
) -> None:
    root = real_scanners
    (root / "sample.py").write_text("print('baseline')\n")
    git(root, "add", "sample.py")
    git(root, "commit", "--no-verify", "-qm", "Main baseline")
    if worktree_config:
        git(root, "config", "extensions.worktreeConfig", "true")
    main_config = (root / ".git/config").read_bytes()
    worktree = root.parent / "linked real scanner checkout"
    git(root, "worktree", "add", "--detach", str(worktree))
    resources = template_files()
    for resource in resources:
        path = worktree / resource.destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resource.content)
        path.chmod(0o755 if resource.executable else 0o644)
    security = worktree / ".security"
    (security / ".venv").symlink_to(root / ".security/.venv", target_is_directory=True)
    (security / "bin").mkdir()
    os.link(root / ".security/bin/gitleaks", security / "bin/gitleaks")
    plan = manage.make_plan(worktree, resources)
    record = manage.record_for(plan)
    original_record = manage.read_record(root)
    assert original_record
    record["gitleaks"] = original_record["gitleaks"]
    record["status"] = record["phase"] = "ready"
    manage.save_record(worktree, record)
    assert git(worktree, "hook", "list", "-z", "pre-commit").stdout == "repo-security\0"
    main_head = git(root, "rev-parse", "HEAD").stdout
    ready = git(worktree, "hook", "run", "pre-commit", check=False)
    assert ready.returncode == 0, ready.stdout + ready.stderr
    assert (ready.stdout + ready.stderr).count("Checking staged snapshot") == 1
    (worktree / "sample.py").write_text("print('safe partial commit')\n")
    clean = git(worktree, "commit", "--only", "-qm", "Safe linked commit", "--", "sample.py")
    assert (clean.stdout + clean.stderr).count("Checking staged snapshot") == 1
    before = git(worktree, "rev-parse", "HEAD").stdout
    (worktree / "sample.py").write_text("result = eval(user_input)\n")
    blocked = git(
        worktree, "commit", "--only", "-qm", "Unsafe linked commit", "--", "sample.py",
        check=False,
    )
    assert blocked.returncode != 0
    assert "Semgrep:" in blocked.stdout + blocked.stderr
    assert git(worktree, "rev-parse", "HEAD").stdout == before
    assert git(root, "rev-parse", "HEAD").stdout == main_head
    assert (root / ".git/config").read_bytes() == main_config
