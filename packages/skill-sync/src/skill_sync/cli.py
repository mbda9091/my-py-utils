from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from skill_sync.adder import add_skill
from skill_sync.catalog import find_local_skill, list_local_skill_names
from skill_sync.config import get_state_home
from skill_sync.errors import LegacyConfigError, SkillSyncError, TargetSkillExistsError
from skill_sync.installations import installation_status, read_managed_installations
from skill_sync.installer import install_skill
from skill_sync.locking import mutation_lock
from skill_sync.repository import init_repository, read_repository_config
from skill_sync.uninstaller import uninstall_skill
from skill_sync.validation import validate_local_skill

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import TextIO

    from skill_sync.installer import InstallResult
    from skill_sync.repository import RepositoryConfig
    from skill_sync.validation import SkillMetadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skill-sync",
        description="Manage skills from a configured personal Git repository.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Save the single local repository directory.")
    init_parser.add_argument("--repo", required=True, type=Path)

    subparsers.add_parser("status", help="Show the configured personal repository.")
    subparsers.add_parser("list", help="List skills in the configured personal repository.")

    validate_parser = subparsers.add_parser("validate", help="Validate a local skill directory.")
    validate_parser.add_argument("local_skill_dir", type=Path)

    add_parser = subparsers.add_parser(
        "add",
        help="Move a local skill into the personal repository and link it back.",
    )
    add_parser.add_argument("local_skill_dir", type=Path)
    add_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing same-name personal repository skill.",
    )

    install_parser = subparsers.add_parser(
        "install",
        help="Link a personal repository skill into the local skills directory.",
    )
    install_parser.add_argument("skill_name", nargs="?")
    install_parser.add_argument("--all", action="store_true", dest="install_all")
    install_parser.add_argument("--force", action="store_true")
    uninstall_parser = subparsers.add_parser(
        "uninstall", help="Remove a personal skill link from ~/.agents/skills."
    )
    uninstall_parser.add_argument("skill_name")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {"publish", "sync"}:
        parser.error(
            f"{arguments[0]} has been removed. Manage Git commits and remotes yourself; "
            "use add/install for local skill management."
        )
    args = parser.parse_args(arguments)
    if args.command == "install" and (bool(args.skill_name) == bool(args.install_all)):
        parser.error("Pass a skill name or --all, not both.")
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
        _write_status(output, error_output)
    elif command == "list":
        _write_list(output, error_output)
    elif command == "validate":
        metadata = validate_local_skill(args.local_skill_dir)
        output.write("skill structure is valid:\n")
        output.write(f"name: {metadata.name}\n")
        output.write(f"description: {metadata.description}\n")
        output.write(f"path: {metadata.path}\n")
    elif command == "add":
        with mutation_lock(get_state_home()):
            _run_add(args.local_skill_dir, output, error_output, force=args.force)
    elif command == "install":
        with mutation_lock(get_state_home()):
            return _run_install(args, output, error_output)
    elif command == "uninstall":
        with mutation_lock(get_state_home()):
            result = uninstall_skill(args.skill_name, read_repository_config(allow_missing=True))
        if result.skipped:
            error_output.write(
                f"[WARN] Skipped uninstall; existing content preserved: {result.path}\n"
            )
        else:
            action = "Uninstalled skill" if result.changed else "Skill link already absent"
            output.write(f"{action}: {result.name}\npath: {result.path}\n")
        error_output.writelines(
            f"[WARN] Uninstalled; inspect remaining cleanup files: {path}\n"
            for path in result.cleanup_paths
        )
    else:
        msg = f"Unhandled command: {command}"
        raise SkillSyncError(msg)
    return 0


def _run_add(path: Path, output: TextIO, error_output: TextIO, *, force: bool) -> None:
    output.write("Keep this skill unchanged until the add operation finishes.\n")
    result = add_skill(path, read_repository_config(), force=force)
    action = "Added skill" if result.changed else "Skill already managed"
    output.write(f"{action}: {result.name}\n")
    output.write(f"path: {result.path}\n")
    output.write(f"repository path: {result.repository_path}\n")
    if result.cleanup_path is not None:
        error_output.write(
            "[WARN] Skill added successfully, but the old repository backup could not be "
            f"fully removed. Inspect remaining files at: {result.cleanup_path}\n"
        )


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
            force=args.force,
            output=output,
            error_output=error_output,
        )

    if not isinstance(skill_name, str):
        msg = "Pass a skill name or --all."
        raise SkillSyncError(msg)

    config = read_repository_config()
    try:
        result = install_skill(
            skill_name,
            config,
            force=args.force,
        )
    except TargetSkillExistsError as exc:
        error_output.write(f"[WARN] Skipped skill: {skill_name}. {exc}\n")
        return 0
    _write_install_result(output, error_output, result)
    return 0


def _install_all(
    config: RepositoryConfig,
    *,
    force: bool,
    output: TextIO,
    error_output: TextIO,
) -> int:
    names = list_local_skill_names(config.repository_path)
    if not names:
        output.write("No skills found.\n")
        return 0

    installed = 0
    skipped = 0
    failed = 0
    for name in names:
        try:
            result = install_skill(name, config, force=force)
        except TargetSkillExistsError as exc:
            error_output.write(f"[WARN] Skipped skill: {name}. {exc}\n")
            skipped += 1
            continue
        except SkillSyncError as exc:
            error_output.write(f"[ERROR] {name}: {exc}\n")
            failed += 1
            continue
        _write_install_result(output, error_output, result)
        installed += 1
    output.write(f"Installed: {installed}; skipped: {skipped}; failed: {failed}\n")
    return int(failed > 0)


def _write_install_result(output: TextIO, error_output: TextIO, result: InstallResult) -> None:
    output.write(f"Installed skill: {result.name}\n")
    output.write(f"path: {result.path}\n")
    output.write(f"source: {result.source_path}\n")
    if result.backup_path is not None:
        output.write(f"backup: {result.backup_path}\n")
    if result.cleanup_path is not None:
        error_output.write(
            f"[WARN] Installation succeeded; inspect cleanup files: {result.cleanup_path}\n"
        )


def _write_status(output: TextIO, error_output: TextIO) -> None:
    try:
        config = read_repository_config()
    except LegacyConfigError as exc:
        output.write(f"{exc}\n")
        return
    output.write(f"repository: {config.repository_path}\n")
    output.write(f"configuration: {config.state_home / 'config.json'}\n")
    skills, invalid = _inspect_catalog(config, error_output)
    output.write(f"skills: {len(skills)}\n")
    if invalid:
        output.write(f"invalid skills: {invalid}\n")
    records = read_managed_installations(config.state_home)
    output.write(f"installations: {len(records)}\n")
    for record in sorted(records.values(), key=lambda item: str(item.path)):
        status = installation_status(record, config.repository_path)
        source = str(record.source_path) if record.source_path is not None else "unknown"
        output.write(f"installation: {record.skill}\t{status}\t{record.path}\t{source}\n")


def _write_list(output: TextIO, error_output: TextIO) -> None:
    config = read_repository_config()
    skills, invalid = _inspect_catalog(config, error_output)
    if not skills:
        output.write("No valid skills found.\n" if invalid else "No skills found.\n")
        return
    output.writelines(f"{skill.name}\t{skill.description}\t{skill.path}\n" for skill in skills)


def _inspect_catalog(
    config: RepositoryConfig, error_output: TextIO
) -> tuple[list[SkillMetadata], int]:
    skills: list[SkillMetadata] = []
    invalid = 0
    for name in list_local_skill_names(config.repository_path):
        try:
            skills.append(find_local_skill(config.repository_path, name))
        except SkillSyncError as exc:
            error_output.write(f"[WARN] Invalid skill: {name}. {exc}\n")
            invalid += 1
    return skills, invalid
