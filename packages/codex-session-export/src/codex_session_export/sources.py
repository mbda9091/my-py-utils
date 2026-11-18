from __future__ import annotations

import os
import sqlite3
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Self, cast

from .models import ExportError, Object, Session, decode, now, obj, string
from .selection import normalize, select_cwd

if TYPE_CHECKING:
    from types import TracebackType

TABLES: dict[str, tuple[str, set[str]]] = {
    "threads": ("state", {"id"}),
    "thread_spawn_edges": ("state", {"parent_thread_id", "child_thread_id"}),
    "thread_turns": ("history", {"thread_id", "turn_id", "rollout_ordinal"}),
    "thread_items": (
        "history",
        {"thread_id", "turn_id", "item_id", "rollout_ordinal", "item_json"},
    ),
}


class Catalog:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.sessions: dict[str, Session] = {}
        self.errors: list[Object] = []
        self.sources: list[Object] = []
        self.connections: dict[str, sqlite3.Connection] = {}
        self.tables: set[str] = set()
        self.stack = ExitStack()
        self.recognized = False

    def __enter__(self) -> Self:
        try:
            self._open_databases()
            self._discover_rows()
            self._discover_files()
            for session in self.sessions.values():
                preferred = string(session.attributes.get("rollout_path"))
                session.files.sort(key=lambda path: (str(path) != preferred, str(path)))
                select_cwd(session)
        except BaseException:
            self.stack.close()
            raise
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stack.close()

    def _error(self, source: str, error: Exception) -> None:
        self.errors.append({"code": "source_error", "source": source, "message": str(error)})

    def _connect(self, key: str, path: Path) -> None:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        self.stack.callback(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        present = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        supported: set[str] = set()
        for table, (database, required) in TABLES.items():
            if database != key:
                continue
            if table not in present:
                if table == "thread_spawn_edges":
                    continue
                msg = f"Missing required table: {table}"
                raise ExportError(msg)
            columns = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
            if not required <= columns:
                msg = f"Unsupported columns in {table}: missing {sorted(required - columns)}"
                raise ExportError(msg)
            supported.add(table)
        self.tables.update(supported)
        self.connections[key] = connection
        self.recognized = True

    def _open_databases(self) -> None:
        for key, name in [("state", "state_5.sqlite"), ("history", "thread_history_1.sqlite")]:
            path = self.home / name
            description: Object = {"path": str(path), "read_started_at": now(), "status": "missing"}
            self.sources.append(description)
            if not path.exists():
                continue
            try:
                self._connect(key, path)
                description["status"] = "available"
            except (sqlite3.Error, ExportError) as error:
                description["status"] = "error"
                self._error(str(path), error)
        supported = {"state_5.sqlite", "thread_history_1.sqlite"}
        for pattern in ("state_*.sqlite", "thread_history_*.sqlite"):
            for path in self.home.glob(pattern):
                if path.name not in supported:
                    self._error(
                        str(path),
                        ExportError("Unsupported database version; not selected automatically"),
                    )

    def rows(self, table: str, session_id: str | None = None) -> list[Object]:
        if table not in self.tables:
            return []
        database = TABLES[table][0]
        # Identifiers come exclusively from TABLES; session IDs are bound parameters.
        query = f'SELECT * FROM "{table}"'  # noqa: S608
        parameters: tuple[str, ...] = ()
        if session_id is not None:
            column = "id" if table == "threads" else "thread_id"
            if table == "thread_spawn_edges":
                query += ' WHERE "parent_thread_id" = ? OR "child_thread_id" = ?'
                parameters = (session_id, session_id)
            else:
                query += f' WHERE "{column}" = ?'
                parameters = (session_id,)
        cursor = self.connections[database].execute(query, parameters)
        return [cast("Object", dict(row)) for row in cursor]

    def _session(self, session_id: str) -> Session:
        if not session_id:
            msg = "Empty session ID"
            raise ExportError(msg)
        return self.sessions.setdefault(session_id, Session(session_id))

    def _discover_rows(self) -> None:
        try:
            for row in self.rows("threads"):
                self._session(string(row.get("id"))).attributes = row
            for row in self.rows("thread_spawn_edges"):
                child = self._session(string(row.get("child_thread_id")))
                child.attributes["parent_thread_id"] = row.get("parent_thread_id")
            for table in ("thread_turns", "thread_items"):
                if table in self.tables:
                    connection = self.connections["history"]
                    for row in connection.execute(f'SELECT DISTINCT thread_id FROM "{table}"'):  # noqa: S608
                        self._session(str(row[0]))
        except (sqlite3.Error, ExportError) as error:
            self._error("database discovery", error)

    def _discover_files(self) -> None:
        paths: set[Path] = set()
        references: dict[Path, list[str]] = {}
        for name in ("sessions", "archived_sessions"):
            directory = self.home / name
            if directory.is_dir():
                self.recognized = True
                paths.update(normalize(str(path)) for path in directory.rglob("*.jsonl"))
        for session in list(self.sessions.values()):
            value = string(session.attributes.get("rollout_path"))
            if value and Path(value).is_absolute():
                path = normalize(value)
                references.setdefault(path, []).append(session.id)
                if path.exists():
                    paths.add(path)
        for path in sorted(paths):
            try:
                self._register_file(path, references.get(path, []))
            except (OSError, ValueError) as error:
                self._error(str(path), error)

    def _register_file(self, path: Path, references: list[str]) -> None:
        with path.open("rb") as stream:
            record = obj(decode(stream.readline()))
        payload = obj(record.get("payload"))
        session_id = string(payload.get("id"))
        if record.get("type") != "session_meta" or not session_id:
            msg = "First record must be session_meta with an ID"
            raise ExportError(msg)
        session = self._session(session_id)
        session.files.append(path)
        session.file_meta[path] = payload
        session.attributes.setdefault(
            "archived", path.is_relative_to(self.home / "archived_sessions")
        )
        for expected in references:
            if expected != session_id:
                msg = f"Referenced session ID {expected!r} does not match {session_id!r}"
                raise ExportError(msg)

    def raw_rows(self, session: Session) -> list[Object]:
        return [
            {"table": table, "row": row} for table in TABLES for row in self.rows(table, session.id)
        ]


def snapshot(source: Path, target: Path) -> Object:
    started = now()
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as stream, target.open("xb") as output:
        initial = os.fstat(stream.fileno())
        remaining = initial.st_size
        while remaining:
            chunk = stream.read(min(remaining, 1024 * 1024))
            if not chunk:
                msg = f"Source truncated during snapshot: {source}"
                raise ExportError(msg)
            output.write(chunk)
            remaining -= len(chunk)
        final = os.fstat(stream.fileno())
        current = source.stat()
    if (initial.st_dev, initial.st_ino) != (current.st_dev, current.st_ino):
        msg = f"Source replaced during snapshot: {source}"
        raise ExportError(msg)
    if final.st_size < initial.st_size or (
        final.st_size == initial.st_size and final.st_mtime_ns != initial.st_mtime_ns
    ):
        msg = f"Source changed during snapshot: {source}"
        raise ExportError(msg)
    return {
        "source_path": str(source),
        "read_started_at": started,
        "read_completed_at": now(),
        "source_bytes_at_start": initial.st_size,
        "source_grew": final.st_size > initial.st_size,
    }


def database_parts(rows: list[Object], table: str) -> list[Object]:
    return [obj(row.get("row")) for row in rows if row.get("table") == table]
