from __future__ import annotations

import runpy
import subprocess
from importlib.resources import files
from typing import TYPE_CHECKING

import pytest

from tests import test_repo_security as fixtures
from tests.test_repo_security import git

if TYPE_CHECKING:
    from pathlib import Path

repository = fixtures.repository
real_scanners = fixtures.real_scanners


def test_staged_selection_uses_one_frozen_tree_and_nul_delimited_paths(
    repository: Path, tmp_path: Path
) -> None:
    script = tmp_path / "scan.py"
    template = files("repo_security").joinpath("templates", "scan.py.template")
    script.write_bytes(template.read_bytes())
    scanner = runpy.run_path(str(script))
    (repository / "unchanged.py").write_text("print('baseline')\n")
    git(repository, "add", "unchanged.py")
    git(repository, "commit", "--no-verify", "-qm", "Baseline")
    names = ["space name.py", "tab\tname.py", "line\nname.py", "测试.py", "-leading.py"]
    for name in names:
        (repository / name).write_text("print('staged')\n")
    git(repository, "add", "--", *names)
    tree = git(repository, "write-tree").stdout.strip()
    (repository / names[0]).write_text("print('later index')\n")
    (repository / "later.py").write_text("print('later file')\n")
    git(repository, "add", "--", names[0], "later.py")
    paths = scanner["staged_paths"](repository, tree)
    assert paths == set(names)
    entries = dict(scanner["entries"](repository, tree, paths))
    assert set(entries) == set(names)
    assert git(repository, "cat-file", "blob", entries[names[0]]).stdout == "print('staged')\n"


@pytest.mark.parametrize("category", ["gitleaks", "semgrep", "presidio"])
def test_unrelated_commit_skips_legacy_findings_but_head_scan_reports_them(
    real_scanners: Path, category: str
) -> None:
    root = real_scanners
    samples = {
        "gitleaks": 'token = "ghp_' + 'aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY3zA5"\n',
        "semgrep": "result = eval(user_input)\n",
        "presidio": 'phone = "139' + '12345678"\n',
    }
    (root / "legacy.py").write_text(samples[category])
    git(root, "add", "legacy.py")
    git(root, "commit", "--no-verify", "-qm", "Legacy fixture")
    (root / "safe.py").write_text("print('new safe file')\n")
    git(root, "add", "safe.py")
    committed = git(root, "commit", "-qm", "Unrelated safe change", check=False)
    output = committed.stdout + committed.stderr
    assert committed.returncode == 0, output
    assert "Scanned 1 files; 0 findings" in output
    full = subprocess.run(  # noqa: S603 -- real scanner in a disposable repository
        [str(root / ".security/run"), "head"], capture_output=True, text=True, check=False
    )
    assert full.returncode == 1, full.stdout + full.stderr
    assert category.casefold() in full.stdout.casefold()
    assert "legacy.py:" in full.stdout
    assert "Scanned 2 files;" in full.stdout


def test_modified_file_scans_unchanged_lines_from_the_staged_version(real_scanners: Path) -> None:
    root = real_scanners
    target = root / "legacy.py"
    target.write_text("result = eval(user_input)\n")
    git(root, "add", target.name)
    git(root, "commit", "--no-verify", "-qm", "Legacy fixture")
    before = git(root, "rev-parse", "HEAD").stdout
    target.write_text("result = eval(user_input)\nprint('new harmless line')\n")
    git(root, "add", target.name)
    target.write_text("print('safe working copy')\n")
    blocked = git(root, "commit", "-qm", "Must inspect the entire staged file", check=False)
    output = blocked.stdout + blocked.stderr
    assert blocked.returncode != 0
    assert "Semgrep: legacy.py:" in output
    assert "Scanned 1 files;" in output
    assert git(root, "rev-parse", "HEAD").stdout == before
    assert target.read_text() == "print('safe working copy')\n"


@pytest.mark.parametrize("operation", ["rename", "copy"])
def test_renamed_or_copied_legacy_file_is_scanned_under_its_new_name(
    real_scanners: Path, operation: str
) -> None:
    root = real_scanners
    source = root / "legacy.py"
    source.write_text("result = eval(user_input)\n")
    git(root, "add", source.name)
    git(root, "commit", "--no-verify", "-qm", "Legacy fixture")
    if operation == "rename":
        git(root, "mv", "--", source.name, "new name.py")
    else:
        (root / "new name.py").write_bytes(source.read_bytes())
        git(root, "add", "new name.py")
    blocked = git(root, "commit", "-qm", "New path must be checked", check=False)
    output = blocked.stdout + blocked.stderr
    assert blocked.returncode != 0
    assert "Semgrep: new name.py:" in output
    assert "Semgrep: legacy.py:" not in output
    assert "Scanned 1 files;" in output


@pytest.mark.parametrize("deletion", [False, True])
def test_empty_or_deletion_only_commit_scans_zero_files(
    real_scanners: Path, *, deletion: bool
) -> None:
    root = real_scanners
    (root / "legacy.py").write_text("result = eval(user_input)\n")
    (root / "obsolete.txt").write_text("safe\n")
    git(root, "add", "legacy.py", "obsolete.txt")
    git(root, "commit", "--no-verify", "-qm", "Legacy fixture")
    if deletion:
        git(root, "rm", "obsolete.txt")
    committed = git(root, "commit", "--allow-empty", "-qm", "No content to scan", check=False)
    output = committed.stdout + committed.stderr
    assert committed.returncode == 0, output
    assert "Scanned 0 files;" in output
    assert "Semgrep:" not in output


def test_unchanged_submodule_does_not_block_an_unrelated_file_change(real_scanners: Path) -> None:
    root = real_scanners
    git(root, "commit", "--no-verify", "--allow-empty", "-qm", "Baseline")
    oid = git(root, "rev-parse", "HEAD").stdout.strip()
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{oid},nested")
    git(root, "commit", "--no-verify", "-qm", "Existing submodule")
    (root / "safe.py").write_text("print('safe')\n")
    git(root, "add", "safe.py")
    committed = git(root, "commit", "-qm", "Unrelated file change", check=False)
    output = committed.stdout + committed.stderr
    assert committed.returncode == 0, output
    assert "Scanned 1 files; 0 findings" in output
