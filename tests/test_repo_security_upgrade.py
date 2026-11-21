from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest
from repo_security.core import setup_repository
from repo_security.errors import RepoSecurityError
from repo_security.resources import template_files
from repo_security.templates import manage

from tests import test_repo_security as fixtures
from tests import test_repo_security_install as installation

if TYPE_CHECKING:
    from pathlib import Path

repository = fixtures.repository
fake_install = installation.fake_install
pytestmark = pytest.mark.usefixtures("fake_install")


@pytest.fixture
def managed_update(repository: Path) -> Path:
    setup_repository(repository)
    record = manage.read_record(repository)
    assert record
    path = repository / ".security/pii.py"
    path.write_bytes(path.read_bytes() + b"\n# Previous managed resource\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    record["files"][".security/pii.py"].update(template_sha256=digest, installed_sha256=digest)
    manage.save_record(repository, record)
    return repository


def test_current_native_resources_can_be_updated_without_touching_hooks(
    managed_update: Path,
) -> None:
    root = managed_update
    foreign = root / ".git/hooks/pre-push"
    foreign.write_text("#!/bin/sh\nexit 7\n")
    foreign.chmod(0o755)
    before = foreign.read_bytes(), foreign.stat().st_mode, foreign.stat().st_mtime_ns
    setup_repository(root)
    resource = next(x for x in template_files() if x.destination == ".security/pii.py")
    assert (root / resource.destination).read_bytes() == resource.content
    record = manage.read_record(root)
    assert record
    assert record["status"] == "ready"
    assert (foreign.read_bytes(), foreign.stat().st_mode, foreign.stat().st_mtime_ns) == before
    assert not (root / ".git/repo-security-backups").exists()
    after = installation.snapshot(root)
    setup_repository(root)
    assert installation.snapshot(root) == after


def test_interrupted_native_resource_update_blocks_and_can_retry(
    managed_update: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = managed_update
    original = manage.deploy_resource

    def interrupted(plan: manage.InstallPlan, resource: manage.TemplateFile) -> None:
        original(plan, resource)
        if resource.destination == ".security/pii.py":
            msg = "injected resource interruption"
            raise OSError(msg)

    monkeypatch.setattr(manage, "deploy_resource", interrupted)
    with pytest.raises(OSError, match="resource interruption"):
        setup_repository(root)
    pending = manage.read_record(root)
    assert pending
    assert pending["status"] == "incomplete"
    with pytest.raises(RepoSecurityError, match="not ready"):
        manage.require_commit_ready(root)
    monkeypatch.setattr(manage, "deploy_resource", original)
    setup_repository(root)
    ready = manage.read_record(root)
    assert ready
    assert ready["status"] == "ready"


@pytest.mark.parametrize("standalone", [False, True])
def test_unsupported_install_record_is_rejected_without_cleanup(
    repository: Path, *, standalone: bool
) -> None:
    setup_repository(repository)
    record = manage.read_record(repository)
    assert record
    record["schema_version"] = 1
    record["template_version"] = "0.2.0"
    manage.save_record(repository, record)
    protected = repository / ".git/hooks/pre-commit"
    protected.write_text("#!/bin/sh\nexit 0\n")
    protected.chmod(0o755)
    before = installation.snapshot(repository)
    message = "automatic cleanup or migration is not supported"
    if standalone:
        with pytest.raises(RepoSecurityError, match=message):
            manage.standalone_main(repository / ".security", install_hooks=True)
    else:
        with pytest.raises(RepoSecurityError, match=message):
            setup_repository(repository)
    assert installation.snapshot(repository) == before
