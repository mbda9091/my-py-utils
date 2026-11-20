from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from .models import (
    Discovery,
    Issue,
    ScanError,
    SessionFile,
    absolute,
    normalize,
    text,
    timestamp,
)
from .reader import DSH_NAME, SessionReader

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from .models import Object, Source


def _candidates(root: Path, source: Source, result: Discovery) -> Iterator[Path]:
    try:
        if not root.exists():
            result.warnings.append(Issue("missing_root", root, "Session directory is missing"))
            return
        allowed = root.resolve()
    except (OSError, RuntimeError):
        result.errors.append(
            Issue("directory_read_error", root, "Cannot inspect session directory")
        )
        return

    def onerror(_error: OSError) -> None:
        result.errors.append(
            Issue("directory_read_error", root, "Cannot enumerate session directory")
        )

    for directory, folders, names in os.walk(root, onerror=onerror, followlinks=False):
        folders.sort()
        for name in sorted(names):
            if (source == "codex" and not name.endswith(".jsonl")) or (
                source == "dsh" and not DSH_NAME.fullmatch(name)
            ):
                continue
            path = normalize(os.path.join(directory, name))  # noqa: PTH118 - os.walk boundary
            result.candidates += 1
            try:
                if not path.resolve().is_relative_to(allowed):
                    result.errors.append(
                        Issue("path_outside_root", path, "File escapes session root")
                    )
                elif not path.is_file():
                    result.errors.append(
                        Issue("invalid_file", path, "Not an existing regular file")
                    )
                else:
                    yield path
            except (OSError, RuntimeError):
                result.errors.append(
                    Issue("file_read_error", path, "Cannot inspect candidate file")
                )


def _file(path: Path, source: Source) -> SessionFile:
    with SessionReader(path, source) as reader:
        header = reader.header
        if header is None:
            msg = "Open reader has no header"
            raise RuntimeError(msg)
        cwd = absolute(header.data.get("cwd"))
        return SessionFile(
            header,
            cwd,
            "file_header" if cwd else None,
            text(header.data.get("title")),
            datetime.fromtimestamp(path.stat().st_mtime, UTC),
            "file_mtime",
        )


def _enrich(home: Path, result: Discovery) -> None:
    database = home / "state_5.sqlite"
    try:
        if not database.exists():
            return
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            columns = {row[1] for row in connection.execute('PRAGMA table_info("threads")')}
            if "id" not in columns:
                result.errors.append(
                    Issue("unsupported_index", database, "Codex threads table lacks an ID column")
                )
                return
            selected = [
                name
                for name in ("id", "cwd", "name", "title", "updated_at_ms", "updated_at")
                if name in columns
            ]
            # Identifiers come from the fixed metadata allowlist, not from source contents.
            projection = ", ".join(f'"{name}"' for name in selected)
            query = f"SELECT {projection} FROM threads WHERE id=?"  # noqa: S608
            for session in result.files:
                if session.header.source != "codex":
                    continue
                row = connection.execute(query, (session.header.session_id,)).fetchone()
                if row is not None:
                    _apply_index(session, cast("Object", dict(row)), result)
    except (sqlite3.Error, OSError):
        result.errors.append(
            Issue("index_read_error", database, "Cannot read Codex metadata index")
        )


def _apply_index(session: SessionFile, row: Object, result: Discovery) -> None:
    cwd = absolute(row.get("cwd"))
    if cwd:
        if session.cwd and session.cwd != cwd:
            result.warnings.append(
                Issue(
                    "cwd_conflict", session.header.path, "Index cwd takes priority over file header"
                )
            )
        session.cwd = cwd
        session.cwd_source = "codex_index"
    session.title = text(row.get("name")) or text(row.get("title")) or session.title
    updated = timestamp(row.get("updated_at_ms"), milliseconds=True) or timestamp(
        row.get("updated_at"), milliseconds=False
    )
    if updated is not None:
        session.updated_at = updated
        session.updated_at_source = "codex_index"


def discover(*, codex_home: Path | None = None, dsh_root: Path | None = None) -> Discovery:
    """Discover actual files, never enumerate metadata-only sessions or parse message rows."""
    result = Discovery()
    roots: list[tuple[Path, Source]] = []
    if codex_home is not None:
        codex_home = normalize(codex_home)
        roots.extend((codex_home / name, "codex") for name in ("sessions", "archived_sessions"))
    if dsh_root is not None:
        roots.append((normalize(dsh_root), "dsh"))
    seen: set[tuple[Source, Path]] = set()
    for root, source in roots:
        for path in _candidates(root, source, result):
            if (source, path) in seen:
                continue
            seen.add((source, path))
            try:
                result.files.append(_file(path, source))
            except ScanError as error:
                result.errors.append(error.issue)
            except OSError:
                result.errors.append(Issue("file_read_error", path, "Cannot read candidate file"))
    if codex_home is not None:
        _enrich(codex_home, result)
    result.files.sort(
        key=lambda item: (-item.updated_at.timestamp(), item.header.source, str(item.header.path))
    )
    return result
