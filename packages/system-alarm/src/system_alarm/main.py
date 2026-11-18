from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from system_alarm.core import DEFAULT_SHORTCUT, DEFAULT_TIMEOUT, create_alarm
from system_alarm.models import AlarmError

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="system-alarm",
        description="Create native system alarms. Currently supported on macOS via Shortcuts.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create a non-repeating alarm.")
    create.add_argument("--time", required=True, help="24-hour HH:MM in the local timezone.")
    create.add_argument("--label", default="Alarm", help="Alarm label. Default: Alarm.")
    create.add_argument(
        "--shortcut",
        default=DEFAULT_SHORTCUT,
        help=f"macOS shortcut name or ID. Default: {DEFAULT_SHORTCUT}.",
    )
    create.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="macOS shortcut execution timeout in seconds (default: 60).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = create_alarm(
            args.time, label=args.label, shortcut=args.shortcut, timeout=args.timeout
        )
    except AlarmError as exc:
        print(f"system-alarm: {exc}", file=sys.stderr)
        return 1
    if result.shortcut is not None:
        print(f"Shortcut completed: {result.time} — {result.label}. Verify the alarm in Clock.")
    else:
        print(f"Alarm request completed: {result.time} — {result.label}.")
    return 0
