from __future__ import annotations

import argparse
import json
import os

from .discovery import discover
from .models import normalize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover DSH/Codex session files as JSON (M1: metadata only)."
    )
    parser.add_argument("--source", choices=("all", "codex", "dsh"), default="all")
    parser.add_argument("--codex-home")
    parser.add_argument("--dsh-root")
    args = parser.parse_args(argv)
    for name in ("codex_home", "dsh_root"):
        value: str | None = getattr(args, name)
        if value is not None and (not value.strip() or not normalize(value).is_dir()):
            parser.error(f"--{name.replace('_', '-')} must be an existing directory")
    codex_home = normalize(args.codex_home or os.environ.get("CODEX_HOME") or "~/.codex")
    dsh_root = normalize(args.dsh_root or "~/.dsh/sessions")
    try:
        result = discover(
            codex_home=codex_home if args.source != "dsh" else None,
            dsh_root=dsh_root if args.source != "codex" else None,
        )
        available = any(
            root.is_dir()
            for root in (
                (
                    [codex_home / "sessions", codex_home / "archived_sessions"]
                    if args.source != "dsh"
                    else []
                )
                + ([dsh_root] if args.source != "codex" else [])
            )
        )
        code = 2 if not available else int(bool(result.errors))
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "completed"
                    if code == 0
                    else "partial_failure"
                    if code == 1
                    else "invalid_source",
                    "filters": {"source": args.source},
                    "results": [session.as_json() for session in result.files],
                    "summary": {
                        "candidate_files": result.candidates,
                        "matched_files": len(result.files),
                    },
                    "warnings": [issue.as_json() for issue in result.warnings],
                    "errors": [issue.as_json() for issue in result.errors],
                },
                ensure_ascii=False,
            )
        )
    except KeyboardInterrupt:
        return 130
    except OSError:
        return 1
    return code
