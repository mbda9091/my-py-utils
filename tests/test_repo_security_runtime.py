from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from repo_security.main import main
from repo_security.resources import template_files, write_template

from tests import test_repo_security as fixtures
from tests.test_repo_security import git

if TYPE_CHECKING:
    from pathlib import Path
from importlib.resources import files

repository = fixtures.repository
real_scanners = fixtures.real_scanners
ERROR_EXIT = 2


def commit(root: Path, text: str) -> str:
    (root / "sample.py").write_text(text)
    git(root, "add", "sample.py")
    git(root, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "Synthetic snapshot")
    return git(root, "rev-parse", "HEAD").stdout.strip()


def test_real_push_covers_intermediate_commits_multiple_refs_and_deletions(
    real_scanners: Path,
    tmp_path: Path,
) -> None:
    root = real_scanners
    remote = tmp_path / "remote.git"
    git(root, "init", "--bare", "-q", str(remote))
    git(root, "remote", "add", "test", str(remote))
    clean = commit(root, "print('safe')\n")
    git(root, "push", "test", "HEAD:refs/heads/main")
    commit(root, 'phone = "13912345678"\n')
    commit(root, "print('removed')\n")
    git(root, "tag", "-a", "v1", "-m", "Synthetic tag")
    result = git(root, "push", "test", "HEAD:refs/heads/main", "refs/tags/v1", check=False)
    assert result.returncode != 0
    assert "CN_MOBILE_PHONE" in result.stdout + result.stderr
    assert "13912345678" not in result.stdout + result.stderr
    assert (
        git(root, "--git-dir", str(remote), "rev-parse", "refs/heads/main").stdout.strip() == clean
    )
    assert (
        git(root, "--git-dir", str(remote), "show-ref", "refs/tags/v1", check=False).returncode != 0
    )
    # First push of a new ref must still detect the removed ancestor content.
    result = git(root, "push", "test", "HEAD:refs/heads/new", check=False)
    assert result.returncode != 0
    assert "CN_MOBILE_PHONE" in result.stdout + result.stderr
    # Ref deletion introduces no snapshots and may proceed even with unsafe local history.
    git(root, "push", "test", ":refs/heads/main")
    assert git(root, "--git-dir", str(remote), "show-ref", check=False).stdout == ""


def test_gitleaks_inline_allow_and_ignore_file_cannot_bypass(real_scanners: Path) -> None:
    secret = "ghp_" + "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY3zA5"
    (real_scanners / "sample.py").write_text(f'token = "{secret}"  # gitleaks:allow\n')
    (real_scanners / ".gitleaksignore").write_text("sample.py:github-pat:1\n")
    git(real_scanners, "add", "sample.py", ".gitleaksignore")
    result = subprocess.run(  # noqa: S603 -- real scanner in an isolated fixture
        [str(real_scanners / ".security/run"), "staged"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Gitleaks:" in result.stdout
    assert secret not in result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["large", "submodule", "binary", "unsupported-encoding"])
def test_real_scanner_coverage_boundaries(real_scanners: Path, kind: str) -> None:
    root = real_scanners
    if kind == "submodule":
        oid = commit(root, "print('safe')\n")
        git(root, "update-index", "--add", "--cacheinfo", f"160000,{oid},nested")
    else:
        data = {"large": b"x" * 100, "binary": b"\0binary", "unsupported-encoding": b"\xfftext"}[
            kind
        ]
        (root / "sample.txt").write_bytes(data)
        git(root, "add", "sample.txt")
    if kind == "large":
        policy = root / ".security/policy.json"
        data = json.loads(policy.read_bytes())
        data["max_file_bytes"] = 10
        policy.write_text(json.dumps(data))
    result = subprocess.run(  # noqa: S603 -- real scanner in an isolated fixture
        [str(root / ".security/run"), "staged"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (ERROR_EXIT if kind in ("large", "submodule") else 0)
    if kind in ("binary", "unsupported-encoding"):
        assert "PII coverage gap" in result.stdout


def test_shallow_history_warns_without_fetch(
    real_scanners: Path,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    commit(real_scanners, "print('safe')\n")
    clone = tmp_path / "shallow"
    git(real_scanners, "clone", "--depth", "1", real_scanners.as_uri(), str(clone))
    write_template(clone)
    for name in (".venv", "bin"):
        (clone / ".security" / name).symlink_to(
            (real_scanners / ".security" / name).resolve(),
            target_is_directory=True,
        )
    assert git(clone, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    assert main(["scan", str(clone), "--mode", "history"]) == 0
    assert "shallow clone" in capfd.readouterr().err
    assert git(clone, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"


def test_tool_timeout_and_invalid_reports_fail_closed(tmp_path: Path) -> None:
    code = files("repo_security").joinpath("templates", "scan.py.template").read_text()
    script = tmp_path / "scan.py"
    script.write_text(code)
    check = """
import runpy, subprocess
from unittest.mock import patch
m = runpy.run_path("scan.py")
with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(["fake"], 60)):
    try:
        m["tool_findings"](["fake"], None, "Semgrep", None, {})
    except RuntimeError as error:
        assert "60-second limit" in str(error)
    else:
        raise AssertionError("timeout was not blocked")
for output in (b"invalid-json", b'{"errors":["parse-failed"],"results":[]}'):
    with patch.object(
        subprocess, "run", return_value=subprocess.CompletedProcess(["fake"], 0, output)
    ):
        try:
            m["tool_findings"](["fake"], None, "Semgrep", None, {})
        except RuntimeError:
            pass
        else:
            raise AssertionError("invalid report was not blocked")
"""
    result = subprocess.run(  # noqa: S603 -- isolated fault injection, no target project executed
        [sys.executable, "-c", check],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_generated_ci_yaml_structure_with_real_parser(real_scanners: Path) -> None:
    workflow = next(
        x for x in template_files(github_actions=True) if x.destination.startswith(".github/")
    )
    check = """
import re, sys, yaml
value = yaml.load(sys.stdin.read(), Loader=yaml.BaseLoader)
assert set(value["on"]) == {"push", "pull_request", "workflow_dispatch"}
assert value["permissions"] == {"contents": "read"}
steps = value["jobs"]["scan"]["steps"]
assert steps[0]["with"]["fetch-depth"] == "0"
assert steps[0]["with"]["persist-credentials"] == "false"
for step in steps:
    if "uses" in step:
        assert re.fullmatch(r"[a-zA-Z0-9/-]+@[0-9a-f]{40}", step["uses"])
assert steps[-1]["run"] == ".security/run history"
assert "--install-hooks" not in steps[-2]["run"]
"""
    result = subprocess.run(  # noqa: S603 -- PyYAML comes from the real scanner environment
        [str(real_scanners / ".security/.venv/bin/python"), "-c", check],
        input=workflow.content.decode(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
