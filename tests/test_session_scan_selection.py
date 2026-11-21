from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from session_scan import selection
from session_scan.discovery import discover
from session_scan.main import main
from session_scan.models import Discovery, Issue
from session_scan.selection import compile_filters, select_files

from tests.test_session_scan import MILLISECONDS, codex_message, dsh_message, write_session

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from session_scan.models import Message, Object, Source
    from session_scan.reader import SessionReader

INVALID_ARGUMENTS = 2
FIRST_MATCH_LINE = 3
INTERRUPTED = 130


@pytest.fixture
def set_timezone(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    previous = os.environ.get("TZ")

    def apply(zone: str) -> None:
        monkeypatch.setenv("TZ", zone)
        time.tzset()

    yield apply
    if previous is None:
        monkeypatch.delenv("TZ", raising=False)
    else:
        monkeypatch.setenv("TZ", previous)
    time.tzset()


@pytest.mark.parametrize(
    ("cwd", "exact", "reason"),
    [
        ("/projects/demo", False, None),
        ("/projects/demo/sub", False, None),
        ("/projects/demo-backup", False, "directory_excluded_files"),
        ("/projects/demo/sub", True, "directory_excluded_files"),
        ("/projects/demo", True, None),
        (None, False, "unknown_cwd_excluded_files"),
        ("/projects/other", False, "directory_excluded_files"),
    ],
)
def test_directory_boundaries(cwd: str | None, *, exact: bool, reason: str | None) -> None:
    filters = compile_filters(["/projects/demo"], exact=exact)
    assert filters.directory_reason(cwd) == reason


def test_directory_normalization_union_and_unknown_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    filters = compile_filters(["missing/../a", "a", "b"])
    assert filters.directories == (tmp_path / "a", tmp_path / "b")
    assert filters.directory_reason(str(tmp_path / "b" / "child")) is None
    assert compile_filters().directory_reason(None) is None
    # Matching is lexical: case and a symlink alias are separate working directories.
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    assert compile_filters([str(alias)]).directory_reason(str(actual)) is not None
    assert compile_filters([str(actual)]).directory_reason(str(actual).upper()) is not None


@pytest.mark.parametrize(
    ("day", "hours", "offset_before", "offset_after"),
    [
        ("2026-03-08", 23, "-05:00", "-04:00"),
        ("2026-11-01", 25, "-04:00", "-05:00"),
    ],
)
def test_local_date_handles_daylight_saving(
    set_timezone: Callable[[str], None], day: str, hours: int, offset_before: str, offset_after: str
) -> None:
    set_timezone("America/New_York")
    filters = compile_filters(since=day, until=day)
    assert filters.since is not None
    assert filters.until is not None
    assert filters.until - filters.since == timedelta(hours=hours)
    assert str(filters.local_date_boundaries["since"]).endswith(offset_before)
    assert str(filters.local_date_boundaries["until"]).endswith(offset_after)


def test_date_calendar_and_explicit_offsets(set_timezone: Callable[[str], None]) -> None:
    set_timezone("Asia/Shanghai")
    filters = compile_filters(since="2028-02-29", until="2028-02-29")
    assert filters.since == datetime(2028, 2, 28, 16, tzinfo=UTC)
    assert filters.until == datetime(2028, 2, 29, 16, tzinfo=UTC)
    offset = compile_filters(since="2026-11-19T10:00:00+08:00", until="2026-11-19T03:00:00Z")
    assert offset.since == datetime(2026, 11, 19, 2, tzinfo=UTC)
    assert offset.until == datetime(2026, 11, 19, 3, tzinfo=UTC)
    assert not offset.local_date_boundaries


@pytest.mark.parametrize(
    "value", ["", "bad", "2026-02-29", "2026-13-01", "2026-11-19T08:00:00", "9999-12-31"]
)
def test_invalid_time_values(value: str) -> None:
    with pytest.raises(ValueError, match="Time must"):
        compile_filters(until=value)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--exact-directory"],
        ["--directory", ""],
        ["--since", "bad"],
        ["--since", "2026-11-20T00:00:00Z", "--until", "2026-11-20T00:00:00Z"],
        ["--since", "2026-11-21", "--until", "2026-11-19"],
    ],
)
def test_invalid_filters_do_not_access_sources(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(**_kwargs: object) -> Discovery:
        pytest.fail("Invalid filters accessed discovery")

    monkeypatch.setattr("session_scan.main.discover", unexpected)
    with pytest.raises(SystemExit) as caught:
        main(arguments)
    assert caught.value.code == INVALID_ARGUMENTS


def test_no_time_or_excluded_directory_does_not_iterate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_session(tmp_path, tail=b"bad body\n")
    found = discover(codex_home=tmp_path)

    def unexpected(_reader: SessionReader) -> Iterator[Message]:
        pytest.fail("Metadata query iterated dialogue")

    monkeypatch.setattr(selection, "iter_messages", unexpected)
    entries, stats, errors = select_files(found, compile_filters(["/projects/demo"]))
    assert len(entries) == 1
    assert stats["body_scanned_files"] == 0
    assert not errors
    entries, stats, errors = select_files(
        found, compile_filters(["/other"], since="2026-11-19T00:00:00Z")
    )
    assert not entries
    assert stats["directory_excluded_files"] == 1
    assert stats["body_scanned_files"] == 0
    assert not errors


@pytest.mark.parametrize("source", ["codex", "dsh"])
def test_time_scan_uses_messages_and_stops_on_first_hit(tmp_path: Path, source: Source) -> None:
    message = codex_message() if source == "codex" else dsh_message()
    path = write_session(
        tmp_path, source, compressed=source == "dsh", rows=[message], tail=b"bad tail\n" * 10000
    )
    os.utime(path, (0, 0))  # A file's mtime must not pre-exclude an in-range message.
    found = discover(**({"codex_home": tmp_path} if source == "codex" else {"dsh_root": tmp_path}))
    filters = compile_filters(
        ["/projects/demo"], since="2026-11-19T08:00:00Z", until="2026-11-19T09:00:00Z"
    )
    entries, stats, errors = select_files(found, filters)
    assert len(entries) == 1
    assert entries[0]["match"] is not None
    assert stats["early_stopped_files"] == 1
    assert not errors


def test_exact_upper_and_nonchronological_records(tmp_path: Path) -> None:
    path = write_session(
        tmp_path,
        rows=[
            codex_message(stamp="2026-11-19T09:00:00Z"),
            codex_message(stamp="2026-11-19T08:00:00Z"),
        ],
    )
    found = discover(codex_home=tmp_path)
    filters = compile_filters(since="2026-11-19T08:00:00Z", until="2026-11-19T09:00:00Z")
    entries, _, errors = select_files(found, filters)
    assert entries[0]["path"] == str(path)
    match = entries[0]["match"]
    assert isinstance(match, dict)
    assert match["line_number"] == FIRST_MATCH_LINE
    assert match["timestamp"] == "2026-11-19T08:00:00+00:00"
    assert not errors


def test_span_and_configuration_are_not_actual_message_matches(tmp_path: Path) -> None:
    write_session(
        tmp_path,
        rows=[
            codex_message(stamp="2026-11-18T08:00:00Z"),
            {"type": "turn_context", "timestamp": "2026-11-19T08:00:00Z"},
            codex_message(stamp="2026-11-20T08:00:00Z"),
        ],
    )
    entries, stats, errors = select_files(
        discover(codex_home=tmp_path),
        compile_filters(since="2026-11-19T00:00:00Z", until="2026-11-20T00:00:00Z"),
    )
    assert not entries
    assert stats["time_excluded_files"] == 1
    assert stats["indeterminate_time_files"] == 0
    assert not errors


@pytest.mark.parametrize("hit", [False, True])
def test_partial_time_scan_never_masquerades_as_no_match(tmp_path: Path, *, hit: bool) -> None:
    rows = [codex_message(stamp=None)]
    if hit:
        rows.append(codex_message())
    write_session(tmp_path, rows=rows)
    entries, stats, errors = select_files(
        discover(codex_home=tmp_path), compile_filters(since="2026-11-19T00:00:00Z")
    )
    assert bool(entries) == hit
    assert stats["indeterminate_time_files"] == 1
    assert stats["time_excluded_files"] == 0
    assert [error.code for error in errors] == ["missing_message_time"]
    if hit:
        match = entries[0]["match"]
        assert isinstance(match, dict)
        assert match["scan_state"] == "first_match_with_errors"


def test_unknown_cwd_time_only_and_missing_file(tmp_path: Path) -> None:
    path = write_session(tmp_path, header_values={"cwd": "relative"}, rows=[codex_message()])
    found = discover(codex_home=tmp_path)
    entries, _, errors = select_files(found, compile_filters(since="2026-11-19T00:00:00Z"))
    assert len(entries) == 1
    assert entries[0]["cwd"] is None
    assert not errors
    entries, stats, _ = select_files(found, compile_filters(["/projects"]))
    assert not entries
    assert stats["unknown_cwd_excluded_files"] == 1
    path.unlink()
    entries, stats, errors = select_files(found, compile_filters(since="2026-11-19T00:00:00Z"))
    assert not entries
    assert stats["indeterminate_time_files"] == 1
    assert errors


def test_header_change_after_discovery_is_not_scanned(tmp_path: Path) -> None:
    write_session(tmp_path, rows=[codex_message()])
    found = discover(codex_home=tmp_path)
    write_session(tmp_path, header_values={"id": "replaced"}, rows=[codex_message()])
    entries, stats, errors = select_files(found, compile_filters(since="2026-11-19T00:00:00Z"))
    assert not entries
    assert stats["body_scanned_files"] == 0
    assert [error.code for error in errors] == ["source_changed"]


def test_json_cli_combined_filters_and_positions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    message = dsh_message()
    write_session(tmp_path, "dsh", compressed=True, rows=[message])
    assert (
        main(
            [
                "--source",
                "dsh",
                "--dsh-root",
                str(tmp_path),
                "--directory",
                "/projects/demo",
                "--exact-directory",
                "--since",
                "2026-11-19T00:00:00Z",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["filters"]["directory_match"] == "exact"
    assert result["results"][0]["match"]["seq"] == 0
    assert result["summary"]["early_stopped_files"] == 1
    assert "match_count" not in result["results"][0]


def test_errors_preserve_other_results_and_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_session(tmp_path / "valid", rows=[codex_message()])
    write_session(tmp_path / "invalid", rows=[codex_message(stamp=None)])
    # Both subtrees are session roots below this one Codex home.
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    for name in ("valid", "invalid"):
        original = tmp_path / name / "sessions" / "one.jsonl"
        original.rename(sessions / f"{name}.jsonl")
    assert (
        main(
            ["--source", "codex", "--codex-home", str(tmp_path), "--since", "2026-11-19T00:00:00Z"]
        )
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert len(result["results"]) == 1
    assert result["summary"]["indeterminate_time_files"] == 1
    assert result["status"] == "partial_failure"


def test_time_sort_uses_first_file_order_match(tmp_path: Path) -> None:
    first = write_session(
        tmp_path / "a",
        rows=[
            codex_message(stamp="2026-11-19T08:00:00Z"),
            codex_message(stamp="2026-11-19T12:00:00Z"),
        ],
    )
    second = write_session(tmp_path / "b", rows=[codex_message(stamp="2026-11-19T09:00:00Z")])
    found = Discovery()
    for home in (tmp_path / "a", tmp_path / "b"):
        found.files.extend(discover(codex_home=home).files)
    entries, _, errors = select_files(found, compile_filters(since="2026-11-19T00:00:00Z"))
    assert [row["path"] for row in entries] == [str(second), str(first)]
    assert not errors


def test_permission_failure_remains_partial_without_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_session(tmp_path, rows=[codex_message()])
    found = discover(codex_home=tmp_path)

    def denied(_reader: SessionReader) -> Iterator[Message]:
        raise PermissionError

    monkeypatch.setattr(selection, "iter_messages", denied)
    entries, stats, errors = select_files(found, compile_filters(since="2026-11-19T00:00:00Z"))
    assert not entries
    assert stats["indeterminate_time_files"] == 1
    assert [error.code for error in errors] == ["file_read_error"]


def test_interrupt_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_session(tmp_path)

    def interrupted(
        _found: Discovery, _filters: selection.Filters
    ) -> tuple[list[Object], dict[str, int], list[Issue]]:
        raise KeyboardInterrupt

    monkeypatch.setattr("session_scan.main.select_files", interrupted)
    assert main(["--source", "codex", "--codex-home", str(tmp_path)]) == INTERRUPTED


def test_seeded_missing_boundary_is_indeterminate(tmp_path: Path) -> None:
    write_session(tmp_path, "dsh", header_values={"isSeeded": True}, rows=[dsh_message()])
    entries, stats, errors = select_files(
        discover(dsh_root=tmp_path), compile_filters(since="2026-11-19T00:00:00Z")
    )
    assert not entries
    assert stats["indeterminate_time_files"] == 1
    assert stats["time_excluded_files"] == 0
    assert [error.code for error in errors] == ["missing_seed_boundary"]


def test_incomplete_compressed_no_hit_is_not_confirmed_exclusion(tmp_path: Path) -> None:
    path = write_session(tmp_path, "dsh", compressed=True, rows=[dsh_message()])
    path.write_bytes(path.read_bytes()[:-1])
    entries, stats, errors = select_files(
        discover(dsh_root=tmp_path), compile_filters(since="2026-11-20T00:00:00Z")
    )
    assert not entries
    assert errors
    assert stats["indeterminate_time_files"] == 1
    assert stats["time_excluded_files"] == 0


@pytest.mark.parametrize(
    ("since", "until", "hit"),
    [
        ("2026-11-19T08:00:00Z", None, True),
        (None, "2026-11-19T08:00:00Z", False),
        (None, "2026-11-19T08:00:01Z", True),
    ],
)
def test_one_sided_boundaries(
    tmp_path: Path, since: str | None, until: str | None, *, hit: bool
) -> None:
    write_session(tmp_path, rows=[codex_message()])
    entries, _, errors = select_files(
        discover(codex_home=tmp_path), compile_filters(since=since, until=until)
    )
    assert bool(entries) == hit
    assert not errors


def test_upper_date_includes_full_local_day(
    tmp_path: Path, set_timezone: Callable[[str], None]
) -> None:
    set_timezone("Asia/Shanghai")
    write_session(tmp_path, rows=[codex_message(stamp="2026-11-19T15:59:59Z")])
    found = discover(codex_home=tmp_path)
    entries, _, errors = select_files(
        found, compile_filters(since="2026-11-19", until="2026-11-19")
    )
    assert len(entries) == 1
    assert not errors


def test_special_paths_and_titles_remain_json_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = write_session(tmp_path, header_values={"title": "Title\nnext line"})
    path = original.with_name("odd\nname.jsonl")
    original.rename(path)
    assert main(["--source", "codex", "--codex-home", str(tmp_path)]) == 0
    raw = capsys.readouterr().out
    assert raw.count("\n") == 1
    result = json.loads(raw)
    assert result["results"][0]["title"] == "Title\nnext line"
    assert result["results"][0]["path"] == str(path)
    assert result["results"][0]["match"] is None


def test_empty_result_and_invalid_explicit_root_are_distinct(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_session(tmp_path)
    assert main(["--source", "codex", "--codex-home", str(tmp_path), "--directory", "/absent"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["results"] == []
    assert result["summary"]["directory_excluded_files"] == 1
    with pytest.raises(SystemExit) as caught:
        main(["--source", "codex", "--codex-home", str(tmp_path / "missing")])
    assert caught.value.code == INVALID_ARGUMENTS


def test_selected_summary_counts_are_consistent(tmp_path: Path) -> None:
    write_session(tmp_path, "dsh", compressed=True, rows=[dsh_message()])
    found = discover(dsh_root=tmp_path)
    entries, stats, _ = select_files(found, compile_filters(since="2026-11-19T00:00:00Z"))
    assert stats["matched_files"] == len(entries)
    assert stats["valid_files"] == stats["header_only_files"] + stats["body_scanned_files"]
    assert stats["early_stopped_files"] == stats["matched_files"]
    assert stats["error_count"] == 0


@pytest.mark.parametrize(
    "source_kind",
    [
        "runtime-context",
        "time-context",
        "tool-jobs",
        "schedule",
        "user-approval",
        "fworkflow",
        "fcodex-subagent-input-notice",
    ],
)
def test_dsh_attributed_context_does_not_count_as_dialogue(
    tmp_path: Path, source_kind: str
) -> None:
    context = dsh_message(source=source_kind)
    actual = dsh_message(1)
    actual["time"] = MILLISECONDS + int(timedelta(days=1).total_seconds() * 1000)
    write_session(tmp_path, "dsh", compressed=True, rows=[context, actual])
    entries, stats, errors = select_files(
        discover(dsh_root=tmp_path),
        compile_filters(since="2026-11-19T00:00:00Z", until="2026-11-20T00:00:00Z"),
    )
    assert not entries
    assert not errors
    assert stats["time_excluded_files"] == 1


def test_index_updated_time_does_not_pre_exclude_messages(tmp_path: Path) -> None:
    write_session(tmp_path, rows=[codex_message()])
    with sqlite3.connect(tmp_path / "state_5.sqlite") as connection:
        connection.execute("CREATE TABLE threads (id TEXT, cwd TEXT, updated_at INTEGER)")
        connection.execute("INSERT INTO threads VALUES ('session-one', '/projects/demo', 0)")
    found = discover(codex_home=tmp_path)
    assert found.files[0].updated_at == datetime(1970, 1, 1, tzinfo=UTC)
    entries, _, errors = select_files(found, compile_filters(since="2026-11-19T00:00:00Z"))
    assert len(entries) == 1
    assert not errors


def test_one_unreadable_root_preserves_other_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex"
    dsh_root = tmp_path / "dsh"
    write_session(codex_home)
    expected = write_session(dsh_root, "dsh", compressed=True)
    original = type(tmp_path).resolve

    def guarded(root: Path, *, strict: bool = False) -> Path:
        if root == codex_home / "sessions":
            raise PermissionError
        return original(root, strict=strict)

    monkeypatch.setattr(type(tmp_path), "resolve", guarded)
    found = discover(codex_home=codex_home, dsh_root=dsh_root)
    entries, _, errors = select_files(found, compile_filters())
    assert [row["path"] for row in entries] == [str(expected)]
    assert [issue.code for issue in errors] == ["directory_read_error"]
