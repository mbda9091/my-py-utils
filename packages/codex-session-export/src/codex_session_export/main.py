from __future__ import annotations

import argparse
import os
import sys

from .exporter import Options, export
from .models import ExportError, decode, obj
from .selection import normalize


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Codex sessions as standard rollout JSONL.")
    parser.add_argument("--output", required=True, help="Export batch parent directory")
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME") or "~/.codex")
    parser.add_argument(
        "--directory", action="append", default=[], help="Match cwd and descendants; repeatable"
    )
    parser.add_argument(
        "--exact-directory", action="store_true", help="Match only the specified cwd"
    )
    parser.add_argument("--session", action="append", default=[], help="Session ID; repeatable")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    values: list[str] = [args.output, args.codex_home, *args.directory, *args.session]
    if any(not value.strip() for value in values):
        parser.error("Paths and session IDs must not be empty")
    try:
        options = Options(
            normalize(args.codex_home),
            normalize(args.output),
            tuple(dict.fromkeys(normalize(path) for path in args.directory)),
            tuple(dict.fromkeys(args.session)),
            exact=args.exact_directory,
        )
        batch, code = export(options)
    except ExportError as error:
        print(str(error), file=sys.stderr)
        return 2
    except OSError as error:
        print(f"Export failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    manifest = obj(decode((batch / "manifest.json").read_text(encoding="utf-8")))
    counts = obj(manifest.get("counts"))
    print(
        f"Selected {manifest.get('selected_count')} sessions; "
        f"generated {counts.get('generated')} rollouts; "
        f"sessions with errors {counts.get('error')}; missing rollouts {counts.get('failed')}."
    )
    print(f"Manifest: {batch / 'manifest.json'}")
    if code:
        print("Export incomplete; see manifest and session metadata for errors.", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
