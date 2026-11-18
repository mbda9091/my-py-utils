from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest
from codex_session_export import exporter as exporter_module
from codex_session_export.adapters import merge_events
from codex_session_export.exporter import Options, directory_name, export
from codex_session_export.main import main
from codex_session_export.models import (
    Conversion,
    Event,
    ExportError,
    Object,
    Session,
    array,
    decode,
    encode,
    obj,
    string,
)
from codex_session_export.rollout import dialogue, operations, read_standard, validate_record
from codex_session_export.selection import matches, normalize, select_cwd
from codex_session_export.sources import Catalog, snapshot

FIXTURES = Path(__file__).parent / "fixtures" / "codex_session_export"
STAMP = "1970-01-01T00:00:10.000Z"
PAIR_COUNT = 2
FIXTURE_MESSAGE_COUNT = 3
INVALID_ARGUMENTS = 2


@pytest.fixture
def items() -> list[Object]:
    return [obj(item) for item in array(decode((FIXTURES / "items.json").read_text()))]


def create_state(home: Path, sessions: list[tuple[str, str | None]]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(home / "state_5.sqlite") as connection:
        connection.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT, title TEXT, "
            "created_at INTEGER, archived INTEGER)"
        )
        connection.executemany("INSERT INTO threads VALUES (?, ?, 'Example', 10, 0)", sessions)


def create_history(home: Path, items: list[Object], session_id: str = "s") -> None:
    home.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(home / "thread_history_1.sqlite") as connection:
        connection.execute(
            "CREATE TABLE thread_turns (thread_id TEXT, turn_id TEXT, rollout_ordinal INTEGER, "
            "status TEXT, started_at INTEGER, completed_at INTEGER, rollout_end_ordinal INTEGER)"
        )
        connection.execute(
            "CREATE TABLE thread_items (thread_id TEXT, turn_id TEXT, item_id TEXT, "
            "rollout_ordinal INTEGER, created_at_ms INTEGER, item_json TEXT)"
        )
        connection.execute(
            "INSERT INTO thread_turns VALUES (?, 't', 1, 'completed', 10, 10, ?)",
            (session_id, len(items) + 2),
        )
        connection.executemany(
            "INSERT INTO thread_items VALUES (?, 't', ?, ?, 10000, ?)",
            [(session_id, item["id"], index + 2, encode(item)) for index, item in enumerate(items)],
        )


def native_file(
    home: Path, items: list[Object], session_id: str = "s", cwd: str = "/projects/demo"
) -> Path:
    fixtures = {
        string(obj(value).get("id")): obj(value)
        for value in array(decode((FIXTURES / "native-items.json").read_text()))
    }
    native = [
        fixtures.get(
            string(item.get("id")),
            {
                "type": "AgentMessage",
                "id": item.get("id"),
                "content": [{"type": "Text", "text": item.get("text")}],
            },
        )
        for item in items
    ]
    records: list[Object] = [
        {"type": "session_meta", "payload": {"id": session_id, "cwd": cwd, "timestamp": STAMP}},
        {
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "t", "started_at": 10},
        },
    ]
    records.extend(
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "thread_id": session_id,
                "turn_id": "t",
                "item": item,
                "started_at_ms": None,
                "completed_at_ms": None,
            },
        }
        for item in native
    )
    records.append(
        {
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "t",
                "started_at": 10,
                "completed_at": 10,
            },
        }
    )
    path = home / "sessions" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            encode({"timestamp": STAMP, "ordinal": index, **record}) + "\n"
            for index, record in enumerate(records)
        ),
        encoding="utf-8",
    )
    return path


def read_json(path: Path) -> Object:
    return obj(decode(path.read_text(encoding="utf-8")))


def session_export(batch: Path, session_id: str = "s") -> Object:
    return obj(
        read_json(batch / "sessions" / directory_name(session_id) / "metadata.json")["export"]
    )


def test_file_database_mixed_share_one_reader(tmp_path: Path, items: list[Object]) -> None:
    results: list[list[Object]] = []
    for mode in ("file", "database", "mixed"):
        home = tmp_path / mode
        source: Path | None = None
        if mode != "file":
            create_state(home, [("s", "/projects/demo")])
            create_history(home, items)
        if mode != "database":
            source = native_file(home, items)
        batch, code = export(Options(home, tmp_path / "out"))
        assert code == 0, session_export(batch)
        records = read_standard(batch / "sessions/s/rollout.jsonl")
        results.append(records)
        metadata = session_export(batch)
        assert obj(metadata["rollout"])["source_mode"] == mode
        assert obj(metadata["rollout"])["completeness"] == "available_content"
        assert metadata["record_sources"]
        if mode != "database":
            assert source is not None
            assert (batch / "sessions/s/raw/rollouts/001.jsonl").read_bytes() == source.read_bytes()
        if mode != "file":
            raw = (batch / "sessions/s/raw/database.jsonl").read_text()
            assert '"table":"thread_items"' in raw
    assert dialogue(results[0]) == dialogue(results[1]) == dialogue(results[2])
    assert operations(results[0]) == operations(results[1]) == operations(results[2])
    assert len(dialogue(results[2])) == len(
        [i for i in items if i["type"] in {"userMessage", "agentMessage"}]
    )


@pytest.mark.parametrize(
    ("cwd", "exact", "expected"),
    [
        ("/projects/demo", False, True),
        ("/projects/demo/sub", False, True),
        ("/projects/demo-backup", False, False),
        ("/projects/demo/sub", True, False),
        ("/projects/demo", True, True),
        (None, False, False),
    ],
)
def test_path_matching(cwd: str | None, *, exact: bool, expected: bool) -> None:
    session = Session("s", cwd=cwd)
    assert matches(session, [Path("/projects/demo")], exact=exact) is expected


def test_filter_union_intersection_and_unknown(tmp_path: Path) -> None:
    home = tmp_path / "home"
    create_state(
        home,
        [
            ("root", "/projects/demo"),
            ("sub", "/projects/demo/sub"),
            ("sibling", "/projects/demo-backup"),
            ("other", "/elsewhere"),
            ("unknown", None),
        ],
    )
    batch, code = export(
        Options(home, tmp_path / "out", (Path("/projects/demo"), Path("/projects/demo/sub")))
    )
    manifest = read_json(batch / "manifest.json")
    assert code == 0
    assert {string(obj(entry)["id"]) for entry in array(manifest["sessions"])} == {"root", "sub"}
    assert manifest["unknown_cwd_count"] == 1
    assert obj(manifest["counts"])["metadata_only"] == PAIR_COUNT
    selected, _ = export(
        Options(home, tmp_path / "out", (Path("/projects/demo"),), ("other", "sub"))
    )
    assert [
        obj(entry)["id"] for entry in array(read_json(selected / "manifest.json")["sessions"])
    ] == ["sub"]


def test_lexical_paths_and_cwd_fallback(tmp_path: Path) -> None:
    original = tmp_path / "original"
    original.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(original)
    assert normalize(str(alias)) != normalize(str(original))
    assert normalize(str(original / "a/../b")) == original / "b"
    path = tmp_path / "record"
    session = Session(
        "s", attributes={"cwd": "relative"}, files=[path], file_meta={path: {"cwd": "/old/project"}}
    )
    select_cwd(session)
    assert session.cwd == "/old/project"
    session.attributes["cwd"] = "/different"
    select_cwd(session)
    assert session.cwd == "/different"
    assert session.warnings


def test_archived_children_and_missing_index(tmp_path: Path, items: list[Object]) -> None:
    home = tmp_path / "home"
    file = native_file(home, items, "parent")
    archive = home / "archived_sessions"
    archive.mkdir()
    file.rename(archive / file.name)
    create_history(home, items, "child")
    batch, code = export(Options(home, tmp_path / "out", (Path("/projects/demo"),)))
    assert code == 0
    manifest = read_json(batch / "manifest.json")
    assert manifest["discovered_count"] == PAIR_COUNT
    assert [obj(entry)["id"] for entry in array(manifest["sessions"])] == ["parent"]


def test_corrupt_and_unknown_items_are_preserved(tmp_path: Path, items: list[Object]) -> None:
    home = tmp_path / "home"
    create_state(home, [("s", "/projects/demo")])
    create_history(home, [items[0], {"type": "futureItem", "id": "unknown"}])
    with sqlite3.connect(home / "thread_history_1.sqlite") as connection:
        connection.execute("INSERT INTO thread_items VALUES ('s','t','broken',9,10000,'{broken')")
    batch, code = export(Options(home, tmp_path / "out"))
    assert code == 1
    metadata = session_export(batch)
    assert obj(metadata["rollout"])["completeness"] == "partial"
    assert len(array(metadata["unconverted_records"])) == PAIR_COUNT
    assert "{broken" in (batch / "sessions/s/raw/database.jsonl").read_text()
    assert len(dialogue(read_standard(batch / "sessions/s/rollout.jsonl"))) == 1


@pytest.mark.parametrize("suffix", [b"", b'{"incomplete":', b"invalid\n"])
def test_raw_tail_and_invalid_lines(tmp_path: Path, items: list[Object], suffix: bytes) -> None:
    home = tmp_path / "home"
    path = native_file(home, items)
    path.write_bytes(path.read_bytes().rstrip(b"\n") + (b"\n" + suffix if suffix else b""))
    before = path.read_bytes()
    batch, code = export(Options(home, tmp_path / "out"))
    assert code == (1 if suffix == b"invalid\n" else 0)
    assert (batch / "sessions/s/raw/rollouts/001.jsonl").read_bytes() == before
    assert read_standard(batch / "sessions/s/rollout.jsonl")
    if suffix:
        assert obj(session_export(batch)["rollout"])["completeness"] == "partial"


def test_wal_snapshot_is_read_only(tmp_path: Path, items: list[Object]) -> None:
    home = tmp_path / "home"
    create_state(home, [("s", "/projects/demo")])
    create_history(home, items)
    with sqlite3.connect(home / "state_5.sqlite") as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO threads VALUES ('new','/projects/new','New',10,0)")
        writer.commit()
        with Catalog(home) as catalog:
            assert "new" in catalog.sessions
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                catalog.connections["state"].execute("DELETE FROM threads")
            writer.execute("UPDATE threads SET title='Changed' WHERE id='s'")
            writer.commit()
            assert catalog.rows("threads", "s")[0]["title"] == "Example"


def test_schema_rejects_payload_and_sequence(tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="Invalid rollout"):
        validate_record(
            {
                "timestamp": STAMP,
                "ordinal": 0,
                "type": "response_item",
                "payload": {"type": "message", "id": "x", "role": "user", "content": "wrong"},
            }
        )
    path = tmp_path / "bad.jsonl"
    meta: Object = {
        "timestamp": STAMP,
        "ordinal": 0,
        "type": "session_meta",
        "payload": {"id": "s", "timestamp": STAMP},
    }
    path.write_text(encode(meta) + "\n" + encode({**meta, "ordinal": 1}) + "\n")
    with pytest.raises(ExportError, match="Duplicate"):
        read_standard(path)
    path.write_text(encode({**meta, "ordinal": 2}) + "\n")
    with pytest.raises(ExportError, match="ordinal"):
        read_standard(path)


def test_duplicate_text_is_not_deduplicated(tmp_path: Path) -> None:
    items: list[Object] = [
        {"type": "agentMessage", "id": name, "text": "same"} for name in ("first", "second")
    ]
    home = tmp_path / "home"
    native_file(home, items)
    batch, code = export(Options(home, tmp_path / "out"))
    assert code == 0
    assert len(dialogue(read_standard(batch / "sessions/s/rollout.jsonl"))) == len(items)


def test_merge_updates_terminal_and_reports_conflicts() -> None:
    def event(status: str, output: str, ordinal: int) -> Event:
        return Event(
            "event_msg",
            {
                "type": "item_completed",
                "item": {"id": "x", "status": status, "aggregatedOutput": output},
            },
            None,
            (ordinal, 0, 0),
            turn_id="t",
        )

    result = Conversion(events=[event("inProgress", "", 1), event("failed", "error", 2)])
    merge_events(result)
    assert len(result.events) == 1
    assert obj(result.events[0].payload["item"])["status"] == "failed"
    assert not result.errors
    result.events.append(event("completed", "other", 3))
    merge_events(result)
    assert result.errors


def test_multiple_raw_files_and_id_mapping(tmp_path: Path, items: list[Object]) -> None:
    home = tmp_path / "home"
    source = native_file(home, items)
    (source.parent / "copy.jsonl").write_bytes(source.read_bytes())
    batch, code = export(Options(home, tmp_path / "out"))
    assert code == 0
    assert len(list((batch / "sessions/s/raw/rollouts").glob("*.jsonl"))) == PAIR_COUNT
    assert len(dialogue(read_standard(batch / "sessions/s/rollout.jsonl"))) == FIXTURE_MESSAGE_COUNT
    assert "/" not in directory_name("../../unsafe")
    assert directory_name("../unsafe") != directory_name("../../unsafe")


def test_cli_exit_codes_and_home_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    create_state(home, [("s", "/projects/demo")])
    monkeypatch.setenv("CODEX_HOME", str(home))
    base = ["--output", str(tmp_path / "out")]
    assert main([*base, "--directory", "/missing"]) == 0
    assert main([*base, "--session", "unknown"]) == 1
    assert main([*base, "--exact-directory"]) == INVALID_ARGUMENTS
    assert main(["--codex-home", str(tmp_path / "missing"), *base]) == INVALID_ARGUMENTS
    assert main(["--output", str(home / "exports")]) == INVALID_ARGUMENTS


def test_batches_and_hashes(tmp_path: Path, items: list[Object]) -> None:
    home = tmp_path / "home"
    native_file(home, items)
    first, _ = export(Options(home, tmp_path / "out"))
    second, _ = export(Options(home, tmp_path / "out"))
    assert first != second
    info = obj(session_export(first)["rollout"])
    content = (first / string(info["file"])).read_bytes()
    assert info["sha256"] == hashlib.sha256(content).hexdigest()
    assert info["bytes"] == len(content)


def test_unknown_database_version_is_reported(tmp_path: Path, items: list[Object]) -> None:
    home = tmp_path / "home"
    native_file(home, items)
    (home / "state_99.sqlite").touch()
    batch, code = export(Options(home, tmp_path / "out"))
    assert code == 1
    assert read_json(batch / "manifest.json")["discovery_errors"]
    assert read_standard(batch / "sessions/s/rollout.jsonl")


def test_real_native_projection_shapes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    create_state(home, [("s", "/projects/demo")])
    projected: Object = {
        "id": "command-1",
        "type": "commandExecution",
        "status": "completed",
        "command": "zsh -lc 'ls -a'",
        "cwd": "/projects/demo",
        "durationMs": 0,
        "source": "unifiedExecStartup",
        "aggregatedOutput": "file.txt",
        "commandActions": [{"type": "listFiles", "command": "ls -a", "path": "."}],
    }
    create_history(home, [projected])
    path = native_file(home, [projected])
    lines = [obj(decode(line)) for line in path.read_text().splitlines()]
    item: Object = {
        "id": "command-1",
        "type": "CommandExecution",
        "status": "completed",
        "command": ["zsh", "-lc", "ls -a"],
        "cwd": "file:///projects/demo",
        "duration": {"secs": 0, "nanos": 7750},
        "source": "unified_exec_startup",
        "aggregated_output": "file.txt",
        "parsed_cmd": [{"type": "list_files", "cmd": "ls -a", "path": "."}],
    }
    obj(lines[2]["payload"])["item"] = item
    path.write_text("".join(encode(line) + "\n" for line in lines))
    batch, code = export(Options(home, tmp_path / "out"))
    assert code == 0, session_export(batch)
    assert operations(read_standard(batch / "sessions/s/rollout.jsonl"))[0] == item


def test_missing_message_id_and_legacy_tools(tmp_path: Path) -> None:
    home = tmp_path / "home"
    path = native_file(home, [])
    records = [obj(decode(line)) for line in path.read_text().splitlines()]
    payloads: list[Object] = [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]},
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "lookup",
            "arguments": '{"key":"x"}',
        },
        {"type": "function_call_output", "call_id": "call-1", "output": "found"},
    ]
    records.extend(
        {"timestamp": STAMP, "ordinal": index + 3, "type": "response_item", "payload": payload}
        for index, payload in enumerate(payloads)
    )
    path.write_text("".join(encode(record) + "\n" for record in records))
    batch, code = export(Options(home, tmp_path / "out"))
    assert code == 0
    output = read_standard(batch / "sessions/s/rollout.jsonl")
    assert string(dialogue(output)[0]["id"]).startswith("export-")
    assert operations(output)[0]["type"] == "DynamicToolCall"
    assert any(
        obj(source).get("generated_id")
        for entry in array(session_export(batch)["record_sources"])
        for source in array(obj(entry).get("sources"))
    )


def test_active_orphan_items_and_multimodal_references(tmp_path: Path) -> None:
    home = tmp_path / "home"
    create_history(
        home,
        [
            {
                "type": "userMessage",
                "id": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {"type": "image", "url": "https://example.com/image.png"},
                    {"type": "skill", "name": "example", "path": "/skills/example"},
                    {"type": "localImage", "path": "/images/a.png"},
                ],
            }
        ],
    )
    with sqlite3.connect(home / "thread_history_1.sqlite") as connection:
        connection.execute("DELETE FROM thread_turns")
        connection.execute("UPDATE thread_items SET created_at_ms = NULL")
    batch, code = export(Options(home, tmp_path / "out"))
    assert code == 0
    records = read_standard(batch / "sessions/s/rollout.jsonl")
    assert all(
        obj(record["payload"]).get("type") not in {"task_complete", "task_started"}
        for record in records
    )
    assert array(dialogue(records)[0]["content"]) == [
        {"type": "input_text", "text": "look"},
        {"type": "input_image", "image_url": "https://example.com/image.png"},
    ]
    meta = session_export(batch)
    assert meta["warnings"]
    assert any(
        obj(entry).get("time_fallback") == "epoch" for entry in array(meta["record_sources"])
    )


def test_standard_validation_failure_does_not_publish(
    tmp_path: Path,
    items: list[Object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    native_file(home, items)

    def reject(_path: Path) -> list[Object]:
        msg = "injected schema failure"
        raise ExportError(msg)

    monkeypatch.setattr(exporter_module, "read_standard", reject)
    batch, code = export(Options(home, tmp_path / "out"))
    assert code == 1
    assert not (batch / "sessions/s/rollout.jsonl").exists()
    assert (batch / "sessions/s/raw/rollouts/001.jsonl").exists()
    assert obj(read_json(batch / "manifest.json")["counts"])["failed"] == 1


def test_interrupt_records_unfinished_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    create_state(home, [("first", "/project"), ("second", "/project")])

    def interrupt(_catalog: Catalog, _session: Session, _batch: Path) -> Object:
        raise KeyboardInterrupt

    monkeypatch.setattr(exporter_module, "export_session", interrupt)
    batch, code = export(Options(home, tmp_path / "out"))
    assert code == exporter_module.INTERRUPTED
    manifest = read_json(batch / "manifest.json")
    assert manifest["status"] == "interrupted"
    assert obj(manifest["counts"])["failed"] == manifest["selected_count"]


def test_session_failure_does_not_block_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    create_state(home, [("first", "/project"), ("second", "/project")])
    original = exporter_module.export_session

    def failure(catalog: Catalog, session: Session, batch: Path) -> Object:
        if session.id == "first":
            msg = "injected write failure"
            raise OSError(msg)
        return original(catalog, session, batch)

    monkeypatch.setattr(exporter_module, "export_session", failure)
    batch, code = export(Options(home, tmp_path / "out"))
    assert code == 1
    assert read_standard(batch / "sessions/second/rollout.jsonl")
    counts = obj(read_json(batch / "manifest.json")["counts"])
    assert counts["generated"] == counts["failed"] == 1


def test_snapshot_excludes_concurrent_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target = tmp_path / "source", tmp_path / "copy"
    source.write_bytes(b"original\n")
    original = os.fstat
    appended = False

    def append_after_stat(fd: int) -> os.stat_result:
        nonlocal appended
        status = original(fd)
        if not appended:
            appended = True
            with source.open("ab") as stream:
                stream.write(b"later\n")
        return status

    monkeypatch.setattr(os, "fstat", append_after_stat)
    info = snapshot(source, target)
    assert target.read_bytes() == b"original\n"
    assert info["source_grew"] is True


def test_snapshot_rejects_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, target, replacement = tmp_path / "source", tmp_path / "copy", tmp_path / "other"
    source.write_bytes(b"original\n")
    replacement.write_bytes(b"replacement\n")
    original = Path.stat

    def changed(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        return original(replacement if path == source else path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", changed)
    with pytest.raises(ExportError, match="replaced"):
        snapshot(source, target)


def test_passthrough_dedup_and_ambiguous_order() -> None:
    result = Conversion(
        events=[
            Event("turn_context", {"turn_id": "t"}, STAMP, (1, 0, 0)),
            Event("turn_context", {"turn_id": "t"}, STAMP, (1, 1, 0)),
        ]
    )
    merge_events(result)
    assert len(result.events) == 1
    result.events.extend(
        [
            Event(
                "turn_context",
                {"turn_id": "a"},
                STAMP,
                (2, 0, 0),
                [{"order_fallback": "line_number"}],
            ),
            Event(
                "turn_context",
                {"turn_id": "b"},
                STAMP,
                (1, 1, 0),
                [{"order_fallback": "line_number"}],
            ),
        ]
    )
    merge_events(result)
    assert result.partial
    assert result.errors[0]["code"] == "ambiguous_order"


def test_semantically_invalid_final_record_is_an_error(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source = native_file(home, [])
    with source.open("ab") as stream:
        stream.write(b'{"type":"response_item","payload":{"type":[]}}')
    batch, code = export(Options(home, tmp_path / "out"))
    assert code == 1
    assert session_export(batch)["errors"]
