"""Entry point for the example CLI."""

from __future__ import annotations

import argparse

from my_py_utils_common import greeting


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hello-util")
    parser.add_argument("--name", default="world", help="Name to greet.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    print(greeting(args.name))
