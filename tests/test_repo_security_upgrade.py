from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from repo_security.core import setup_repository
from repo_security.errors import RepoSecurityError
from repo_security.resources import template_files
from repo_security.templates import manage

from tests import test_repo_security as fixtures
from tests import test_repo_security_install as install_fixtures
from tests.test_repo_security import git

if TYPE_CHECKING:
    from collections.abc import Callable

repository = fixtures.repository
fake_install = install_fixtures.fake_install
GOLDEN = Path(__file__).parent / "fixtures/repo-security"


def register_file(record: manage.InstallRecord, root: Path, relative: str) -> None:
    path = root / relative
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    record["files"][relative] = {
        "template_sha256": digest,
        "installed_sha256": digest,
        "executable": relative.startswith(".security/hooks/"),
    }


@pytest.fixture
def old_installation(repository: Path, fake_install: list[Path]) -> Path:
    setup_repository(repository)
    record = manage.read_record(repository)
    assert record
    (repository / ".security/setup.sh").unlink()
    record["files"].pop(".security/setup.sh")
    previous = repository / ".security/pii.py"
    previous.write_bytes(previous.read_bytes() + b"\n# Earlier managed release\n")
    register_file(record, repository, ".security/pii.py")
    guard = repository / ".security/hooks/pre-commit"
    guard.write_bytes(guard.read_bytes().replace(b" --require-ready", b""))
    register_file(record, repository, ".security/hooks/pre-commit")
    legacy_push = repository / manage.LEGACY_PUSH
    legacy_push.write_bytes((GOLDEN / "legacy-pre-push.sh").read_bytes())
    legacy_push.chmod(0o755)
    register_file(record, repository, manage.LEGACY_PUSH)
    (repository / manage.HOOK_ENTRY).write_bytes((GOLDEN / "dispatcher-late.sh").read_bytes())
    manage.save_record(repository, record)
    assert fake_install == [repository]
    return repository


@pytest.mark.parametrize("dispatcher", ["dispatcher-early.sh", "dispatcher-late.sh"])
def test_owned_legacy_installation_upgrades_and_preserves_user_checks(
    old_installation: Path, fake_install: list[Path], dispatcher: str
) -> None:
    root = old_installation
    entry = root / manage.HOOK_ENTRY
    entry.write_bytes((GOLDEN / dispatcher).read_bytes())
    old_record = (root / manage.MANIFEST).read_bytes()
    old_pii = (root / ".security/pii.py").read_bytes()
    checks = [root / ".git/hooks/pre-commit.d/20-user", root / ".git/hooks/pre-push"]
    for path in checks:
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in checks]
    setup_repository(root)
    record = manage.read_record(root)
    assert record
    assert record["status"] == "ready"
    assert "migration_backup" in record
    assert manage.hooks_enabled(root, manage.hooks_state(root))
    assert not (root / manage.LEGACY_PUSH).exists()
    assert (root / ".security/setup.sh").is_file()
    assert before == [(path.read_bytes(), path.stat().st_mtime_ns) for path in checks]
    backup = root / record["migration_backup"]
    assert (backup / "files" / manage.MANIFEST).read_bytes() == old_record
    assert (backup / "files/.security/pii.py").read_bytes() == old_pii
    assert (backup / "files" / manage.HOOK_ENTRY).read_bytes() == (GOLDEN / dispatcher).read_bytes()
    after = install_fixtures.snapshot(root)
    setup_repository(root)
    assert install_fixtures.snapshot(root) == after
    assert fake_install == [root, root, root]


def checkout_new_release(root: Path) -> None:
    for resource in template_files():
        target = root / resource.destination
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(resource.content)
        target.chmod(0o755 if resource.executable else 0o644)
    (root / manage.LEGACY_PUSH).unlink()
    git(root, "add", ".security", ".gitignore")
    # A fixture commit models pulling reviewed new files while local installation state stays old.
    git(root, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "New managed release")


def test_standalone_adopts_clean_pulled_release_and_migrates_legacy_hooks_path(
    old_installation: Path,
) -> None:
    root = old_installation
    git(root, "config", "core.hooksPath", manage.HOOKS_PATH)
    checkout_new_release(root)
    manage.standalone_main(root / ".security", install_hooks=True)
    record = manage.read_record(root)
    assert record
    assert record["status"] == "ready"
    assert "migration_backup" in record
    assert manage.hooks_enabled(root, manage.hooks_state(root))
    assert not git(root, "status", "--porcelain", "--untracked-files=no").stdout
    assert (root / record["migration_backup"] / "backup.json").is_file()


@pytest.mark.parametrize("relative", [".security/pii.py", ".security/policy.json"])
def test_standalone_does_not_adopt_uncommitted_edits(old_installation: Path, relative: str) -> None:
    root = old_installation
    checkout_new_release(root)
    path = root / relative
    path.write_bytes(path.read_bytes() + b"\n")
    before = install_fixtures.snapshot(root)
    with pytest.raises(RepoSecurityError, match="recorded installation"):
        manage.standalone_main(root / ".security", install_hooks=True)
    assert install_fixtures.snapshot(root) == before
    assert not (root / manage.BACKUPS).exists()


def test_registered_repository_rules_survive_bundle_upgrade(old_installation: Path) -> None:
    root = old_installation
    record = manage.read_record(root, legacy=True)
    assert record
    policy = root / ".security/policy.json"
    policy.write_bytes(policy.read_bytes() + b"\n")
    register_file(record, root, ".security/policy.json")
    manage.save_record(root, record)
    before = policy.read_bytes(), policy.stat().st_mtime_ns
    setup_repository(root)
    assert before == (policy.read_bytes(), policy.stat().st_mtime_ns)
    updated = manage.read_record(root)
    assert updated
    assert (
        updated["files"][".security/policy.json"]["installed_sha256"]
        == hashlib.sha256(before[0]).hexdigest()
    )


@pytest.mark.parametrize(
    ("target", "registered"),
    [(manage.HOOK_ENTRY, False), (manage.LEGACY_PUSH, False), (manage.LEGACY_PUSH, True)],
)
def test_modified_legacy_hooks_are_not_replaced(
    old_installation: Path, target: str, *, registered: bool
) -> None:
    root = old_installation
    path = root / target
    path.write_bytes(path.read_bytes() + b"\n# User extension\n")
    if registered:
        record = manage.read_record(root, legacy=True)
        assert record
        register_file(record, root, target)
        manage.save_record(root, record)
    before = install_fixtures.snapshot(root)
    with pytest.raises(RepoSecurityError):
        setup_repository(root)
    assert install_fixtures.snapshot(root) == before
    assert not (root / manage.BACKUPS).exists()


@pytest.mark.parametrize("stop_at", [".security/setup.py", ".security/pii.py"])
def test_partial_upgrade_blocks_commits_and_retries_from_original_backup(
    old_installation: Path, monkeypatch: pytest.MonkeyPatch, stop_at: str
) -> None:
    root = old_installation
    original: Callable[[manage.InstallPlan, manage.TemplateFile], None] = manage.deploy_resource

    def interrupted(plan: manage.InstallPlan, resource: manage.TemplateFile) -> None:
        original(plan, resource)
        if resource.destination == stop_at:
            commit = git(
                root, "commit", "--allow-empty", "-qm", "Must block during migration", check=False
            )
            assert commit.returncode != 0
            assert "setup is running or interrupted" in commit.stdout + commit.stderr
            msg = "injected migration interruption"
            raise OSError(msg)

    monkeypatch.setattr(manage, "deploy_resource", interrupted)
    with pytest.raises(OSError, match="migration interruption"):
        setup_repository(root)
    pending = manage.read_record(root, legacy=True)
    assert pending
    assert pending["status"] == "incomplete"
    assert "migration_backup" in pending
    backup = pending["migration_backup"]
    interpreter = root / ".security/.venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(sys.executable)
    protected = git(root, "commit", "--allow-empty", "-qm", "Must stay blocked", check=False)
    assert protected.returncode != 0
    output = protected.stdout + protected.stderr
    assert "BLOCKED:" in output
    assert "install manifest" in output or "installation is not ready" in output
    assert "Checking staged snapshot" not in output
    monkeypatch.setattr(manage, "deploy_resource", original)
    if stop_at == ".security/setup.py":
        with pytest.raises(RepoSecurityError, match="interrupted bundled upgrade"):
            manage.standalone_main(root / ".security", install_hooks=True)
    setup_repository(root)
    record = manage.read_record(root)
    assert record
    assert record["status"] == "ready"
    assert "migration_backup" in record
    assert record["migration_backup"] == backup
    assert len(list((root / manage.BACKUPS).iterdir())) == 1


def test_owned_disabled_dispatcher_is_enabled_during_upgrade(old_installation: Path) -> None:
    root = old_installation
    (root / manage.HOOK_ENTRY).chmod(0o644)
    setup_repository(root)
    assert manage.hooks_enabled(root, manage.hooks_state(root))


def test_upgrade_refuses_linked_backup_directory(old_installation: Path, tmp_path: Path) -> None:
    root = old_installation
    outside = tmp_path / "outside-backups"
    outside.mkdir()
    (root / manage.BACKUPS).symlink_to(outside, target_is_directory=True)
    before = install_fixtures.snapshot(root)
    with pytest.raises(RepoSecurityError, match="symlink"):
        setup_repository(root)
    assert install_fixtures.snapshot(root) == before
    assert not list(outside.iterdir())


def test_standalone_rejects_staged_changes_hidden_by_clean_working_copy(
    old_installation: Path,
) -> None:
    root = old_installation
    checkout_new_release(root)
    path = root / ".security/pii.py"
    clean = path.read_bytes()
    path.write_bytes(clean + b"\n# staged local edit\n")
    git(root, "add", ".security/pii.py")
    path.write_bytes(clean)
    with pytest.raises(RepoSecurityError, match="recorded installation"):
        manage.standalone_main(root / ".security", install_hooks=True)
    assert not (root / manage.BACKUPS).exists()
