from __future__ import annotations

import json
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
        "legacy_push",
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
    git(root, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "Fixture baseline")
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
    elif damage == "legacy_push":
        (security / "hooks/pre-push").write_text("#!/bin/sh\nexit 0\n")
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
    (real_scanners / manage.HOOK_LINK).unlink()
    with pytest.raises(RepoSecurityError, match="hooks are not enabled"):
        manage.require_commit_ready(real_scanners)
    assert not (real_scanners / manage.HOOK_LINK).exists()


def test_setup_refuses_to_silently_keep_legacy_push_hook(
    repository: Path,
) -> None:
    path = repository / ".security/hooks/pre-push"
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    plan = manage.make_plan(repository, template_files())
    assert any("Legacy pre-push hook" in conflict for conflict in plan.conflicts)
    assert path.read_text() == "#!/bin/sh\nexit 0\n"
