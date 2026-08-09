from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from skill_sync.catalog import list_skills
from skill_sync.config import init_config, read_config, read_installations
from skill_sync.errors import SkillSyncError
from skill_sync.git_repo import repository_status, sync_repository
from skill_sync.installer import install_skill
from skill_sync.publisher import publish_skill
from skill_sync.validation import validate_local_skill

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import TextIO

    from skill_sync.security import SecurityFinding


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skill-sync",
        description="Personal Git-backed sync CLI for Codex skills.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Save registry configuration.")
    init_parser.add_argument("git_url")
    init_parser.add_argument("--branch", default="main")
    init_parser.add_argument("--git-name", help="Git author name for registry commits.")
    init_parser.add_argument("--git-email", help="Git author email for registry commits.")

    subparsers.add_parser("status", help="Show configuration and cache status.")
    subparsers.add_parser("sync", help="Clone or pull the configured registry.")
    subparsers.add_parser("list", help="List skills in the registry cache.")

    validate_parser = subparsers.add_parser("validate", help="Validate a local skill directory.")
    validate_parser.add_argument("local_skill_dir", type=Path)

    publish_parser = subparsers.add_parser("publish", help="Publish a local skill.")
    publish_parser.add_argument("local_skill_dir", type=Path)
    publish_parser.add_argument("--update", action="store_true")
    publish_parser.add_argument("--no-push", action="store_true")

    install_parser = subparsers.add_parser("install", help="Install a registry skill locally.")
    install_parser.add_argument("skill_name")
    install_parser.add_argument("--target", required=True)
    install_parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run_command(args, sys.stdout)
    except SkillSyncError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1


def _run_command(args: argparse.Namespace, output: TextIO) -> int:
    command = args.command
    if command == "init":
        config = init_config(
            args.git_url,
            branch=args.branch,
            git_name=args.git_name,
            git_email=args.git_email,
        )
        output.write(f"Configured registry: {config.repository}\n")
        output.write(f"branch: {config.branch}\n")
        output.write(f"checkout: {config.checkout}\n")
        if config.git_identity is not None:
            output.write(
                f"git identity: {config.git_identity.name} <{config.git_identity.email}>\n",
            )
    elif command == "status":
        _write_status(output)
    elif command == "sync":
        config = read_config()
        commit = sync_repository(config)
        output.write(f"Synced registry at commit: {commit}\n")
    elif command == "list":
        _write_list(output)
    elif command == "validate":
        metadata = validate_local_skill(args.local_skill_dir)
        output.write("skill can be published:\n")
        output.write(f"name: {metadata.name}\n")
        output.write(f"description: {metadata.description}\n")
        output.write(f"path: {metadata.path}\n")
    elif command == "publish":
        config = read_config()
        result = publish_skill(
            args.local_skill_dir,
            config,
            update=args.update,
            push=not args.no_push,
        )
        _write_warnings(output, result.warnings)
        if not result.changed:
            output.write(f"Registry already up to date for skill: {result.name}\n")
        else:
            output.write(f"Published skill: {result.name}\n")
            output.write(f"registry path: {result.registry_path}\n")
            output.write(f"pushed: {str(result.pushed).lower()}\n")
    elif command == "install":
        config = read_config()
        result = install_skill(
            args.skill_name,
            config,
            target=args.target,
            force=args.force,
        )
        output.write(f"Installed skill: {result.name}\n")
        output.write(f"target: {result.target}\n")
        output.write(f"path: {result.path}\n")
        output.write(f"commit: {result.commit}\n")
    else:
        msg = f"Unhandled command: {command}"
        raise SkillSyncError(msg)
    return 0


def _write_status(output: TextIO) -> None:
    config = read_config()
    status = repository_status(config)
    installations = read_installations(config.state_home)
    output.write(f"registry: {config.repository}\n")
    output.write(f"branch: {config.branch}\n")
    output.write(f"checkout: {config.checkout}\n")
    if config.git_identity is None:
        output.write("git identity: not configured\n")
    else:
        output.write(f"git identity: {config.git_identity.name} <{config.git_identity.email}>\n")
    output.write(f"cache exists: {str(status.exists).lower()}\n")
    if status.exists:
        output.write(f"commit: {status.commit}\n")
        output.write(f"dirty: {str(status.dirty).lower()}\n")
    output.write(f"installations: {len(installations)}\n")


def _write_list(output: TextIO) -> None:
    config = read_config()
    skills = list_skills(config.checkout)
    if not skills:
        output.write("No skills found.\n")
        return
    for skill in skills:
        compatibility = ",".join(skill.compatibility)
        output.write(
            f"{skill.name}\t{skill.description}\t{compatibility}\t{skill.updated_at}\n",
        )


def _write_warnings(output: TextIO, warnings: Sequence[SecurityFinding]) -> None:
    output.writelines(f"[WARN] {warning.path}: {warning.reason}\n" for warning in warnings)
