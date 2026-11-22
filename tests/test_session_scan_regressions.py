from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest
from session_scan import discovery
from session_scan.discovery import discover
from session_scan.main import main
from session_scan.messages import iter_messages
from session_scan.reader import SessionReader

from tests.test_session_scan import INVALID_ARGUMENTS, dsh_message, write_session

if TYPE_CHECKING:
    from pathlib import Path


def test_compressed_hit_survives_bad_following_frame(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_session(tmp_path, "dsh", compressed=True, rows=[dsh_message()])
    with path.open("ab") as output:
        output.write(b"invalid following frame")
    assert (
        main(["--source", "dsh", "--dsh-root", str(tmp_path), "--since", "2026-11-19T00:00:00Z"])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert [row["path"] for row in result["results"]] == [str(path)]
    assert result["errors"] == []
    # Exhausting the same file still reports the damaged tail after delivering the message.
    with SessionReader(path, "dsh") as reader:
        assert len(list(iter_messages(reader))) == 1
        assert [issue.code for issue in reader.errors] == ["record_read_error"]
        assert not reader.complete


@pytest.mark.parametrize("keep_shm", [False, True])
def test_wal_index_is_current_without_source_writes(tmp_path: Path, *, keep_shm: bool) -> None:
    path = write_session(tmp_path)
    database = tmp_path / "state_5.sqlite"
    wal = tmp_path / "state_5.sqlite-wal"
    shm = tmp_path / "state_5.sqlite-shm"
    with closing(sqlite3.connect(database)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE threads(id TEXT, cwd TEXT, title TEXT)")
        writer.execute("INSERT INTO threads VALUES ('session-one', '/projects/latest', 'Latest')")
        writer.commit()
        # Restore a coherent committed WAL pair after close, without a live SQLite writer.
        contents = {source: source.read_bytes() for source in (database, wal, shm)}
    for source, data in contents.items():
        if source != shm or keep_shm:
            source.write_bytes(data)
    before = {source.name: source.read_bytes() for source in tmp_path.iterdir() if source.is_file()}
    found = discover(codex_home=tmp_path)
    after = {source.name: source.read_bytes() for source in tmp_path.iterdir() if source.is_file()}
    assert after == before
    assert not found.errors
    assert [session.header.path for session in found.files] == [path]
    assert found.files[0].cwd == "/projects/latest"
    assert found.files[0].title == "Latest"


def test_changing_index_falls_back_to_file_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_session(tmp_path)
    database = tmp_path / "state_5.sqlite"
    with closing(sqlite3.connect(database)) as writer, writer:
        writer.execute("CREATE TABLE threads(id TEXT, cwd TEXT)")
        writer.execute("INSERT INTO threads VALUES ('session-one', '/projects/index')")
    # Inject a write after copying, before the source stability check.
    original = discovery._copy_index_file  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001

    def change_source(source: Path, target: Path, size: int) -> None:
        original(source, target, size)
        stamp = source.stat()
        os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))

    monkeypatch.setattr(discovery, "_copy_index_file", change_source)
    found = discover(codex_home=tmp_path)
    assert [session.header.path for session in found.files] == [path]
    assert found.files[0].cwd == "/projects/demo"
    assert [issue.code for issue in found.errors] == ["index_read_error"]


def test_missing_default_root_is_only_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "missing"))
    dsh = tmp_path / "dsh"
    expected = write_session(dsh, "dsh", compressed=True)
    assert main(["--dsh-root", str(dsh)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [row["path"] for row in result["results"]] == [str(expected)]
    assert not result["errors"]
    assert result["warnings"]
    assert all(issue["code"] == "missing_root" for issue in result["warnings"])


def test_inaccessible_default_root_preserves_other_source_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    blocked = tmp_path / "blocked"
    codex = blocked / "codex"
    write_session(codex)
    dsh = tmp_path / "dsh"
    expected = write_session(dsh, "dsh", compressed=True)
    monkeypatch.setenv("CODEX_HOME", str(codex))
    mode = blocked.stat().st_mode
    blocked.chmod(0)
    try:
        code = main(["--dsh-root", str(dsh)])
    finally:
        blocked.chmod(mode)
    assert code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "partial_failure"
    assert [row["path"] for row in result["results"]] == [str(expected)]
    assert any(issue["code"] == "directory_read_error" for issue in result["errors"])


@pytest.mark.parametrize("option", ["--codex-home", "--dsh-root"])
def test_inaccessible_explicit_root_is_argument_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], option: str
) -> None:
    blocked = tmp_path / "blocked"
    root = blocked / "root"
    root.mkdir(parents=True)
    mode = blocked.stat().st_mode
    blocked.chmod(0)
    try:
        with pytest.raises(SystemExit) as caught:
            main([option, str(root)])
    finally:
        blocked.chmod(mode)
    assert caught.value.code == INVALID_ARGUMENTS
    captured = capsys.readouterr()
    assert not captured.out
    assert "error:" in captured.err
