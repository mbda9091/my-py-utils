from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .core import WorkspaceController, WorkspaceError


def add_common_output_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")


def add_task_root_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--task-root",
        required=True,
        help="Task root path, relative to the workspace root or absolute.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workspacectl")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init", help="Initialize workspace skeleton and inject usage AGENTS if missing."
    )
    add_common_output_args(init_parser)

    update_parser = subparsers.add_parser("update", help="Validate and refresh workspace state.")
    add_common_output_args(update_parser)

    create_parser = subparsers.add_parser(
        "create-requirement", help="Create a requirement and its worktrees."
    )
    create_parser.add_argument("--title", required=True, help="Requirement title from the PRD.")
    create_parser.add_argument("--source-prd-url", required=True, help="Source PRD URL.")
    create_parser.add_argument(
        "--project",
        action="append",
        required=True,
        dest="projects",
        help="Project id to include in the requirement scope. Repeatable.",
    )
    create_parser.add_argument("--slug", help="Explicit requirement slug.")
    create_parser.add_argument("--task-date", help="Override the task-root date prefix.")
    create_parser.add_argument("--git-username", help="Override the branch username segment.")
    create_parser.add_argument("--base-ref", help="Override the base ref used for new worktrees.")
    create_parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help=("Reuse an active task root with the same slug, title, PRD URL, and scope."),
    )
    add_common_output_args(create_parser)

    extend_parser = subparsers.add_parser("extend-requirement", help="Adjust a requirement scope.")
    add_task_root_arg(extend_parser)
    extend_parser.add_argument(
        "--add", action="append", default=[], dest="add_ids", help="Project id to add."
    )
    extend_parser.add_argument(
        "--remove", action="append", default=[], dest="remove_ids", help="Project id to remove."
    )
    extend_parser.add_argument("--git-username", help="Override the branch username segment.")
    extend_parser.add_argument(
        "--base-ref", help="Override the base ref used for newly added worktrees."
    )
    add_common_output_args(extend_parser)

    archive_parser = subparsers.add_parser("archive-requirement", help="Archive worktree docs.")
    add_task_root_arg(archive_parser)
    archive_parser.add_argument(
        "--discard-missing-docs",
        action="store_true",
        help="Archive even if some worktree doc trees are missing.",
    )
    add_common_output_args(archive_parser)

    cleanup_parser = subparsers.add_parser(
        "cleanup", help="Remove task-root worktrees and the task root directory."
    )
    add_task_root_arg(cleanup_parser)
    cleanup_parser.add_argument("--force", action="store_true", help="Remove dirty worktrees.")
    add_common_output_args(cleanup_parser)

    status_parser = subparsers.add_parser("status", help="Show workspace state.")
    add_common_output_args(status_parser)

    doctor_parser = subparsers.add_parser("doctor", help="Run workspace health checks.")
    add_common_output_args(doctor_parser)

    mode_parser = subparsers.add_parser(
        "set-agent-mode", help="Replace AGENTS.md with a mode template."
    )
    mode_parser.add_argument("mode", choices=("usage", "development"), help="Agent guide mode.")
    add_common_output_args(mode_parser)

    return parser


# Keep human-readable output centralized so every command has a stable presentation.
def emit(  # noqa: C901, PLR0912, PLR0915
    result: dict[str, Any], *, json_mode: bool
) -> int:
    if json_mode:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok", True) else 1
    command = result.get("command")
    if command == "init":
        print(f"workspace initialized: {result['workspace_root']}")
        if result.get("created"):
            print("created:")
            for item in result["created"]:
                print(f"  - {item}")
        else:
            print("nothing created")
    elif command == "update":
        print(f"workspace updated: {result['workspace_root']}")
        if result.get("refreshed"):
            print("refreshed:")
            for item in result["refreshed"]:
                print(f"  - {item}")
    elif command == "create-requirement":
        print(f"task root: {result['task_root']}")
        print(f"branch: {result['branch']}")
        print(f"source PRD URL: {result['requirement']['source_prd_url']}")
        print("worktrees:")
        for item in result["worktrees"]:
            print(f"  - {item['project_id']}: {item['path']}")
    elif command == "extend-requirement":
        print(f"task root: {result['task_root']}")
        print(f"branch: {result['branch']}")
        print("worktrees:")
        for item in result["worktrees"]:
            print(f"  - {item['project_id']}: {item['path']}")
    elif command == "archive-requirement":
        print(f"task root: {result['task_root']}")
        print(f"archive root: {result['archive_root']}")
        if result.get("moved"):
            print("moved:")
            for item in result["moved"]:
                print(f"  - {item['project_id']}: {item['target']}")
        if result.get("missing"):
            print("missing:")
            for item in result["missing"]:
                print(f"  - {item}")
    elif command == "cleanup":
        print(f"task root: {result['task_root']}")
        if result.get("removed"):
            print("removed:")
            for item in result["removed"]:
                print(f"  - {item}")
    elif command == "status":
        print(f"workspace root: {result['workspace_root']}")
        print("repositories:")
        for repo in result.get("repositories", []):
            state = "ok"
            if repo.get("error"):
                state = "error"
            elif repo.get("dirty"):
                state = "dirty"
            print(f"  - {repo['repo_id']} ({state})")
        print("active tasks:")
        for task in result.get("active_tasks", []):
            print(f"  - {task['task_root_name']} -> {task['requirement'].get('title', '')}")
        print("archived tasks:")
        for task in result.get("archived_tasks", []):
            print(f"  - {task['task_root_name']}")
    elif command == "doctor":
        print(f"workspace root: {result['workspace_root']}")
        if result.get("ok"):
            print("ok")
        else:
            print("issues:")
            for issue in result.get("issues", []):
                print(f"  - {issue['severity']} {issue['code']}: {issue['message']}")
    elif command == "set-agent-mode":
        print(f"agent mode: {result['mode']}")
        print(f"AGENTS.md: {result['agents_path']}")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok", True) else 1


# Dispatch stays in one place to preserve identical JSON and text command behavior.
def run_command(  # noqa: C901, PLR0911, PLR0912, PLR0915
    args: argparse.Namespace,
) -> dict[str, Any]:
    controller = WorkspaceController()
    if args.command == "init":
        result = controller.ensure_init(create_config=True)
        result["command"] = "init"
        result["ok"] = True
        return result
    if args.command == "update":
        report = controller.doctor_report()
        refreshed: list[str] = []
        for task_root in controller.active_task_roots():
            try:
                manifest = controller.load_task_manifest(task_root)
                controller.save_task_manifest(task_root, manifest)
                for entry in manifest.get("worktrees", []):
                    project_id = entry.get("project_id")
                    relative_path = entry.get("path")
                    if not isinstance(project_id, str) or not isinstance(relative_path, str):
                        continue
                    binding = controller.bind_repository(project_id)
                    worktree_path = (controller.root / relative_path).resolve()
                    controller.ensure_worktree_excludes(worktree_path)
                    controller.write_agents(
                        task_root=task_root,
                        manifest=manifest,
                        worktree=controller.manifest_to_worktree_binding(entry),
                        repo_root=binding.repo_root,
                    )
                refreshed.append(str(task_root))
            except WorkspaceError:
                continue
        report["command"] = "update"
        report["refreshed"] = refreshed
        return report
    if args.command == "create-requirement":
        result = controller.create_requirement(
            title=args.title,
            source_prd_url=args.source_prd_url,
            project_ids=args.projects,
            slug=args.slug,
            task_date=args.task_date,
            git_username=args.git_username,
            base_ref=args.base_ref,
            reuse_existing=args.reuse_existing,
        )
        result["command"] = "create-requirement"
        result["ok"] = True
        return result
    if args.command == "extend-requirement":
        task_root = controller.resolve_task_root(args.task_root)
        result = controller.extend_requirement(
            task_root=task_root,
            add_ids=args.add_ids,
            remove_ids=args.remove_ids,
            git_username=args.git_username,
            base_ref=args.base_ref,
        )
        result["command"] = "extend-requirement"
        result["ok"] = True
        return result
    if args.command == "archive-requirement":
        task_root = controller.resolve_task_root(args.task_root)
        result = controller.archive_requirement(
            task_root=task_root,
            discard_missing_docs=args.discard_missing_docs,
        )
        result["command"] = "archive-requirement"
        result["ok"] = True
        return result
    if args.command == "cleanup":
        task_root = controller.resolve_task_root(args.task_root)
        result = controller.cleanup_requirement(task_root=task_root, force=args.force)
        result["command"] = "cleanup"
        result["ok"] = True
        return result
    if args.command == "status":
        result = controller.status_report()
        result["command"] = "status"
        result["ok"] = True
        return result
    if args.command == "doctor":
        result = controller.doctor_report()
        result["command"] = "doctor"
        return result
    if args.command == "set-agent-mode":
        result = controller.set_agent_mode(args.mode)
        result["command"] = "set-agent-mode"
        result["ok"] = True
        return result
    message = f"unknown command: {args.command}"
    raise WorkspaceError(message)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run_command(args)
    except WorkspaceError as exc:
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {"ok": False, "command": getattr(args, "command", ""), "error": str(exc)},
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(str(exc), file=sys.stderr)
        return 1
    return emit(result, json_mode=getattr(args, "json", False))
