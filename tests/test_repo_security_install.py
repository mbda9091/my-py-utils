from __future__ import annotations

import hashlib
import io
import json
import platform
import subprocess
import sys
import tarfile
import urllib.request
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from repo_security.core import doctor_repository, plan_repository, setup_repository
from repo_security.errors import RepoSecurityError
from repo_security.main import main
from repo_security.resources import write_template
from repo_security.templates import manage

from tests import test_repo_security as fixtures
from tests.test_repo_security import git

repository = fixtures.repository

if TYPE_CHECKING:
    from pathlib import Path

ERROR_EXIT = 2
EXPECTED_RESTORED_STATUS = 1


def snapshot(root: Path) -> dict[str, tuple[bytes, int, int]]:
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
        for p in root.rglob("*")
        if p.is_file() and not p.is_symlink()
    }


@pytest.fixture
def fake_install(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    calls: list[Path] = []

    def install(root: Path, record: manage.InstallRecord) -> None:
        calls.append(root)
        binary = root / ".security/bin/gitleaks"
        binary.parent.mkdir(exist_ok=True)
        if not binary.exists():
            binary.write_bytes(b"unit-test executable")
            binary.chmod(0o755)
        record["gitleaks"] = {
            "version": manage.GITLEAKS_VERSION,
            "archive_sha256": manage.CHECKSUMS[(platform.system(), platform.machine())],
            "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        }

    monkeypatch.setattr(manage, "install_runtime", install)
    return calls


def test_dry_run_has_no_mutations_or_install(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    fake_install: list[Path],
) -> None:
    before = snapshot(repository)
    assert main(["setup", str(repository), "--dry-run", "--github-actions"]) == 0
    output = capsys.readouterr().out
    assert "ADD .github/workflows/repo-security.yml" in output
    assert "uv's Python/package caches" in output
    assert not fake_install
    assert snapshot(repository) == before
    assert not (repository / ".security").exists()


def test_conflicts_are_aggregated_in_read_only_plan(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    fake_install: list[Path],
) -> None:
    write_template(repository)
    (repository / ".security/semgrep.yml").write_text("custom\n")
    (repository / ".security/policy.json").write_text("custom\n")
    git(repository, "config", "core.hooksPath", ".husky")
    before = snapshot(repository)
    assert main(["setup", str(repository), "--dry-run"]) == ERROR_EXIT
    output = capsys.readouterr().out
    assert "semgrep.yml" in output
    assert "policy.json" in output
    assert "existing hook manager" in output
    assert snapshot(repository) == before
    assert not fake_install


def test_repeat_install_and_ignore_merge_are_idempotent(
    repository: Path,
    fake_install: list[Path],
) -> None:
    (repository / ".gitignore").write_bytes(b"# custom\r\noutput/\r\n")
    setup_repository(repository)
    before = snapshot(repository)
    setup_repository(repository)
    assert snapshot(repository) == before
    assert all(path == repository for path in fake_install)
    assert (repository / ".gitignore").read_bytes().startswith(b"# custom\r\noutput/\r\n")
    record = manage.read_record(repository)
    assert record
    assert record["status"] == "ready"
    assert record["original_local_hooks"] == []
    assert git(repository, "check-ignore", ".security/install-manifest.json").returncode == 0


def test_exact_template_without_manifest_can_be_adopted(
    repository: Path,
    fake_install: list[Path],
) -> None:
    write_template(repository)
    path = repository / ".security/policy.json"
    before = path.stat().st_mtime_ns
    setup_repository(repository)
    assert path.stat().st_mtime_ns == before
    assert fake_install == [repository]


def test_custom_config_and_old_version_are_not_overwritten(
    repository: Path,
    fake_install: list[Path],
) -> None:
    setup_repository(repository)
    record = repository / manage.MANIFEST
    data = json.loads(record.read_bytes())
    data["template_version"] = "0.1.0"
    record.write_text(json.dumps(data))
    policy = repository / ".security/policy.json"
    policy.write_text("custom\n")
    before = snapshot(repository)
    plan = plan_repository(repository)
    assert any("different-version install manifest" in x for x in plan.conflicts)
    assert any("policy.json" in x for x in plan.conflicts)
    with pytest.raises(RepoSecurityError):
        setup_repository(repository)
    assert snapshot(repository) == before
    assert fake_install == [repository]


def test_failed_install_retains_incomplete_state_and_retry_succeeds(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_root: Path, _record: manage.InstallRecord) -> None:
        message = "injected network failure"
        raise OSError(message)

    monkeypatch.setattr(manage, "install_runtime", fail)
    with pytest.raises(OSError, match="network failure"):
        setup_repository(repository)
    record = manage.read_record(repository)
    assert record
    assert record["status"] == "incomplete"
    assert (
        git(repository, "config", "--get", "core.hooksPath", check=False).returncode
        == EXPECTED_RESTORED_STATUS
    )
    assert not (repository / ".git/repo-security-setup.lock").exists()
    before = (repository / ".security/scan.py").stat().st_mtime_ns

    def succeed(_root: Path, _record: manage.InstallRecord) -> None:
        return

    monkeypatch.setattr(manage, "install_runtime", succeed)
    setup_repository(repository)
    assert (repository / ".security/scan.py").stat().st_mtime_ns == before
    record = manage.read_record(repository)
    assert record
    assert record["status"] == "ready"


def test_failed_repair_keeps_existing_protection(
    repository: Path,
    fake_install: list[Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_repository(repository)

    def fail(_root: Path, _record: manage.InstallRecord) -> None:
        message = "injected repair failure"
        raise OSError(message)

    monkeypatch.setattr(manage, "install_runtime", fail)
    with pytest.raises(OSError, match="repair failure"):
        setup_repository(repository)
    assert git(repository, "config", "--get", "core.hooksPath").stdout.strip() == manage.HOOKS_PATH
    assert fake_install == [repository]


def test_external_hook_change_during_install_is_preserved(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def change(root: Path, _record: manage.InstallRecord) -> None:
        git(root, "config", "core.hooksPath", ".external-hooks")

    monkeypatch.setattr(manage, "install_runtime", change)
    with pytest.raises(RepoSecurityError, match="changed during verification"):
        setup_repository(repository)
    assert git(repository, "config", "--get", "core.hooksPath").stdout.strip() == ".external-hooks"


def test_activation_failure_restores_original_local_setting(
    repository: Path,
    fake_install: list[Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = manage.hooks_state
    calls = 0

    def state(root: Path) -> manage.HooksState:
        nonlocal calls
        calls += 1
        actual = original(root)
        # Fail only the post-write effective-value check, while local config remains ours.
        if actual.local == (manage.HOOKS_PATH,) and calls > 1:
            return manage.HooksState(actual.local, ".blocked-by-command-config", "command line:")
        return actual

    monkeypatch.setattr(manage, "hooks_state", state)
    with pytest.raises(RepoSecurityError, match="did not take effect"):
        setup_repository(repository)
    assert (
        git(repository, "config", "--local", "--get", "core.hooksPath", check=False).returncode
        == EXPECTED_RESTORED_STATUS
    )
    assert fake_install == [repository]


def test_setup_lock_blocks_concurrent_install_and_is_released(
    repository: Path,
    fake_install: list[Path],
) -> None:
    with manage.setup_lock(repository):
        with pytest.raises(RepoSecurityError, match="lock exists"):
            setup_repository(repository)
        assert plan_repository(repository).conflicts
    assert not (repository / ".git/repo-security-setup.lock").exists()
    assert not fake_install


@pytest.mark.parametrize(
    "relative", [".security/hooks", ".security/bin", ".github", ".security/install-manifest.json"]
)
def test_symlink_targets_are_rejected_before_writes(
    repository: Path,
    tmp_path: Path,
    fake_install: list[Path],
    relative: str,
) -> None:
    target = repository / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    target.symlink_to(outside, target_is_directory=True)
    before = snapshot(repository)
    with pytest.raises(RepoSecurityError, match="symlink"):
        setup_repository(repository, github_actions=True)
    assert snapshot(repository) == before
    assert not list(outside.iterdir())
    assert not fake_install


def test_ci_opt_in_and_later_repeat_preserve_workflows(
    repository: Path,
    fake_install: list[Path],
) -> None:
    setup_repository(repository)
    assert not (repository / manage.WORKFLOW).exists()
    other = repository / ".github/workflows/other.yml"
    other.parent.mkdir(parents=True)
    other.write_text("name: Other\n")
    setup_repository(repository, github_actions=True)
    workflow = repository / manage.WORKFLOW
    before = workflow.read_bytes(), workflow.stat().st_mtime_ns
    setup_repository(repository)
    assert (workflow.read_bytes(), workflow.stat().st_mtime_ns) == before
    assert other.read_text() == "name: Other\n"
    assert len(fake_install) > 1


def test_custom_ci_conflict_does_not_install(repository: Path, fake_install: list[Path]) -> None:
    workflow = repository / manage.WORKFLOW
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: Custom\n")
    with pytest.raises(RepoSecurityError, match="different-version content"):
        setup_repository(repository, github_actions=True)
    assert not (repository / ".security").exists()
    assert workflow.read_text() == "name: Custom\n"
    assert not fake_install


def test_doctor_custom_rules_are_visible_without_repair(
    repository: Path,
    fake_install: list[Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_repository(repository)
    policy = repository / ".security/policy.json"
    policy.write_bytes(policy.read_bytes() + b"\n")
    calls: list[Path] = []
    monkeypatch.setattr(manage, "verify_runtime", calls.append)
    before = snapshot(repository)
    healthy, messages = doctor_repository(repository)
    assert healthy
    assert any(x.startswith("CUSTOM") for x in messages)
    assert snapshot(repository) == before
    assert calls == [repository / ".security"]
    assert fake_install == [repository]


def test_doctor_never_executes_modified_runtime(
    repository: Path,
    fake_install: list[Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_repository(repository)
    (repository / ".security/selftest.py").write_text("raise RuntimeError('untrusted')\n")
    calls: list[Path] = []
    monkeypatch.setattr(manage, "verify_runtime", calls.append)
    healthy, messages = doctor_repository(repository)
    assert not healthy
    assert any("selftest.py" in x for x in messages)
    assert not calls
    assert fake_install == [repository]


def test_gitleaks_checksum_failure_does_not_publish_binary(tmp_path: Path) -> None:
    with (
        patch.object(urllib.request, "urlopen", return_value=io.BytesIO(b"corrupt")),
        pytest.raises(RepoSecurityError, match="checksum mismatch"),
    ):
        manage.install_gitleaks(tmp_path, {})
    assert not (tmp_path / "bin/gitleaks").exists()


def test_gitleaks_valid_archive_reuse_and_corruption_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"synthetic executable"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("gitleaks")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    payload = buffer.getvalue()
    key = (platform.system(), platform.machine())
    monkeypatch.setitem(manage.CHECKSUMS, key, hashlib.sha256(payload).hexdigest())

    def download(*_args: object, **_kwargs: object) -> io.BytesIO:
        return io.BytesIO(payload)

    with patch.object(urllib.request, "urlopen", side_effect=download) as request:
        record = manage.install_gitleaks(tmp_path, {})
        manage.install_gitleaks(tmp_path, record)
        assert request.call_count == 1
        (tmp_path / "bin/gitleaks").write_bytes(b"tampered")
        manage.install_gitleaks(tmp_path, record)
        assert request.call_count == ERROR_EXIT
    assert (tmp_path / "bin/gitleaks").read_bytes() == content


def test_bare_and_linked_worktree_are_rejected(repository: Path, tmp_path: Path) -> None:
    bare = tmp_path / "bare.git"
    git(repository, "init", "--bare", "-q", str(bare))
    assert main(["setup", str(bare), "--dry-run"]) == ERROR_EXIT
    (repository / "safe.txt").write_text("safe\n")
    git(repository, "add", "safe.txt")
    git(repository, "commit", "-qm", "Initial")
    worktree = tmp_path / "worktree"
    git(repository, "worktree", "add", "--detach", str(worktree))
    assert main(["setup", str(worktree), "--dry-run"]) == ERROR_EXIT
    assert not (worktree / ".security").exists()


def test_final_manifest_failure_restores_newly_activated_hooks(
    repository: Path,
    fake_install: list[Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = manage.save_record
    failed = False

    def save(root: Path, record: manage.InstallRecord) -> None:
        nonlocal failed
        if record["status"] == "ready" and not failed:
            failed = True
            message = "injected manifest disk failure"
            raise OSError(message)
        original(root, record)

    monkeypatch.setattr(manage, "save_record", save)
    with pytest.raises(OSError, match="manifest disk failure"):
        setup_repository(repository)
    assert (
        git(repository, "config", "--get", "core.hooksPath", check=False).returncode
        == EXPECTED_RESTORED_STATUS
    )
    record = manage.read_record(repository)
    assert record
    assert record["status"] == "incomplete"
    assert fake_install == [repository]


def test_post_activation_concurrent_hook_value_is_not_rolled_back(
    repository: Path,
    fake_install: list[Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = manage.hooks_state

    def state(root: Path) -> manage.HooksState:
        actual = original(root)
        if actual.local == (manage.HOOKS_PATH,):
            git(root, "config", "core.hooksPath", ".concurrent")
            return original(root)
        return actual

    monkeypatch.setattr(manage, "hooks_state", state)
    with pytest.raises(RepoSecurityError, match="did not take effect"):
        setup_repository(repository)
    assert git(repository, "config", "--get", "core.hooksPath").stdout.strip() == ".concurrent"
    assert fake_install == [repository]


def test_damaged_binary_is_not_executed_by_doctor(
    repository: Path,
    fake_install: list[Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_repository(repository)
    (repository / ".security/bin/gitleaks").write_bytes(b"tampered")
    calls: list[Path] = []
    monkeypatch.setattr(manage, "verify_runtime", calls.append)
    healthy, messages = doctor_repository(repository)
    assert not healthy
    assert any("digest missing/mismatched" in x for x in messages)
    assert not calls
    assert fake_install == [repository]


def test_second_process_cannot_acquire_setup_lock(
    repository: Path, fake_install: list[Path]
) -> None:
    script = """
from pathlib import Path
import sys
from repo_security.templates.manage import setup_lock
with setup_lock(Path(sys.argv[1])):
    print("locked", flush=True)
    sys.stdin.read()
"""
    process = subprocess.Popen(  # noqa: S603 -- disposable fixture, explicitly released below
        [sys.executable, "-c", script, str(repository)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(RepoSecurityError, match="lock exists"):
            setup_repository(repository)
        assert not fake_install
    finally:
        process.communicate(input="", timeout=10)
    assert process.returncode == 0
    assert not (repository / ".git/repo-security-setup.lock").exists()
