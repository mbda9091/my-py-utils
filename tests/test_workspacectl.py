from __future__ import annotations

import contextlib
import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from workspacectl.cli import main
from workspacectl.core import (
    TASK_KIND_REQUIREMENT,
    WorkspaceController,
    WorkspaceError,
    git_branch_relation,
    git_fetch_remote_branch,
    git_local_branch_upstream,
    git_worktree_path_for_branch,
    task_kind,
)
from workspacectl.hooks import HookResult

ARGPARSE_ERROR_EXIT_CODE = 2
EXPECTED_HOOK_COUNT = 2


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


def git_output(*args: str, cwd: Path) -> str:
    return subprocess.run(  # noqa: S603 - test controls the Git arguments
        ["git", *args],  # noqa: S607 - git is the intended executable
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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


def make_remote_branch(repo: Path, branch: str, *, keep_local: bool = True) -> None:
    git("switch", "-c", branch, cwd=repo)
    (repo / "collaboration.txt").write_text(f"{branch}\n", encoding="utf-8")
    git("add", "collaboration.txt", cwd=repo)
    git("commit", "-m", f"add {branch}", cwd=repo)
    git("push", "--set-upstream", "origin", branch, cwd=repo)
    git("switch", "main", cwd=repo)
    if not keep_local:
        git("branch", "-D", branch, cwd=repo)


def advance_remote_branch(repo: Path, peer: Path, branch: str) -> None:
    remote = repo.parent / f"{repo.name}-origin.git"
    git("clone", str(remote), str(peer), cwd=repo.parent)
    git("config", "user.name", "Peer", cwd=peer)
    git("config", "user.email", "peer@example.com", cwd=peer)
    git("switch", branch, cwd=peer)
    (peer / "peer.txt").write_text("remote advance\n", encoding="utf-8")
    git("add", "peer.txt", cwd=peer)
    git("commit", "-m", "advance remote branch", cwd=peer)
    git("push", "origin", branch, cwd=peer)


def configure_workspace_project(root: Path, project_id: str, repo: Path) -> None:
    configure_workspace_projects(root, {project_id: repo})


def configure_workspace_projects(root: Path, projects_by_id: dict[str, Path]) -> None:
    projects = root / "projects"
    projects.mkdir(exist_ok=True)
    for project_id, repo in projects_by_id.items():
        (projects / project_id).symlink_to(repo, target_is_directory=True)
    (root / "workspace.json").write_text(
        json.dumps({"repositories": {project_id: {} for project_id in projects_by_id}}) + "\n",
        encoding="utf-8",
    )


def read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def write_agent_templates(root: Path) -> None:
    templates_root = root / "templates" / "agents"
    templates_root.mkdir(parents=True)
    (templates_root / "usage.md").write_text("# Usage Mode\n\nUse commands.\n", encoding="utf-8")
    (templates_root / "development.md").write_text(
        "# Development Mode\n\nChange code.\n", encoding="utf-8"
    )


class WorkspaceControllerTests(unittest.TestCase):
    def test_task_kind_defaults_legacy_manifests_to_requirement(self) -> None:
        assert task_kind({"requirement": {"slug": "legacy"}}) == TASK_KIND_REQUIREMENT

    def test_task_kind_rejects_unknown_explicit_kind(self) -> None:
        with pytest.raises(WorkspaceError, match="invalid task manifest kind"):
            task_kind({"kind": "unknown"})

    def test_requirement_manifest_writes_explicit_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = WorkspaceController(Path(tmp))
            manifest = controller.build_manifest(
                title="Typed Requirement",
                slug="typed-requirement",
                source_prd_url=None,
                scope=["alpha"],
                worktrees=[],
            )
            assert manifest["kind"] == TASK_KIND_REQUIREMENT

    def test_parse_project_branch_mappings_rejects_invalid_or_duplicate_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = WorkspaceController(Path(tmp))
            mappings = controller.parse_project_branch_mappings(
                ["android:merchant/alice/search", "ios:feature/search-ui"]
            )
            assert [(item.project_id, item.branch) for item in mappings] == [
                ("android", "merchant/alice/search"),
                ("ios", "feature/search-ui"),
            ]
            with pytest.raises(WorkspaceError, match="expected <project-id>:<branch>"):
                controller.parse_project_branch_mappings(["android"])
            with pytest.raises(WorkspaceError, match="duplicate project id"):
                controller.parse_project_branch_mappings(["android:one", "android:two"])

    def test_collaboration_preflight_rejects_unknown_project_and_invalid_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-a")
            configure_workspace_project(root, "alpha", source)
            controller = WorkspaceController(root)
            unknown = controller.parse_project_branch_mappings(["missing:feature/work"])
            with pytest.raises(WorkspaceError, match="repository not found"):
                controller.preflight_collaboration_branches(unknown)
            invalid = controller.parse_project_branch_mappings(["alpha:bad branch"])
            with pytest.raises(WorkspaceError, match="invalid Git branch name"):
                controller.preflight_collaboration_branches(invalid)

    def test_collaboration_preflight_fetches_missing_local_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-a")
            make_remote_branch(source, "feature/shared", keep_local=False)
            configure_workspace_project(root, "alpha", source)
            controller = WorkspaceController(root)
            mappings = controller.parse_project_branch_mappings(["alpha:feature/shared"])
            plans = controller.preflight_collaboration_branches(mappings)
            assert len(plans) == 1
            assert plans[0].relation == "missing"
            assert plans[0].remote_ref == "origin/feature/shared"
            assert git_local_branch_upstream(source, "feature/shared") is None
            assert git_worktree_path_for_branch(source, "feature/shared") is None

            missing = controller.parse_project_branch_mappings(["alpha:feature/missing"])
            with pytest.raises(WorkspaceError, match="unable to fetch collaboration branch"):
                controller.preflight_collaboration_branches(missing)

    def test_collaboration_preflight_classifies_equal_behind_and_ahead_branches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-a")
            branch = "feature/shared"
            make_remote_branch(source, branch)
            remote_ref = git_fetch_remote_branch(source, branch)
            assert git_branch_relation(source, branch, remote_ref) == "equal"

            advance_remote_branch(source, root / "peer", branch)
            remote_ref = git_fetch_remote_branch(source, branch)
            assert git_branch_relation(source, branch, remote_ref) == "behind"

            git("switch", branch, cwd=source)
            git("merge", "--ff-only", remote_ref, cwd=source)
            (source / "local.txt").write_text("local advance\n", encoding="utf-8")
            git("add", "local.txt", cwd=source)
            git("commit", "-m", "advance local branch", cwd=source)
            git("switch", "main", cwd=source)
            assert git_branch_relation(source, branch, remote_ref) == "ahead"

    def test_collaboration_preflight_rejects_upstream_divergence_and_occupied_branch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-a")
            branch = "feature/shared"
            make_remote_branch(source, branch)
            configure_workspace_project(root, "alpha", source)
            controller = WorkspaceController(root)
            mappings = controller.parse_project_branch_mappings([f"alpha:{branch}"])

            git("branch", "--set-upstream-to", "origin/main", branch, cwd=source)
            with pytest.raises(WorkspaceError, match="tracks 'origin/main'"):
                controller.preflight_collaboration_branches(mappings)
            git("branch", "--set-upstream-to", f"origin/{branch}", branch, cwd=source)

            git("switch", branch, cwd=source)
            with pytest.raises(WorkspaceError, match="already checked out"):
                controller.preflight_collaboration_branches(mappings)
            git("switch", "main", cwd=source)

            (source / "local.txt").write_text("local divergence\n", encoding="utf-8")
            git("switch", branch, cwd=source)
            git("add", "local.txt", cwd=source)
            git("commit", "-m", "local divergence", cwd=source)
            git("switch", "main", cwd=source)
            advance_remote_branch(source, root / "peer", branch)
            with pytest.raises(WorkspaceError, match="has diverged"):
                controller.preflight_collaboration_branches(mappings)

    def test_create_collaboration_builds_multiple_explicit_branch_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = make_repo(root / "src-alpha")
            beta = make_repo(root / "src-beta")
            make_remote_branch(alpha, "feature/alice-search", keep_local=False)
            make_remote_branch(beta, "merchant/bob/search-api", keep_local=False)
            local_agents = alpha / "AGENTS.local.md"
            local_agents.write_text("local collaboration guidance\n", encoding="utf-8")
            configure_workspace_projects(root, {"alpha": alpha, "beta": beta})

            code, out, err = run_cli(
                root,
                "create-collaboration",
                "--slug",
                "search-debug",
                "--project-branch",
                "alpha:feature/alice-search",
                "--project-branch",
                "beta:merchant/bob/search-api",
                "--task-date",
                "2026-09-01",
                "--json",
            )
            assert code == 0, (out, err)
            payload = json.loads(out)
            task_root = root / "worktrees" / "2026-09-01-search-debug"
            alpha_worktree = task_root / "alpha-search-debug"
            beta_worktree = task_root / "beta-search-debug"
            assert payload["kind"] == "collaboration"
            assert payload["collaboration"] == {"slug": "search-debug"}
            assert [(item["project_id"], item["branch"]) for item in payload["worktrees"]] == [
                ("alpha", "feature/alice-search"),
                ("beta", "merchant/bob/search-api"),
            ]
            manifest = read_yaml(task_root / ".workspace" / "task.yaml")
            assert manifest["kind"] == "collaboration"
            assert manifest["scope"] == ["alpha", "beta"]
            assert git_output("branch", "--show-current", cwd=alpha_worktree) == (
                "feature/alice-search"
            )
            assert git_output("branch", "--show-current", cwd=beta_worktree) == (
                "merchant/bob/search-api"
            )
            assert git_output(
                "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}",
                cwd=alpha_worktree,
            ) == "origin/feature/alice-search"
            assert (alpha_worktree / "AGENTS.local.md").resolve() == local_agents.resolve()
            agents = (alpha_worktree / ".workspace" / "AGENTS.md").read_text(encoding="utf-8")
            assert "collaboration `search-debug`" in agents
            assert "source PRD" not in agents
            assert "belongs to requirement" not in agents
            assert len(payload["hooks"]) == EXPECTED_HOOK_COUNT

            code, out, err = run_cli(
                root,
                "create-collaboration",
                "--slug",
                "search-debug",
                "--project-branch",
                "alpha:feature/alice-search",
            )
            assert code == 1
            assert "slug already exists" in (err or out)

    def test_create_collaboration_fast_forwards_and_sets_missing_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-a")
            branch = "feature/shared"
            make_remote_branch(source, branch)
            advance_remote_branch(source, root / "peer", branch)
            git("branch", "--unset-upstream", branch, cwd=source)
            configure_workspace_project(root, "alpha", source)

            code, out, err = run_cli(
                root,
                "create-collaboration",
                "--slug",
                "shared-work",
                "--project-branch",
                f"alpha:{branch}",
                "--task-date",
                "2026-09-01",
            )
            assert code == 0, (out, err)
            assert "alpha (feature/shared)" in out
            worktree = root / "worktrees" / "2026-09-01-shared-work" / "alpha-shared-work"
            assert git_output("rev-parse", "HEAD", cwd=worktree) == git_output(
                "rev-parse", f"origin/{branch}", cwd=worktree
            )
            assert git_output(
                "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}",
                cwd=worktree,
            ) == f"origin/{branch}"

    def test_create_collaboration_rolls_back_after_later_hook_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = make_repo(root / "src-alpha")
            beta = make_repo(root / "src-beta")
            alpha_branch = "feature/alpha-shared"
            beta_branch = "feature/beta-shared"
            make_remote_branch(alpha, alpha_branch, keep_local=False)
            make_remote_branch(beta, beta_branch, keep_local=False)
            configure_workspace_projects(root, {"alpha": alpha, "beta": beta})

            with patch(
                "workspacectl.core.run_post_create_hook",
                side_effect=[
                    HookResult(project="default", messages=["first hook ok"]),
                    WorkspaceError("second hook failed"),
                ],
            ):
                code, out, err = run_cli(
                    root,
                    "create-collaboration",
                    "--slug",
                    "rollback-work",
                    "--project-branch",
                    f"alpha:{alpha_branch}",
                    "--project-branch",
                    f"beta:{beta_branch}",
                    "--task-date",
                    "2026-09-01",
                )
            assert code == 1
            assert "second hook failed" in (err or out)
            assert not (root / "worktrees" / "2026-09-01-rollback-work").exists()
            assert not git_output("branch", "--list", alpha_branch, cwd=alpha)
            assert not git_output("branch", "--list", beta_branch, cwd=beta)
            assert git_output(
                "show-ref", "--verify", f"refs/remotes/origin/{alpha_branch}", cwd=alpha
            )
            assert git_output(
                "show-ref", "--verify", f"refs/remotes/origin/{beta_branch}", cwd=beta
            )

    def test_create_collaboration_preflight_failure_leaves_no_task_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            alpha = make_repo(root / "src-alpha")
            beta = make_repo(root / "src-beta")
            make_remote_branch(alpha, "feature/available", keep_local=False)
            configure_workspace_projects(root, {"alpha": alpha, "beta": beta})
            code, out, err = run_cli(
                root,
                "create-collaboration",
                "--slug",
                "preflight-failure",
                "--project-branch",
                "alpha:feature/available",
                "--project-branch",
                "beta:feature/missing",
                "--task-date",
                "2026-09-01",
            )
            assert code == 1
            assert "feature/missing" in (err or out)
            assert not (root / "worktrees" / "2026-09-01-preflight-failure").exists()
            assert not git_output("branch", "--list", "feature/available", cwd=alpha)

    def test_collaboration_status_and_update_preserve_mappings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-a")
            branch = "feature/shared"
            make_remote_branch(source, branch, keep_local=False)
            configure_workspace_project(root, "alpha", source)
            code, out, err = run_cli(
                root,
                "create-collaboration",
                "--slug",
                "status-work",
                "--project-branch",
                f"alpha:{branch}",
                "--task-date",
                "2026-09-01",
            )
            assert code == 0, (out, err)
            task_root = root / "worktrees" / "2026-09-01-status-work"
            worktree = task_root / "alpha-status-work"
            manifest_path = task_root / ".workspace" / "task.yaml"
            manifest_before = read_yaml(manifest_path)

            code, out, err = run_cli(root, "status", "--json")
            assert code == 0, (out, err)
            task = json.loads(out)["active_tasks"][0]
            assert task["kind"] == "collaboration"
            assert task["collaboration"] == {"slug": "status-work"}
            assert "requirement" not in task
            assert task["worktrees"][0]["branch"] == branch
            code, out, err = run_cli(root, "status")
            assert code == 0, (out, err)
            assert "collaboration status-work" in out
            assert f"alpha: {branch}" in out

            agents_path = worktree / ".workspace" / "AGENTS.md"
            agents_path.write_text("stale\n", encoding="utf-8")
            code, out, err = run_cli(root, "update", "--json")
            assert code == 0, (out, err)
            assert str(task_root.resolve()) in json.loads(out)["refreshed"]
            assert read_yaml(manifest_path) == manifest_before
            assert "collaboration `status-work`" in agents_path.read_text(encoding="utf-8")

    def test_doctor_reports_collaboration_manifest_and_worktree_problems(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-a")
            branch = "feature/shared"
            make_remote_branch(source, branch, keep_local=False)
            configure_workspace_project(root, "alpha", source)
            code, out, err = run_cli(
                root,
                "create-collaboration",
                "--slug",
                "doctor-work",
                "--project-branch",
                f"alpha:{branch}",
                "--task-date",
                "2026-09-01",
            )
            assert code == 0, (out, err)
            task_root = root / "worktrees" / "2026-09-01-doctor-work"
            worktree = task_root / "alpha-doctor-work"
            manifest_path = task_root / ".workspace" / "task.yaml"
            original = read_yaml(manifest_path)
            original_worktrees = copy.deepcopy(original["worktrees"])

            manifest = read_yaml(manifest_path)
            manifest["collaboration"] = {}
            write_yaml(manifest_path, manifest)
            code, out, _ = run_cli(root, "doctor", "--json")
            assert code == 1
            assert json.loads(out)["issues"][0]["code"] == "invalid_manifest"

            manifest = read_yaml(manifest_path)
            manifest["collaboration"] = original["collaboration"]
            manifest["worktrees"][0]["project_id"] = "missing"
            write_yaml(manifest_path, manifest)
            code, out, _ = run_cli(root, "doctor", "--json")
            assert code == 1
            assert "unknown_project" in {item["code"] for item in json.loads(out)["issues"]}

            manifest = read_yaml(manifest_path)
            manifest["worktrees"] = copy.deepcopy(original_worktrees)
            manifest["worktrees"][0]["path"] = "projects/alpha"
            write_yaml(manifest_path, manifest)
            code, out, _ = run_cli(root, "doctor", "--json")
            assert code == 1
            assert "worktree_outside_task_root" in {
                item["code"] for item in json.loads(out)["issues"]
            }

            manifest = read_yaml(manifest_path)
            manifest["worktrees"] = copy.deepcopy(original_worktrees)
            manifest["worktrees"][0]["branch"] = "feature/wrong"
            write_yaml(manifest_path, manifest)
            code, out, _ = run_cli(root, "doctor", "--json")
            assert code == 1
            assert "branch_mismatch" in {item["code"] for item in json.loads(out)["issues"]}

            write_yaml(manifest_path, original)
            git("worktree", "remove", str(worktree), cwd=source)
            code, out, _ = run_cli(root, "doctor", "--json")
            assert code == 1
            assert "missing_worktree" in {item["code"] for item in json.loads(out)["issues"]}
            worktree.mkdir()
            code, out, _ = run_cli(root, "doctor", "--json")
            assert code == 1
            assert "unregistered_worktree" in {
                item["code"] for item in json.loads(out)["issues"]
            }

    def test_cleanup_collaboration_protects_dirty_work_and_preserves_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-a")
            branch = "feature/shared"
            make_remote_branch(source, branch, keep_local=False)
            configure_workspace_project(root, "alpha", source)
            code, out, err = run_cli(
                root,
                "create-collaboration",
                "--slug",
                "cleanup-work",
                "--project-branch",
                f"alpha:{branch}",
                "--task-date",
                "2026-09-01",
            )
            assert code == 0, (out, err)
            task_root = root / "worktrees" / "2026-09-01-cleanup-work"
            worktree = task_root / "alpha-cleanup-work"
            (worktree / "dirty.txt").write_text("keep me\n", encoding="utf-8")

            code, out, err = run_cli(root, "cleanup", "--task-root", str(task_root))
            assert code == 1
            assert "dirty" in (err or out)
            assert worktree.exists()
            assert git_output("branch", "--list", branch, cwd=source)

            with patch(
                "workspacectl.core.run_pre_cleanup_hook",
                return_value=HookResult(project="default", messages=["cleanup hook"]),
            ) as cleanup_hook:
                code, out, err = run_cli(
                    root,
                    "cleanup",
                    "--task-root",
                    str(task_root),
                    "--force",
                    "--json",
                )
            assert code == 0, (out, err)
            payload = json.loads(out)
            assert payload["kind"] == "collaboration"
            assert payload["hooks"][0]["messages"] == ["cleanup hook"]
            cleanup_hook.assert_called_once()
            assert not task_root.exists()
            assert git_output("branch", "--list", branch, cwd=source)

    def test_requirement_only_commands_reject_collaboration_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-a")
            branch = "feature/shared"
            make_remote_branch(source, branch, keep_local=False)
            configure_workspace_project(root, "alpha", source)
            code, out, err = run_cli(
                root,
                "create-collaboration",
                "--slug",
                "fixed-work",
                "--project-branch",
                f"alpha:{branch}",
                "--task-date",
                "2026-09-01",
            )
            assert code == 0, (out, err)
            task_root = root / "worktrees" / "2026-09-01-fixed-work"
            worktree = task_root / "alpha-fixed-work"
            manifest_path = task_root / ".workspace" / "task.yaml"
            manifest_before = manifest_path.read_bytes()

            code, out, err = run_cli(
                root,
                "extend-requirement",
                "--task-root",
                str(task_root),
                "--add",
                "beta",
            )
            assert code == 1
            assert "does not support collaboration tasks" in (err or out)
            code, out, err = run_cli(
                root,
                "archive-requirement",
                "--task-root",
                str(task_root),
            )
            assert code == 1
            assert "does not support collaboration tasks" in (err or out)
            assert worktree.exists()
            assert manifest_path.read_bytes() == manifest_before
            assert not (root / "doc" / task_root.name).exists()

    def test_legacy_requirement_manifest_keeps_requirement_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-a")
            configure_workspace_project(root, "alpha", source)
            code, out, err = run_cli(
                root,
                "create-requirement",
                "--title",
                "Legacy Requirement",
                "--slug",
                "legacy-requirement",
                "--project",
                "alpha",
                "--task-date",
                "2026-09-01",
                "--git-username",
                "tester",
            )
            assert code == 0, (out, err)
            task_root = root / "worktrees" / "2026-09-01-legacy-requirement"
            manifest_path = task_root / ".workspace" / "task.yaml"
            manifest = read_yaml(manifest_path)
            manifest.pop("kind")
            write_yaml(manifest_path, manifest)

            code, out, err = run_cli(root, "status", "--json")
            assert code == 0, (out, err)
            task = json.loads(out)["active_tasks"][0]
            assert task["kind"] == "requirement"
            assert task["requirement"]["title"] == "Legacy Requirement"
            code, out, err = run_cli(
                root,
                "archive-requirement",
                "--task-root",
                str(task_root),
                "--json",
            )
            assert code == 0, (out, err)
            assert (root / "doc" / task_root.name).exists()

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

    def test_archive_moves_requirement_directory_and_removes_worktrees(self) -> None:
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
            (task_root / "deliverables").mkdir()
            (task_root / "deliverables" / "requirement.md").write_text(
                "alpha req", encoding="utf-8"
            )
            (task_root / "deliverables" / "implementation.md").write_text(
                "beta impl", encoding="utf-8"
            )
            code, out, _ = run_cli(
                root,
                "archive-requirement",
                "--task-root",
                str(task_root),
                "--json",
            )
            assert code == 0, out
            archive_payload = json.loads(out)
            archive_root = root / "doc" / "2026-08-24-merchant-search-flow"
            assert (archive_root / "deliverables" / "requirement.md").exists()
            assert (archive_root / "deliverables" / "implementation.md").exists()
            assert (archive_root / ".workspace" / "task.yaml").exists()
            assert not task_root.exists()
            assert not alpha_worktree.exists()
            assert not beta_worktree.exists()
            assert archive_payload["moved"] == {
                "source": str(task_root.resolve()),
                "target": str(archive_root.resolve()),
            }
            assert set(archive_payload["removed"]) == {
                str(alpha_worktree.resolve()),
                str(beta_worktree.resolve()),
            }
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

    def test_archive_does_not_require_doc_directory(self) -> None:
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
            code, out, err = run_cli(
                root,
                "archive-requirement",
                "--task-root",
                str(task_root),
            )
            assert code == 0, out
            assert not err
            assert "moved: " in out
            assert "removed worktrees:" in out
            assert not task_root.exists()

    def test_archive_requires_force_for_dirty_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-a")
            (root / "projects").mkdir()
            (root / "projects" / "alpha").symlink_to(source, target_is_directory=True)
            (root / "workspace.json").write_text(
                json.dumps({"repositories": {"alpha": {}}}) + "\n", encoding="utf-8"
            )
            code, out, _ = run_cli(
                root,
                "create-requirement",
                "--title",
                "Dirty Archive",
                "--project",
                "alpha",
                "--slug",
                "dirty-archive",
                "--task-date",
                "2026-08-24",
                "--git-username",
                "merchant",
            )
            assert code == 0, out
            task_root = root / "worktrees" / "2026-08-24-dirty-archive"
            worktree = task_root / "alpha-dirty-archive"
            (worktree / "README.md").write_text("dirty\n", encoding="utf-8")
            code, out, err = run_cli(root, "archive-requirement", "--task-root", str(task_root))
            assert code != 0
            assert "dirty" in (err or out)
            assert task_root.exists()
            code, out, err = run_cli(
                root,
                "archive-requirement",
                "--task-root",
                str(task_root),
                "--force",
                "--json",
            )
            assert code == 0, (out, err)
            assert not task_root.exists()

    def test_archive_rejects_existing_destination_before_removing_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-a")
            (root / "projects").mkdir()
            (root / "projects" / "alpha").symlink_to(source, target_is_directory=True)
            (root / "workspace.json").write_text(
                json.dumps({"repositories": {"alpha": {}}}) + "\n", encoding="utf-8"
            )
            code, out, _ = run_cli(
                root,
                "create-requirement",
                "--title",
                "Collision Archive",
                "--project",
                "alpha",
                "--slug",
                "collision-archive",
                "--task-date",
                "2026-08-24",
                "--git-username",
                "merchant",
            )
            assert code == 0, out
            task_root = root / "worktrees" / "2026-08-24-collision-archive"
            archive_root = root / "doc" / task_root.name
            archive_root.mkdir(parents=True)
            (archive_root / "existing.txt").write_text("keep\n", encoding="utf-8")
            worktree = task_root / "alpha-collision-archive"
            code, out, err = run_cli(root, "archive-requirement", "--task-root", str(task_root))
            assert code != 0
            assert "archive target already exists" in (err or out)
            assert worktree.exists()
            assert (archive_root / "existing.txt").read_text(encoding="utf-8") == "keep\n"

    def test_cleanup_removes_unarchived_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_repo(root / "src-a")
            (root / "projects").mkdir()
            (root / "projects" / "alpha").symlink_to(source, target_is_directory=True)
            (root / "workspace.json").write_text(
                json.dumps({"repositories": {"alpha": {}}}) + "\n", encoding="utf-8"
            )
            code, out, _ = run_cli(
                root,
                "create-requirement",
                "--title",
                "Unarchived Cleanup",
                "--project",
                "alpha",
                "--slug",
                "unarchived-cleanup",
                "--task-date",
                "2026-08-24",
                "--git-username",
                "merchant",
            )
            assert code == 0, out
            task_root = root / "worktrees" / "2026-08-24-unarchived-cleanup"
            worktree = task_root / "alpha-unarchived-cleanup"
            code, out, err = run_cli(root, "cleanup", "--task-root", str(task_root), "--json")
            assert code == 0, (out, err)
            assert not task_root.exists()
            assert not worktree.exists()
            git_executable = shutil.which("git")
            assert git_executable is not None
            branch_list = subprocess.run(  # noqa: S603 - executable is resolved from PATH
                [git_executable, "branch", "--list", "merchant/merchant/unarchived-cleanup"],
                cwd=str(source),
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout
            assert "merchant/merchant/unarchived-cleanup" in branch_list

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
