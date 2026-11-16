from __future__ import annotations

import argparse
import subprocess
import sys
import tarfile
from pathlib import Path

from repo_security import __version__
from repo_security.core import doctor_repository, plan_repository, scan_repository, setup_repository
from repo_security.errors import RepoSecurityError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Repository-local security scanners and Git hooks."
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="Install or verify repository security configuration")
    setup.add_argument("path", type=Path)
    setup.add_argument("--dry-run", action="store_true", help="Read-only installation plan")
    setup.add_argument(
        "--github-actions", action="store_true", help="Also manage the GitHub workflow"
    )
    doctor = commands.add_parser("doctor", help="Diagnose configuration and real scanner health")
    doctor.add_argument("path", type=Path)
    scan = commands.add_parser("scan", help="Scan Git snapshots with the installed local runtime")
    scan.add_argument("path", type=Path)
    scan.add_argument("--mode", choices=("staged", "head", "history"), default="staged")
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            plan = plan_repository(args.path, github_actions=args.github_actions)
            for line in plan.describe():
                print(line, flush=True)
            if args.dry_run:
                return 2 if plan.conflicts else 0
            root = setup_repository(args.path, github_actions=args.github_actions)
            print(f"Security tools and repository-local hooks verified: {root}")
            print("Review generated files, then run repo-security scan <repo> --mode history.")
            return 0
        if args.command == "doctor":
            healthy, messages = doctor_repository(args.path)
            for message in messages:
                print(message)
            return 0 if healthy else 1
        return scan_repository(args.path, args.mode)
    except (
        RepoSecurityError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
        tarfile.TarError,
    ) as error:
        print(f"repo-security: {error}", file=sys.stderr)
        if args.command == "setup" and not args.dry_run:
            print(
                "Inspect .security/install-manifest.json and rerun setup after resolving conflicts."
                " New files, partial dependencies and uv caches may remain.",
                file=sys.stderr,
            )
        return 2
