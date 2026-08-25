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

import pytest
import yaml
from workspacectl.cli import main

ARGPARSE_ERROR_EXIT_CODE = 2


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
    remote = path.parent / f"{path.name}-origin.git"
    git("init", "--bare", str(remote), cwd=path.parent)
    git("remote", "add", "origin", str(remote), cwd=path)
    git("push", "--set-upstream", "origin", branch, cwd=path)
    git("symbolic-ref", "HEAD", f"refs/heads/{branch}", cwd=remote)
    git("remote", "set-head", "origin", branch, cwd=path)
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
    def test_create_requirement_requires_slug(self) -> None:
        stderr: list[str] = []
        with pytest.raises(SystemExit) as error, contextlib.redirect_stderr(_Capture(stderr)):
            main(
                [
                    "create-requirement",
                    "--title",
                    "中文需求",
                    "--project",
                    "alpha",
                ]
            )
        assert error.value.code == ARGPARSE_ERROR_EXIT_CODE
        assert "--slug" in "".join(stderr)

    def test_create_requirement_rejects_invalid_slug_with_format_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli(
                Path(tmp),
                "create-requirement",
                "--title",
                "中文需求",
                "--slug",
                "中文需求",
                "--project",
                "alpha",
            )
            assert code == 1
            message = err or out
            assert "invalid requirement slug" in message
            assert "lowercase ASCII letters, digits, and single hyphens" in message
            assert "Please retry." in message

    def test_init_uses_packaged_templates_when_workspace_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, _, _ = run_cli(root, "init")
            assert code == 0
            agents = (root / "AGENTS.md").read_text(encoding="utf-8")
            assert "workspacectl init" in agents
            assert not (root / "templates").exists()

    def test_init_allows_unavailable_registered_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "workspace.json").write_text(
                json.dumps({"repositories": {"aibot": {"git": {"origin": "example/aibot"}}}})
                + "\n",
                encoding="utf-8",
            )
            code, out, _ = run_cli(root, "init")
            assert code == 0, out

    def test_init_and_status_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_agent_templates(root)
            code, _, _ = run_cli(root, "init")
            assert code == 0
            assert (root / "workspace.json").exists()
            assert (root / ".git").is_dir()
            assert (root / ".gitignore").read_text(encoding="utf-8").splitlines() == [
                "projects/",
                "worktrees/",
            ]
            assert (root / "projects").is_dir()
            assert (root / "worktrees").is_dir()
            assert (root / "doc").is_dir()
            assert (root / ".workspace").is_dir()
            assert (root / "AGENTS.md").read_text(
                encoding="utf-8"
            ) == "# Usage Mode\n\nUse commands.\n"
            code, out, _ = run_cli(root, "init", "--json")
            assert code == 0
            repeat_payload = json.loads(out)
            assert repeat_payload["created"] == []
            assert repeat_payload["git_initialized"] is False
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
            (src1 / "REMOTE.md").write_text("from origin\n", encoding="utf-8")
            git("add", "REMOTE.md", cwd=src1)
            git("commit", "-m", "remote update", cwd=src1)
            git("push", "origin", "main", cwd=src1)
            (src1 / "openspec").mkdir()
            (src1 / "openspec" / "draft.md").write_text("local draft\n", encoding="utf-8")
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
                            "aibot": {"git": {"origin": "git@example.com:aibot.git"}},
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
            assert (alpha_worktree / "REMOTE.md").exists()
            assert not (alpha_worktree / "openspec").exists()
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
            code, out, _ = run_cli(root, "cleanup", "--task-root", str(task_root), "--json")
            assert code == 0, out
            cleanup_payload = json.loads(out)
            assert cleanup_payload["hooks"][0]["project"] == "default"
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

    def test_create_links_main_checkout_agents_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-a")
            local_agents = source / "AGENTS.local.md"
            local_agents.write_text("local project guidance\n", encoding="utf-8")
            (root / "projects").mkdir()
            (root / "projects" / "alpha").symlink_to(source, target_is_directory=True)
            (root / "workspace.json").write_text(
                json.dumps({"repositories": {"alpha": {}}}) + "\n", encoding="utf-8"
            )
            code, out, _ = run_cli(
                root,
                "create-requirement",
                "--title",
                "Local Agent Link",
                "--slug",
                "local-agent-link",
                "--project",
                "alpha",
                "--task-date",
                "2026-08-25",
                "--git-username",
                "merchant",
            )
            assert code == 0, out
            worktree = root / "worktrees" / "2026-08-25-local-agent-link" / "alpha-local-agent-link"
            link = worktree / "AGENTS.local.md"
            assert link.is_symlink()
            assert link.resolve() == local_agents.resolve()

    def test_create_replaces_tracked_agents_local_with_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-a")
            local_agents = source / "AGENTS.local.md"
            local_agents.write_text("tracked local guidance\n", encoding="utf-8")
            git("add", "AGENTS.local.md", cwd=source)
            git("commit", "-m", "add local guidance", cwd=source)
            git("push", "origin", "main", cwd=source)
            (root / "projects").mkdir()
            (root / "projects" / "alpha").symlink_to(source, target_is_directory=True)
            (root / "workspace.json").write_text(
                json.dumps({"repositories": {"alpha": {}}}) + "\n", encoding="utf-8"
            )
            code, out, _ = run_cli(
                root,
                "create-requirement",
                "--title",
                "Tracked Local Agent Link",
                "--slug",
                "tracked-local-agent-link",
                "--project",
                "alpha",
                "--task-date",
                "2026-08-25",
                "--git-username",
                "merchant",
            )
            assert code == 0, out
            worktree = (
                root
                / "worktrees"
                / "2026-08-25-tracked-local-agent-link"
                / "alpha-tracked-local-agent-link"
            )
            assert (worktree / "AGENTS.local.md").is_symlink()
            assert (worktree / "AGENTS.local.md").resolve() == local_agents.resolve()

    def test_ios_post_create_hook_copies_local_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-ios")
            (source / "Podfile").write_text("platform :ios, '16.0'\n", encoding="utf-8")
            (source / "PodfileConfig").write_text("local config\n", encoding="utf-8")
            git("add", "Podfile", "PodfileConfig", cwd=source)
            git("commit", "-m", "add ios config", cwd=source)
            git("push", "origin", "main", cwd=source)
            (root / "projects").mkdir()
            (root / "projects" / "ios").symlink_to(source, target_is_directory=True)
            (root / "workspace.json").write_text(
                json.dumps({"repositories": {"ios": {}}}) + "\n", encoding="utf-8"
            )
            code, out, _ = run_cli(
                root,
                "create-requirement",
                "--title",
                "iOS Hook",
                "--source-prd-url",
                "https://docs.example/prd/ios-hook",
                "--project",
                "ios",
                "--slug",
                "ios-hook",
                "--task-date",
                "2026-08-25",
                "--git-username",
                "merchant",
            )
            assert code == 0, out
            worktree = root / "worktrees" / "2026-08-25-ios-hook" / "ios-ios-hook"
            assert (worktree / "PodfileConfig").read_text(encoding="utf-8") == "local config\n"

    def test_android_post_create_hook_configures_local_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-android")
            (source / "asx").mkdir()
            (source / "asx" / "sourceProjects.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (source / "kconfig.json").write_text(
                json.dumps({"remote": {"server": "example"}}) + "\n", encoding="utf-8"
            )
            git("add", "asx", "kconfig.json", cwd=source)
            git("commit", "-m", "add android config", cwd=source)
            git("push", "origin", "main", cwd=source)
            (root / "projects").mkdir()
            (root / "projects" / "android").symlink_to(source, target_is_directory=True)
            (root / "workspace.json").write_text(
                json.dumps({"repositories": {"android": {}}}) + "\n", encoding="utf-8"
            )
            code, out, _ = run_cli(
                root,
                "create-requirement",
                "--title",
                "Android Hook",
                "--source-prd-url",
                "https://docs.example/prd/android-hook",
                "--project",
                "android",
                "--slug",
                "android-hook",
                "--task-date",
                "2026-08-25",
                "--git-username",
                "merchant",
            )
            assert code == 0, out
            worktree = root / "worktrees" / "2026-08-25-android-hook" / "android-android-hook"
            assert (worktree / "asx").is_symlink()
            assert (worktree / "asx").resolve() == (source / "asx").resolve()
            config = json.loads((worktree / "kconfig.json").read_text(encoding="utf-8"))
            assert config["remote"]["target"] == "~/code/android-android-hook"

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

    def test_create_requirement_without_source_prd_url_can_be_extended(self) -> None:
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
                "Requirement Without PRD",
                "--project",
                "alpha",
                "--slug",
                "requirement-without-prd",
                "--task-date",
                "2026-08-25",
                "--git-username",
                "merchant",
                "--json",
            )
            assert code == 0, out
            payload = json.loads(out)
            assert payload["requirement"]["source_prd_url"] is None
            task_root = root / "worktrees" / "2026-08-25-requirement-without-prd"
            manifest = read_yaml(task_root / ".workspace" / "task.yaml")
            assert manifest["requirement"]["source_prd_url"] is None
            agents_path = task_root / "alpha-requirement-without-prd" / ".workspace" / "AGENTS.md"
            agents = agents_path.read_text(encoding="utf-8")
            assert "- source PRD: ``" in agents

            code, out, _ = run_cli(
                root,
                "extend-requirement",
                "--task-root",
                str(task_root),
                "--add",
                "beta",
                "--json",
            )
            assert code == 0, out
            extended_payload = json.loads(out)
            assert extended_payload["worktrees"][0]["project_id"] == "alpha"
            assert extended_payload["worktrees"][1]["project_id"] == "beta"

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
