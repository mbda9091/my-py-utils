from __future__ import annotations

import argparse
import sys
from pathlib import Path

from private_pack.manifest import PackError
from private_pack.workflow import decrypt_config, keygen, pack


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pack per-item encrypted archives and a manifest.")
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("keygen", help="Create a private 32-byte master key.")
    generate.add_argument("--output", type=Path, required=True)
    archive = commands.add_parser("pack", help="Pack immediate children (Linux only).")
    archive.add_argument("source", type=Path)
    archive.add_argument(
        "--output",
        type=Path,
        help="Output directory (default: <input-name>-packed beside the input directory).",
    )
    archive.add_argument("--key-file", type=Path, required=True)
    decrypt = commands.add_parser("decrypt-config", help="Decrypt the manifest only.")
    decrypt.add_argument("source", type=Path)
    decrypt.add_argument("--output", type=Path, required=True)
    decrypt.add_argument("--key-file", type=Path, required=True)
    return parser


def _progress(done: int, total: int) -> None:
    print(f"Packed {done}/{total}", file=sys.stderr)


def _default_output(source: Path) -> Path:
    resolved = source.resolve(strict=True)
    if not resolved.name:
        msg = "The filesystem root requires an explicit --output."
        raise PackError(msg)
    return resolved.with_name(resolved.name + "-packed")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "keygen":
            keygen(args.output)
            print("Master key created.")
        elif args.command == "decrypt-config":
            decrypt_config(args.source, args.key_file, args.output)
            print("Manifest decrypted.")
        else:
            output: Path | None = args.output
            if output is None:
                output = _default_output(args.source)
            count = pack(args.source, output, args.key_file, _progress)
            print(f"Created {count} archives and an encrypted manifest.")
            print(f"Output: {output}")
    except PackError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Filesystem operation failed (errno={exc.errno}).", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; no incomplete batch was published.", file=sys.stderr)
        return 130
    return 0
