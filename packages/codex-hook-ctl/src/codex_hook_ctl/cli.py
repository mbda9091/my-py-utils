# ruff: noqa: T201

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from codex_hook_ctl.core import inject_hook, record_hooks
from codex_hook_ctl.errors import HookCtlError

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-hook-ctl",
        description="Record and inject custom Codex hooks by name.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser(
        "record",
        help="Record all custom hooks from a source layer.",
    )
    _add_layer_arguments(record_parser)
    record_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite same-name records.",
    )

    inject_parser = subparsers.add_parser("inject", help="Inject one recorded hook by name.")
    inject_parser.add_argument("hook_name")
    _add_layer_arguments(inject_parser)
    inject_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite the target hook.",
    )
    return parser


def _add_layer_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--global", dest="global_layer", action="store_true", help="Use ~/.codex.")
    group.add_argument("--directory", type=Path, help="Use the exact directory's .codex layer.")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "record":
            records = record_hooks(Path.cwd(), args.directory, force=args.force)
            for record in records:
                print(f"Recorded hook: {record.name}")
            if not records:
                print("No custom hooks found.")
            return 0
        if args.command == "inject":
            target_file, config_path = inject_hook(
                Path.cwd(),
                args.directory,
                args.hook_name,
                force=args.force,
            )
            print(f"Injected hook: {args.hook_name}")
            print(f"file: {target_file}")
            print(f"config: {config_path}")
            return 0
        raise HookCtlError(f"Unhandled command: {args.command}")  # noqa: EM102,TRY003,TRY301
    except HookCtlError as exc:
        print(str(exc), file=sys.stderr)
        return 1
