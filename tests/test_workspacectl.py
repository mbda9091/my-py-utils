from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml
from workspacectl.cli import main


def run_cli(cwd: Path, *argv: str) -> tuple[int, str, str]:
    stdout: list[str] = []
    stderr: list[str] = []
    code = 0
    old_cwd = Path.cwd()
    os.chdir(cwd)
    try:
        with (
            contextlib.redirect_stdout(_Capture(stdout)),
            contextlib.redirect_stderr(_Capture(stderr)),
        ):
            code = main(list(argv))
    finally:
        os.chdir(old_cwd)
    return code, "".join(stdout), "".join(stderr)


class _Capture:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    def write(self, text: str) -> int:
        self.chunks.append(text)
        return len(text)

    def flush(self) -> None:
        return None


def git(*args: str, cwd: Path) -> None:
    subprocess.run(  # noqa: S603 - test controls the git arguments
        ["git", *args],  # noqa: S607 - git is the intended executable
        cwd=str(cwd),
        check=True,
        capture_output=True,
    )


def make_repo(path: Path, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-b", branch, cwd=path)
    git("config", "user.name", "Tester", cwd=path)
    git("config", "user.email", "tester@example.com", cwd=path)
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    git("add", "README.md", cwd=path)
    git("commit", "-m", "init", cwd=path)
    return path


def read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_agent_templates(root: Path) -> None:
    templates_root = root / "templates" / "agents"
    templates_root.mkdir(parents=True)
    (templates_root / "usage.md").write_text("# Usage Mode\n\nUse commands.\n", encoding="utf-8")
    (templates_root / "development.md").write_text(
        "# Development Mode\n\nChange code.\n", encoding="utf-8"
    )


class WorkspaceControllerTests(unittest.TestCase):
    def test_init_uses_packaged_templates_when_workspace_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, _, _ = run_cli(root, "init")
            assert code == 0
            agents = (root / "AGENTS.md").read_text(encoding="utf-8")
            assert "workspacectl init" in agents
            assert not (root / "templates").exists()

    def test_init_and_status_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_agent_templates(root)
            code, _, _ = run_cli(root, "init")
            assert code == 0
            assert (root / "workspace.json").exists()
            assert (root / "projects").is_dir()
            assert (root / "worktrees").is_dir()
            assert (root / "doc").is_dir()
            assert (root / ".workspace").is_dir()
            assert (root / "AGENTS.md").read_text(
                encoding="utf-8"
            ) == "# Usage Mode\n\nUse commands.\n"
            code, out, _ = run_cli(root, "status", "--json")
            assert code == 0
            payload = json.loads(out)
            assert payload["workspace_root"] == str(root.resolve())
            assert payload["repositories"] == []

    def test_init_preserves_existing_agents_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_agent_templates(root)
            (root / "AGENTS.md").write_text("custom\n", encoding="utf-8")
            code, _, _ = run_cli(root, "init")
            assert code == 0
            assert (root / "AGENTS.md").read_text(encoding="utf-8") == "custom\n"

    def test_create_archive_and_cleanup_multi_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src1 = make_repo(root / "src-a")
            src2 = make_repo(root / "src-b")
            (root / "projects").mkdir()
            (root / "projects" / "alpha").symlink_to(src1, target_is_directory=True)
            (root / "projects" / "beta").symlink_to(src2, target_is_directory=True)
            (root / "workspace.json").write_text(
                json.dumps(
                    {
                        "repositories": {
                            "alpha": {
                                "git": {"origin": "git@example.com:alpha.git"},
                                "kind": "server",
                            },
                            "beta": {
                                "git": {"origin": "git@example.com:beta.git"},
                                "kind": "frontend",
                            },
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            code, out, _ = run_cli(
                root,
                "create-requirement",
                "--title",
                "Merchant Search Flow",
                "--source-prd-url",
                "https://docs.example/prd/42",
                "--project",
                "alpha",
                "--project",
                "beta",
                "--slug",
                "merchant-search-flow",
                "--task-date",
                "2026-08-24",
                "--git-username",
                "merchant",
            )
            assert code == 0, out
            task_root = root / "worktrees" / "2026-08-24-merchant-search-flow"
            assert task_root.is_dir()
            manifest = read_yaml(task_root / ".workspace" / "task.yaml")
            assert manifest["requirement"]["slug"] == "merchant-search-flow"
            assert manifest["scope"] == ["alpha", "beta"]
            alpha_worktree = task_root / "alpha-merchant-search-flow"
            beta_worktree = task_root / "beta-merchant-search-flow"
            assert (alpha_worktree / ".workspace" / "AGENTS.md").exists()
            assert (beta_worktree / ".workspace" / "AGENTS.md").exists()
            (alpha_worktree / "doc").mkdir()
            (alpha_worktree / "doc" / "requirement.md").write_text("alpha req", encoding="utf-8")
            (beta_worktree / "doc").mkdir()
            (beta_worktree / "doc" / "implementation.md").write_text("beta impl", encoding="utf-8")
            code, out, _ = run_cli(root, "archive-requirement", "--task-root", str(task_root))
            assert code == 0, out
            archive_root = root / "doc" / "2026-08-24-merchant-search-flow"
            assert (archive_root / "alpha-merchant-search-flow" / "requirement.md").exists()
            assert (archive_root / "beta-merchant-search-flow" / "implementation.md").exists()
            assert not (alpha_worktree / "doc").exists()
            code, out, _ = run_cli(root, "cleanup", "--task-root", str(task_root))
            assert code == 0, out
            assert not task_root.exists()
            assert (src1 / ".git").exists()
            git_executable = shutil.which("git")
            assert git_executable is not None
            branch_list = subprocess.run(  # noqa: S603 - executable is resolved from PATH
                [
                    git_executable,
                    "branch",
                    "--list",
                    "merchant/merchant/merchant-search-flow",
                ],
                cwd=str(src1),
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout
            assert "merchant/merchant/merchant-search-flow" in branch_list

    def test_archive_requires_docs_unless_discard_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = make_repo(root / "src-a")
            (root / "projects").mkdir()
            (root / "projects" / "alpha").symlink_to(src, target_is_directory=True)
            (root / "workspace.json").write_text(
                json.dumps(
                    {"repositories": {"alpha": {"git": {"origin": "git@example.com:alpha.git"}}}},
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            code, out, _ = run_cli(
                root,
                "create-requirement",
                "--title",
                "Archive Guard",
                "--source-prd-url",
                "https://docs.example/prd/99",
                "--project",
                "alpha",
                "--slug",
                "archive-guard",
                "--task-date",
                "2026-08-24",
                "--git-username",
                "merchant",
            )
            assert code == 0, out
            task_root = root / "worktrees" / "2026-08-24-archive-guard"
            code, out, err = run_cli(root, "archive-requirement", "--task-root", str(task_root))
            assert code != 0
            assert "missing" in (err or out)
            code, out, _ = run_cli(
                root,
                "archive-requirement",
                "--task-root",
                str(task_root),
                "--discard-missing-docs",
            )
            assert code == 0, out

    def test_extend_requirement_add_and_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src1 = make_repo(root / "src-a")
            src2 = make_repo(root / "src-b")
            (root / "projects").mkdir()
            (root / "projects" / "alpha").symlink_to(src1, target_is_directory=True)
            (root / "projects" / "beta").symlink_to(src2, target_is_directory=True)
            (root / "workspace.json").write_text(
                json.dumps(
                    {
                        "repositories": {
                            "alpha": {"git": {"origin": "git@example.com:alpha.git"}},
                            "beta": {"git": {"origin": "git@example.com:beta.git"}},
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            code, out, _ = run_cli(
                root,
                "create-requirement",
                "--title",
                "Scope Change",
                "--source-prd-url",
                "https://docs.example/prd/11",
                "--project",
                "alpha",
                "--slug",
                "scope-change",
                "--task-date",
                "2026-08-24",
                "--git-username",
                "merchant",
            )
            assert code == 0, out
            task_root = root / "worktrees" / "2026-08-24-scope-change"
            code, out, _ = run_cli(
                root,
                "extend-requirement",
                "--task-root",
                str(task_root),
                "--add",
                "beta",
                "--remove",
                "alpha",
            )
            assert code == 0, out
            manifest = read_yaml(task_root / ".workspace" / "task.yaml")
            assert manifest["scope"] == ["beta"]
            assert not (task_root / "alpha-scope-change").exists()
            assert (task_root / "beta-scope-change").is_dir()

    def test_doctor_json_reports_clean_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_agent_templates(root)
            src = make_repo(root / "src-a")
            (root / "projects").mkdir()
            (root / "projects" / "alpha").symlink_to(src, target_is_directory=True)
            (root / "workspace.json").write_text(
                json.dumps(
                    {"repositories": {"alpha": {"git": {"origin": "git@example.com:alpha.git"}}}},
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            code, _, _ = run_cli(root, "init")
            assert code == 0
            code, out, _ = run_cli(root, "doctor", "--json")
            assert code == 0
            payload = json.loads(out)
            assert payload["ok"]
            assert payload["issues"] == []

    def test_status_reports_invalid_repository_entry_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace.json").write_text(
                json.dumps({"repositories": {"broken": "not-an-object"}}) + "\n",
                encoding="utf-8",
            )
            code, out, _ = run_cli(root, "status", "--json")
            assert code == 0
            payload = json.loads(out)
            assert payload["repositories"][0]["repo_id"] == "broken"
            assert "must be an object" in payload["repositories"][0]["error"]

    def test_set_agent_mode_replaces_agents_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_agent_templates(root)
            (root / "AGENTS.md").write_text("old\n", encoding="utf-8")
            code, out, _ = run_cli(root, "set-agent-mode", "development")
            assert code == 0, out
            assert (root / "AGENTS.md").read_text(
                encoding="utf-8"
            ) == "# Development Mode\n\nChange code.\n"
            code, out, _ = run_cli(root, "set-agent-mode", "usage", "--json")
            assert code == 0, out
            payload = json.loads(out)
            assert payload["mode"] == "usage"
            assert (root / "AGENTS.md").read_text(
                encoding="utf-8"
            ) == "# Usage Mode\n\nUse commands.\n"


if __name__ == "__main__":
    unittest.main()
