from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from repo_security import __version__
from repo_security.core import scan_repository, setup_repository
from repo_security.errors import RepoSecurityError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repository-local security scanners (M1 preview).")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="First installation into a fresh repository")
    setup.add_argument("path", type=Path)
    scan = commands.add_parser("scan", help="Scan Git snapshots with the installed local runtime")
    scan.add_argument("path", type=Path)
    scan.add_argument("--mode", choices=("staged", "head", "history"), default="staged")
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            root = setup_repository(args.path)
            print(f"Security tools and repository-local hooks installed: {root}")
            print("Review generated files, then run repo-security scan <repo> --mode history.")
            return 0
        return scan_repository(args.path, args.mode)
    except (RepoSecurityError, OSError, subprocess.CalledProcessError) as error:
        print(f"repo-security: {error}", file=sys.stderr)
        if args.command == "setup":
            print(
                "Setup incomplete. Inspect generated files; automatic recovery is planned for M2.",
                file=sys.stderr,
            )
        return 2
