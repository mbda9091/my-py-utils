from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import zstandard
from session_scan import reader as reader_module
from session_scan.discovery import discover
from session_scan.main import main
from session_scan.messages import iter_messages
from session_scan.models import Json, Object, ScanError, Source, timestamp
from session_scan.reader import SessionReader

STAMP = "2026-11-19T08:00:00Z"
MILLISECONDS = 1795075200000
INVALID_ARGUMENTS = 2


def write_session(  # noqa: PLR0913 - independent format, header and fault-injection options
    root: Path,
    source: Source = "codex",
    *,
    version: int = 4,
    compressed: bool = False,
    header_values: Object | None = None,
    rows: list[Object] | None = None,
    tail: bytes = b"",
) -> Path:
    header: Object = {"id": "session-one", "cwd": "/projects/demo"}
    header.update(header_values or {})
    if source == "codex":
        header = {"type": "session_meta", "payload": header}
        path = root / "sessions" / "one.jsonl"
    else:
        header.update(type="session", version=version)
        name = "session" if version == 0 else f"session.v{version}"
        path = root / "project" / "one" / f"{name}.jsonl"
    first = (json.dumps(header) + "\n").encode()
    body = "".join(json.dumps(row) + "\n" for row in rows or []).encode() + tail
    if compressed:
        path = path.with_name(path.name + ".zstd")
        compressor = zstandard.ZstdCompressor(write_checksum=True)
        content = compressor.compress(first) + (compressor.compress(body) if body else b"")
    else:
        content = first + body
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def codex_message(*, role: str = "user", stamp: Json = STAMP) -> Object:
    return {
        "type": "response_item",
        "timestamp": stamp,
        "payload": {"type": "message", "role": role},
    }


def dsh_message(seq: int = 0, *, source: str = "user", role: str = "user") -> Object:
    message: Object = {"role": role, "source": {"kind": source}, "content": []}
    data: Object = message if role == "user" else {"message": message}
    return {
        "type": "user/message" if role == "user" else "assistant/message",
        "seq": seq,
        "time": MILLISECONDS,
        "surfaceOp": "append",
        "data": data,
    }


@pytest.mark.parametrize("version", [0, 3, 4])
@pytest.mark.parametrize("compressed", [False, True])
def test_dsh_versions_and_encodings(tmp_path: Path, version: int, *, compressed: bool) -> None:
    path = write_session(
        tmp_path, "dsh", version=version, compressed=compressed, rows=[dsh_message()]
    )
    with SessionReader(path, "dsh") as reader:
        assert reader.header is not None
        assert reader.header.version == version
        messages = list(iter_messages(reader))
        assert [message.role for message in messages] == ["user"]
        assert [message.timestamp for message in messages] == [
            datetime(2026, 11, 19, 8, tzinfo=UTC)
        ]
        assert reader.complete
        assert not reader.errors


@pytest.mark.parametrize("compressed", [False, True])
def test_discovery_does_not_parse_body(tmp_path: Path, *, compressed: bool) -> None:
    source: Source = "dsh" if compressed else "codex"
    path = write_session(tmp_path, source, compressed=compressed, tail=b"broken body\n" * 10000)
    found = discover(**({"dsh_root": tmp_path} if compressed else {"codex_home": tmp_path}))
    assert [entry.header.path for entry in found.files] == [path]
    assert not found.errors
    with SessionReader(path, source) as reader:
        assert not reader.complete
        assert reader.line_number == 1
        assert not reader.errors


def test_discovery_index_only_and_multiple_files(tmp_path: Path) -> None:
    path = write_session(tmp_path)
    archived = tmp_path / "archived_sessions" / "two.jsonl"
    archived.parent.mkdir()
    archived.write_bytes(path.read_bytes())
    with sqlite3.connect(tmp_path / "state_5.sqlite") as connection:
        connection.execute(
            "CREATE TABLE threads (id TEXT, cwd TEXT, name TEXT, updated_at_ms INTEGER)"
        )
        connection.executemany(
            "INSERT INTO threads VALUES (?, ?, ?, ?)",
            [
                ("session-one", "/projects/index", "Index title", MILLISECONDS),
                ("no-file", "/projects/index", "No file", MILLISECONDS),
            ],
        )
    found = discover(codex_home=tmp_path)
    assert {entry.header.path for entry in found.files} == {path, archived}
    assert {entry.cwd for entry in found.files} == {"/projects/index"}
    assert {entry.title for entry in found.files} == {"Index title"}
    assert {entry.updated_at_source for entry in found.files} == {"codex_index"}
    assert all(issue.code == "cwd_conflict" for issue in found.warnings)
    assert not found.errors


def test_bad_index_keeps_unindexed_file(tmp_path: Path) -> None:
    path = write_session(tmp_path)
    (tmp_path / "state_5.sqlite").write_bytes(b"not sqlite")
    found = discover(codex_home=tmp_path)
    assert [entry.header.path for entry in found.files] == [path]
    assert [issue.code for issue in found.errors] == ["index_read_error"]
    assert found.files[0].cwd == "/projects/demo"


def test_file_symlink_escape_and_directory_links(tmp_path: Path) -> None:
    outside = write_session(tmp_path / "outside")
    home = tmp_path / "home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "escape.jsonl").symlink_to(outside)
    (sessions / "loop").symlink_to(sessions, target_is_directory=True)
    found = discover(codex_home=home)
    assert not found.files
    assert [issue.code for issue in found.errors] == ["path_outside_root"]


@pytest.mark.parametrize(
    "rows",
    [
        [codex_message()],
        [{"type": "event_msg", "timestamp": STAMP, "payload": {"type": "user_message"}}],
        [
            {
                "type": "event_msg",
                "timestamp": STAMP,
                "payload": {"type": "item_completed", "item": {"type": "UserMessage"}},
            }
        ],
    ],
)
def test_codex_message_shapes(tmp_path: Path, rows: list[Object]) -> None:
    path = write_session(tmp_path, rows=rows)
    with SessionReader(path, "codex") as reader:
        messages = list(iter_messages(reader))
        assert [message.role for message in messages] == ["user"]
        assert messages[0].line_number == len(rows) + 1
        assert reader.complete


def test_paginated_context_and_retained_messages_are_excluded(tmp_path: Path) -> None:
    retained = codex_message(role="assistant")
    retained["metadata"] = {"retained_source": {"id": "old"}}
    path = write_session(
        tmp_path,
        header_values={"history_mode": "paginated"},
        rows=[
            codex_message(),
            retained,
            {"type": "turn_context", "timestamp": STAMP},
            {
                "type": "event_msg",
                "timestamp": STAMP,
                "payload": {"type": "item_completed", "item": {"type": "AgentMessage"}},
            },
        ],
    )
    with SessionReader(path, "codex") as reader:
        assert [message.role for message in iter_messages(reader)] == ["assistant"]
        assert not reader.errors


def test_dsh_injections_replacements_and_model_replay_state(tmp_path: Path) -> None:
    replacement = dsh_message(1)
    replacement["surfaceOp"] = {"op": "replace", "startSeq": 0, "endSeq": 0}
    assistant = dsh_message(3, source="model", role="assistant")
    assistant_data = assistant["data"]
    assert isinstance(assistant_data, dict)
    assistant_message = assistant_data["message"]
    assert isinstance(assistant_message, dict)
    assistant_message["source"] = {"kind": "model", "replayState": {"response": {"version": 2}}}
    path = write_session(
        tmp_path,
        "dsh",
        rows=[
            dsh_message(source="working-directory"),
            replacement,
            {"type": "tool/result", "time": MILLISECONDS},
            assistant,
        ],
    )
    with SessionReader(path, "dsh") as reader:
        assert [message.role for message in iter_messages(reader)] == ["assistant"]
        assert not reader.errors


@pytest.mark.parametrize("compressed", [False, True])
def test_seeded_last_boundary_excludes_inherited_messages(
    tmp_path: Path, *, compressed: bool
) -> None:
    marker: Object = {"type": "session/end-seed", "seq": 1, "data": {"inherited": True}}
    path = write_session(
        tmp_path,
        "dsh",
        compressed=compressed,
        header_values={"isSeeded": True},
        rows=[
            dsh_message(),
            marker,
            dsh_message(2),
            {**marker, "seq": 3},
            dsh_message(4, source="model", role="assistant"),
        ],
    )
    with SessionReader(path, "dsh") as reader:
        assert [message.role for message in iter_messages(reader)] == ["assistant"]
        assert reader.complete
        assert not reader.errors


def test_legacy_seed_length_and_missing_boundary(tmp_path: Path) -> None:
    path = write_session(
        tmp_path,
        "dsh",
        version=0,
        header_values={"seedLength": 1},
        rows=[dsh_message(), dsh_message(1)],
    )
    with SessionReader(path, "dsh") as reader:
        assert [message.position for message in iter_messages(reader)] == [1]
    path = write_session(tmp_path, "dsh", header_values={"isSeeded": True}, rows=[dsh_message()])
    with SessionReader(path, "dsh") as reader:
        assert not list(iter_messages(reader))
        assert [error.code for error in reader.errors] == ["missing_seed_boundary"]


@pytest.mark.parametrize("compressed", [False, True])
def test_message_iterator_early_stop_leaves_bad_tail_unvisited(
    tmp_path: Path, *, compressed: bool
) -> None:
    source: Source = "dsh" if compressed else "codex"
    message = dsh_message() if compressed else codex_message()
    path = write_session(
        tmp_path, source, compressed=compressed, rows=[message], tail=b"invalid\n" * 10000
    )
    with SessionReader(path, source) as reader:
        iterator = iter_messages(reader)
        assert next(iterator).role == "user"
        iterator.close()
        assert not reader.complete
        assert not reader.errors
        assert reader.line_number == len([message]) + 1


@pytest.mark.parametrize(
    ("stamp", "code"),
    [
        (None, "missing_message_time"),
        ("bad", "invalid_message_time"),
        (True, "invalid_message_time"),
    ],
)
def test_invalid_message_time_is_not_filled(tmp_path: Path, stamp: Json, code: str) -> None:
    path = write_session(tmp_path, rows=[codex_message(stamp=stamp), codex_message()])
    with SessionReader(path, "codex") as reader:
        assert [message.role for message in iter_messages(reader)] == ["user"]
        assert [error.code for error in reader.errors] == [code]
        assert not reader.complete


def test_corrupt_record_does_not_hide_valid_messages(tmp_path: Path) -> None:
    path = write_session(tmp_path, rows=[codex_message()], tail=b'[]\n{"partial":')
    with SessionReader(path, "codex") as reader:
        assert [message.role for message in iter_messages(reader)] == ["user"]
        assert [error.code for error in reader.errors] == ["invalid_record", "truncated_record"]


def test_limits_and_unknown_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader_module, "HEADER_LIMIT", 8)
    path = write_session(tmp_path)
    with pytest.raises(ScanError) as caught, SessionReader(path, "codex"):
        pytest.fail("Oversized header accepted")
    assert caught.value.issue.code == "header_too_large"
    monkeypatch.setattr(reader_module, "HEADER_LIMIT", 1024)
    path = write_session(tmp_path, "dsh", version=99)
    with pytest.raises(ScanError) as caught, SessionReader(path, "dsh"):
        pytest.fail("Unknown version accepted")
    assert caught.value.issue.code == "unsupported_version"


def test_truncated_compressed_tail_is_not_complete(tmp_path: Path) -> None:
    path = write_session(tmp_path, "dsh", compressed=True, rows=[dsh_message()])
    path.write_bytes(path.read_bytes()[:-1])
    with SessionReader(path, "dsh") as reader:
        list(iter_messages(reader))
        assert not reader.complete
        assert reader.errors


def test_opening_size_does_not_chase_appends(tmp_path: Path) -> None:
    path = write_session(tmp_path, rows=[codex_message()])
    with SessionReader(path, "codex") as reader:
        with path.open("ab") as output:
            output.write(b"invalid appended data\n")
        assert [message.role for message in iter_messages(reader)] == ["user"]
        assert reader.complete


def test_json_cli_and_partial_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_session(tmp_path)
    assert main(["--source", "codex", "--codex-home", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [entry["path"] for entry in result["results"]] == [str(path)]
    path.write_text("invalid header\n")
    assert main(["--source", "codex", "--codex-home", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["errors"]


def test_help_and_invalid_args_do_not_discover(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(**_kwargs: object) -> None:
        pytest.fail("Discovery called for help/invalid arguments")

    monkeypatch.setattr("session_scan.main.discover", unexpected)
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    with pytest.raises(SystemExit) as caught:
        main(["--format", "json"])
    assert caught.value.code == INVALID_ARGUMENTS


def test_time_parser_rejects_naive_and_nonfinite_values() -> None:
    assert timestamp("2026-11-19T08:00:00") is None
    assert timestamp(float("inf"), milliseconds=True) is None
    assert timestamp(value=True, milliseconds=True) is None


def test_header_only_does_not_decode_corrupt_next_frame(tmp_path: Path) -> None:
    path = write_session(tmp_path, "dsh", compressed=True)
    with path.open("ab") as output:
        output.write(b"invalid compressed body")
    found = discover(dsh_root=tmp_path)
    assert [entry.header.path for entry in found.files] == [path]
    assert not found.errors
    with SessionReader(path, "dsh") as reader:
        assert not list(iter_messages(reader))
        assert reader.errors


def test_oversized_message_continues_at_next_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reader_module, "RECORD_LIMIT", 256)
    path = write_session(tmp_path, rows=[{"type": "context", "data": "x" * 4096}, codex_message()])
    with SessionReader(path, "codex") as reader:
        assert [message.role for message in iter_messages(reader)] == ["user"]
        assert [issue.code for issue in reader.errors] == ["record_too_large"]


@pytest.mark.parametrize("source", ["unknown-provider", "user"])
def test_dsh_ambiguous_origin_is_reported(tmp_path: Path, source: str) -> None:
    row = dsh_message(source=source)
    if source == "user":
        del row["surfaceOp"]
    path = write_session(tmp_path, "dsh", rows=[row])
    with SessionReader(path, "dsh") as reader:
        assert not list(iter_messages(reader))
        assert [issue.code for issue in reader.errors] == ["ambiguous_message_origin"]


def test_seeded_boundary_prepass_is_not_used_by_discovery(tmp_path: Path) -> None:
    path = write_session(tmp_path, "dsh", header_values={"isSeeded": True}, tail=b"bad body\n")
    found = discover(dsh_root=tmp_path)
    assert [entry.header.path for entry in found.files] == [path]
    assert not found.errors
    with SessionReader(path, "dsh") as reader:
        assert not list(iter_messages(reader))
        assert reader.errors


def test_truncation_and_replacement_during_iteration_are_reported(tmp_path: Path) -> None:
    path = write_session(tmp_path, rows=[codex_message()])
    with SessionReader(path, "codex") as reader:
        iterator = iter_messages(reader)
        next(iterator)
        path.write_bytes(b"")
        list(iterator)
        assert [issue.code for issue in reader.errors] == ["source_changed"]


def test_dsh_filename_conflict(tmp_path: Path) -> None:
    path = write_session(tmp_path, "dsh")
    other = path.with_name("session.v3.jsonl")
    path.rename(other)
    with pytest.raises(ScanError) as caught, SessionReader(other, "dsh"):
        pytest.fail("Conflicting generation accepted")
    assert caught.value.issue.code == "version_conflict"


def test_checksum_failure_is_reported(tmp_path: Path) -> None:
    path = write_session(tmp_path, "dsh", compressed=True, rows=[dsh_message()])
    data = bytearray(path.read_bytes())
    data[-1] ^= 0xFF
    path.write_bytes(data)
    with SessionReader(path, "dsh") as reader:
        list(iter_messages(reader))
        assert reader.errors
        assert not reader.complete


def test_index_does_not_read_message_preview_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_session(tmp_path)
    with sqlite3.connect(tmp_path / "state_5.sqlite") as connection:
        connection.execute(
            "CREATE TABLE threads (id TEXT, cwd TEXT, title TEXT, first_user_message TEXT)"
        )
        connection.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?)",
            ("session-one", "/projects/index", "Title", "Private message preview"),
        )
    original = sqlite3.connect

    def authorize(
        operation: int,
        _table: str | None,
        column: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        if operation == sqlite3.SQLITE_READ and column == "first_user_message":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def protected_connect(database: str, *, uri: bool) -> sqlite3.Connection:
        connection = original(database, uri=uri)
        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(sqlite3, "connect", protected_connect)
    result = discover(codex_home=tmp_path)
    assert [entry.header.path for entry in result.files] == [path]
    assert result.files[0].title == "Title"
    assert not result.errors
