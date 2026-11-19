from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from skill_sync.adder import add_skill
from skill_sync.catalog import list_local_skills
from skill_sync.config import read_config
from skill_sync.errors import LegacyConfigError, SkillSyncError
from skill_sync.git_repo import sync_repository
from skill_sync.installations import installation_status, read_managed_installations
from skill_sync.installer import install_skill
from skill_sync.publisher import publish_skill
from skill_sync.repository import init_repository, read_repository_config
from skill_sync.validation import validate_local_skill

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import TextIO

    from skill_sync.installer import InstallResult
    from skill_sync.repository import RepositoryConfig
    from skill_sync.security import SecurityFinding


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skill-sync",
        description="Manage skills from a configured personal Git repository.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Save the single local repository directory.")
    init_parser.add_argument("--repo", required=True, type=Path)

    subparsers.add_parser("status", help="Show the configured personal repository.")
    subparsers.add_parser("sync", help="Clone or pull the configured registry.")
    subparsers.add_parser("list", help="List skills in the configured personal repository.")

    validate_parser = subparsers.add_parser("validate", help="Validate a local skill directory.")
    validate_parser.add_argument("local_skill_dir", type=Path)

    add_parser = subparsers.add_parser(
        "add",
        help="Move a local skill into the personal repository and link it back.",
    )
    add_parser.add_argument("local_skill_dir", type=Path)

    publish_parser = subparsers.add_parser("publish", help="Publish a local skill.")
    publish_parser.add_argument("local_skill_dir", type=Path)
    publish_parser.add_argument("--update", action="store_true")
    publish_parser.add_argument("--no-push", action="store_true")

    install_parser = subparsers.add_parser(
        "install",
        help="Link a personal repository skill into the local skills directory.",
    )
    install_parser.add_argument("skill_name", nargs="?")
    install_parser.add_argument("--all", action="store_true", dest="install_all")
    install_parser.add_argument("--target", required=True)
    install_parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run_command(args, sys.stdout, sys.stderr)
    except (SkillSyncError, OSError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1


def _run_command(args: argparse.Namespace, output: TextIO, error_output: TextIO) -> int:
    command = args.command
    if command == "init":
        config = init_repository(args.repo)
        output.write(f"Configured personal repository: {config.repository_path}\n")
        output.write(f"configuration: {config.state_home / 'config.json'}\n")
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
        output.write("skill structure is valid:\n")
        output.write(f"name: {metadata.name}\n")
        output.write(f"description: {metadata.description}\n")
        output.write(f"path: {metadata.path}\n")
    elif command == "add":
        _run_add(args.local_skill_dir, output)
    elif command == "publish":
        _run_publish(args, output)
    elif command == "install":
        return _run_install(args, output, error_output)
    else:
        msg = f"Unhandled command: {command}"
        raise SkillSyncError(msg)
    return 0


def _run_add(path: Path, output: TextIO) -> None:
    output.write("Keep this skill unchanged until the add operation finishes.\n")
    result = add_skill(path, read_repository_config())
    action = "Added skill" if result.changed else "Skill already managed"
    output.write(f"{action}: {result.name}\n")
    output.write(f"path: {result.path}\n")
    output.write(f"repository path: {result.registry_path}\n")


def _run_publish(args: argparse.Namespace, output: TextIO) -> None:
    result = publish_skill(
        args.local_skill_dir,
        read_config(),
        update=args.update,
        push=not args.no_push,
    )
    _write_warnings(output, result.warnings)
    if result.changed:
        output.write(f"Published skill: {result.name}\n")
        output.write(f"registry path: {result.registry_path}\n")
    else:
        output.write(f"Registry already up to date for skill: {result.name}\n")
    output.write(f"pushed: {str(result.pushed).lower()}\n")


def _run_install(args: argparse.Namespace, output: TextIO, error_output: TextIO) -> int:
    skill_name = args.skill_name
    install_all = bool(args.install_all)
    if install_all:
        if skill_name is not None:
            msg = "Pass either a skill name or --all, not both."
            raise SkillSyncError(msg)
        config = read_repository_config()
        return _install_all(
            config,
            target=args.target,
            force=args.force,
            output=output,
            error_output=error_output,
        )

    if not isinstance(skill_name, str):
        msg = "Pass a skill name or --all."
        raise SkillSyncError(msg)

    config = read_repository_config()
    result = install_skill(
        skill_name,
        config,
        target=args.target,
        force=args.force,
    )
    _write_install_result(output, result)
    return 0


def _install_all(
    config: RepositoryConfig,
    *,
    target: str,
    force: bool,
    output: TextIO,
    error_output: TextIO,
) -> int:
    skills = list_local_skills(config.repository_path)
    if not skills:
        output.write("No skills found.\n")
        return 0

    installed = 0
    failed = 0
    for skill in skills:
        try:
            result = install_skill(skill.name, config, target=target, force=force)
        except SkillSyncError as exc:
            error_output.write(f"[ERROR] {exc}\n")
            failed += 1
            continue
        _write_install_result(output, result)
        installed += 1
    output.write(f"Installed: {installed}; failed: {failed}\n")
    return int(failed > 0)


def _write_install_result(output: TextIO, result: InstallResult) -> None:
    output.write(f"Installed skill: {result.name}\n")
    output.write(f"target: {result.target}\n")
    output.write(f"path: {result.path}\n")
    output.write(f"source: {result.source_path}\n")


def _write_status(output: TextIO) -> None:
    try:
        config = read_repository_config()
    except LegacyConfigError as exc:
        output.write(f"{exc}\n")
        return
    output.write(f"repository: {config.repository_path}\n")
    output.write(f"configuration: {config.state_home / 'config.json'}\n")
    output.write(f"skills: {len(list_local_skills(config.repository_path))}\n")
    records = read_managed_installations(config.state_home)
    output.write(f"installations: {len(records)}\n")
    for record in sorted(records.values(), key=lambda item: str(item.path)):
        status = installation_status(record, config.repository_path)
        source = str(record.source_path) if record.source_path is not None else "unknown"
        output.write(f"installation: {record.skill}\t{status}\t{record.path}\t{source}\n")


def _write_list(output: TextIO) -> None:
    config = read_repository_config()
    skills = list_local_skills(config.repository_path)
    if not skills:
        output.write("No skills found.\n")
        return
    output.writelines(f"{skill.name}\t{skill.description}\t{skill.path}\n" for skill in skills)


def _write_warnings(output: TextIO, warnings: Sequence[SecurityFinding]) -> None:
    output.writelines(f"[WARN] {warning.path}: {warning.reason}\n" for warning in warnings)
