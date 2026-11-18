from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from . import __version__
from .adapters import (
    add_legacy_tools,
    add_views,
    database_events,
    merge_events,
    read_rollout,
    session_meta,
)
from .models import (
    Conversion,
    ExportError,
    Json,
    Object,
    Session,
    encode,
    now,
    obj,
    string,
    timestamp,
)
from .rollout import FORMAT, dialogue, read_standard, render
from .selection import matches
from .sources import Catalog, database_parts, snapshot

if TYPE_CHECKING:
    from pathlib import Path


INTERRUPTED = 130


@dataclass(frozen=True)
class Options:
    home: Path
    output: Path
    directories: tuple[Path, ...] = ()
    session_ids: tuple[str, ...] = ()
    exact: bool = False


def atomic_lines(path: Path, records: list[Object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(encode(record) + "\n")
    temporary.replace(path)


def atomic_json(path: Path, value: Object) -> None:
    atomic_lines(path, [value])


def file_info(path: Path, batch: Path) -> Object:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "file": path.relative_to(batch).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": digest,
    }


def directory_name(session_id: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]{1,160}", session_id):
        return session_id
    return "session-" + hashlib.sha256(session_id.encode()).hexdigest()


def session_details(session: Session) -> Object:
    attributes = session.attributes
    native = session_meta(session)
    git = obj(native.get("git"))
    return {
        **attributes,
        "id": session.id,
        "cwd": session.cwd,
        "title": attributes.get("name") or attributes.get("title"),
        "created_at": timestamp(attributes.get("created_at_ms"), milliseconds=True)
        or timestamp(attributes.get("created_at"))
        or timestamp(native.get("timestamp")),
        "updated_at": timestamp(attributes.get("updated_at_ms"), milliseconds=True)
        or timestamp(attributes.get("updated_at")),
        "archived": bool(attributes["archived"]) if "archived" in attributes else None,
        "git": {
            "branch": attributes.get("git_branch") or git.get("branch"),
            "commit": attributes.get("git_sha") or git.get("commit_hash"),
            "repository_url": attributes.get("git_origin_url") or git.get("repository_url"),
        },
    }


def export_session(catalog: Catalog, session: Session, batch: Path) -> Object:
    directory = batch / "sessions" / directory_name(session.id)
    directory.mkdir(parents=True, exist_ok=False)
    result = Conversion(warnings=list(session.warnings))
    raw_sources: list[Json] = []
    for index, path in enumerate(session.files):
        target = directory / "raw" / "rollouts" / f"{index + 1:03d}.jsonl"
        temporary = target.with_suffix(".tmp")
        try:
            info = snapshot(path, temporary)
            temporary.replace(target)
            raw_sources.append({**info, **file_info(target, batch)})
            read_rollout(target, str(path), index, result)
        except (OSError, ValueError) as error:
            result.error("file_read_error", str(path), str(error))
    rows: list[Object] = []
    try:
        rows = catalog.raw_rows(session)
        if rows:
            target = directory / "raw" / "database.jsonl"
            atomic_lines(target, rows)
            raw_sources.append(
                {**file_info(target, batch), "source": "sqlite_snapshot", "read_at": now()}
            )
        database_events(rows, session, result)
    except (sqlite3.Error, OSError, ValueError) as error:
        result.error("database_read_error", session.id, str(error))
    database_history = bool(
        database_parts(rows, "thread_items") or database_parts(rows, "thread_turns")
    )
    if database_history:
        result.missing.append(
            "Database projections cannot recover unrecorded original context or tool call details"
        )
    merge_events(result)
    add_views(result)
    add_legacy_tools(result, session.id)
    source_mode = (
        "mixed"
        if session.files and database_history
        else "file"
        if session.files
        else "database"
        if database_history
        else "metadata"
    )
    metadata: Object = {
        "schema_version": 1,
        "session": session_details(session),
        "selection": {
            "cwd_source": session.cwd_source,
            "normalized_cwd": session.cwd,
            "matched_directories": list(session.matched_directories),
        },
    }
    rollout: Object = {
        "format": FORMAT,
        "status": "error",
        "file": None,
        "source_mode": source_mode,
    }
    origins: list[Object] = []
    try:
        records, origins = render(session, session_meta(session), result)
        target = directory / "rollout.jsonl"
        pending = directory / "rollout.pending"
        atomic_lines(pending, records)
        read_standard(pending)
        pending.replace(target)
        completeness = (
            "partial"
            if result.partial
            else "metadata_only"
            if len(records) == 1
            else "available_content"
        )
        if completeness == "metadata_only":
            result.warnings.append(
                {"code": "metadata_only", "message": "No conversation body found"}
            )
        rollout.update(
            **file_info(target, batch),
            status="generated",
            completeness=completeness,
            record_count=len(records),
            message_count=len(dialogue(records)),
        )
    except (OSError, ValueError) as error:
        result.error("rollout_error", session.id, str(error))
    status = "error" if result.errors else "warning" if result.warnings or result.partial else "ok"
    metadata["export"] = {
        "exported_at": now(),
        "status": status,
        "rollout": rollout,
        "raw_sources": raw_sources,
        "record_sources": list(origins),
        "missing_information": result.missing,
        "unconverted_records": list(result.errors),
        "warnings": list(result.warnings),
        "errors": list(result.errors),
    }
    atomic_json(directory / "metadata.json", metadata)
    return {
        "id": session.id,
        "metadata": (directory / "metadata.json").relative_to(batch).as_posix(),
        "status": status,
        "rollout": rollout,
    }


def validate_options(options: Options) -> None:
    if not options.home.is_dir():
        msg = f"Codex home does not exist: {options.home}"
        raise ExportError(msg)
    if options.exact and not options.directories:
        msg = "--exact-directory requires --directory"
        raise ExportError(msg)
    # Resolve only for output isolation, never for historical cwd matching.
    if options.output.resolve().is_relative_to(options.home.resolve()):
        msg = "Output must be outside Codex home"
        raise ExportError(msg)


def update_counts(manifest: Object, entries: list[Object]) -> None:
    counts: Object = {}
    for name in ("ok", "warning", "error"):
        counts[name] = sum(entry.get("status") == name for entry in entries)
    counts["generated"] = sum(
        obj(entry.get("rollout")).get("status") == "generated" for entry in entries
    )
    counts["failed"] = len(entries) - int(counts["generated"] or 0)
    for name in ("available_content", "partial", "metadata_only"):
        counts[name] = sum(
            obj(entry.get("rollout")).get("completeness") == name for entry in entries
        )
    manifest["counts"] = counts


def select_sessions(catalog: Catalog, options: Options) -> tuple[list[Session], int, int]:
    selected: list[Session] = []
    unknown = 0
    excluded = 0
    for session in sorted(catalog.sessions.values(), key=lambda entry: entry.id):
        if options.session_ids and session.id not in options.session_ids:
            excluded += 1
        elif options.directories and session.cwd is None:
            unknown += 1
        elif matches(session, list(options.directories), exact=options.exact):
            selected.append(session)
        else:
            excluded += 1
    return selected, unknown, excluded


def export(options: Options) -> tuple[Path, int]:
    validate_options(options)
    with Catalog(options.home) as catalog:
        if not catalog.recognized:
            detail = "; ".join(string(error.get("message")) for error in catalog.errors)
            raise ExportError("No supported Codex data sources" + (f": {detail}" if detail else ""))
        selected, unknown, excluded = select_sessions(catalog, options)
        missing = sorted(set(options.session_ids) - catalog.sessions.keys())
        options.output.mkdir(parents=True, exist_ok=True)
        batch = options.output / (now().replace(":", "").replace("-", "") + "-" + uuid4().hex[:8])
        batch.mkdir(exist_ok=False)
        entries: list[Object] = []
        manifest: Object = {
            "schema_version": 1,
            "format": FORMAT,
            "tool_version": __version__,
            "batch_id": batch.name,
            "started_at": now(),
            "status": "in_progress",
            "options": {
                "codex_home": str(options.home),
                "output": str(options.output),
                "directories": [str(path) for path in options.directories],
                "exact_directory": options.exact,
                "session_ids": list(options.session_ids),
            },
            "discovered_count": len(catalog.sessions),
            "selected_count": len(selected),
            "excluded_count": excluded,
            "unknown_cwd_count": unknown,
            "sources": list(catalog.sources),
            "discovery_errors": list(catalog.errors),
            "missing_session_ids": list(missing),
            "sessions": [],
        }
        atomic_json(batch / "manifest.json", manifest)
        code = 1 if catalog.errors or missing else 0
        try:
            for session in selected:
                try:
                    entry: Object = export_session(catalog, session, batch)
                except (OSError, ValueError, sqlite3.Error) as error:
                    entry = {"id": session.id, "status": "error", "error": str(error)}
                entries.append(entry)
                if entry["status"] == "error":
                    code = 1
                manifest["sessions"] = list(entries)
                update_counts(manifest, entries)
                atomic_json(batch / "manifest.json", manifest)
        except KeyboardInterrupt:
            code = 130
            entries.extend(
                {"id": session.id, "status": "error", "error": "interrupted"}
                for session in selected[len(entries) :]
            )
            manifest["sessions"] = list(entries)
        manifest["status"] = (
            "interrupted"
            if code == INTERRUPTED
            else "completed_with_errors"
            if code
            else "completed"
        )
        manifest["completed_at"] = now()
        for source in catalog.sources:
            source["read_completed_at"] = manifest["completed_at"]
        update_counts(manifest, entries)
        atomic_json(batch / "manifest.json", manifest)
        return batch, code
