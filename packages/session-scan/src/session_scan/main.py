from __future__ import annotations

import argparse
import json
import os

from .discovery import discover
from .models import normalize
from .selection import compile_filters, select_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Find DSH/Codex session files by directory and message time; output JSON."
    )
    parser.add_argument("--source", choices=("all", "codex", "dsh"), default="all")
    parser.add_argument("--codex-home")
    parser.add_argument("--dsh-root")
    parser.add_argument("--directory", action="append", default=[])
    parser.add_argument("--exact-directory", action="store_true")
    parser.add_argument("--since", help="Inclusive message time; date or timezone-aware ISO 8601")
    parser.add_argument("--until", help="Inclusive date, or exclusive timezone-aware ISO 8601 time")
    args = parser.parse_args(argv)
    try:
        filters = compile_filters(
            args.directory, exact=args.exact_directory, since=args.since, until=args.until
        )
        for name in ("codex_home", "dsh_root"):
            value: str | None = getattr(args, name)
            if value is not None and (not value.strip() or not normalize(value).is_dir()):
                parser.error(f"--{name.replace('_', '-')} must be an existing directory")
        codex_home = normalize(args.codex_home or os.environ.get("CODEX_HOME") or "~/.codex")
        dsh_root = normalize(args.dsh_root or "~/.dsh/sessions")
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    try:
        found = discover(
            codex_home=codex_home if args.source != "dsh" else None,
            dsh_root=dsh_root if args.source != "codex" else None,
        )
        entries, summary, errors = select_files(found, filters)
        code = 2 if not found.available_roots else int(bool(errors))
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "completed"
                    if code == 0
                    else "partial_failure"
                    if code == 1
                    else "invalid_source",
                    "filters": {"source": args.source, **filters.as_json()},
                    "results": entries,
                    "summary": summary,
                    "warnings": [issue.as_json() for issue in found.warnings],
                    "errors": [issue.as_json() for issue in errors],
                },
                ensure_ascii=False,
            )
        )
    except KeyboardInterrupt:
        return 130
    except OSError:
        return 1
    return code
