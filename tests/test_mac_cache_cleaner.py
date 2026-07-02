from __future__ import annotations

import io
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock

from mac_cache_cleaner.cli import (
    CleanCommand,
    CleanResult,
    clean_all,
    clean_one,
    resolve_paths,
)

if TYPE_CHECKING:
    import pytest


def test_resolve_paths_expands_android_studio_versions(tmp_path: Path) -> None:
    first = tmp_path / "Library/Caches/Google/AndroidStudio2023.1/vcs-log"
    second = tmp_path / "Library/Caches/Google/AndroidStudio2024.2/vcs-log"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    command = CleanCommand(
        "Android",
        "Android Studio vcs-log",
        ("~/Library/Caches/Google/AndroidStudio*/vcs-log",),
    )

    assert resolve_paths(command, tmp_path) == [first.resolve(), second.resolve()]


def test_clean_one_deletes_matched_paths(tmp_path: Path) -> None:
    konan = tmp_path / ".konan"
    konan.mkdir()
    command = CleanCommand("KMP", "~/.konan", ("~/.konan",))
    log_output = io.StringIO()

    result = clean_one(command, tmp_path, log_output)

    assert result == CleanResult("~/.konan", "done", str(konan.resolve()))
    assert not konan.exists()
    assert f"removing: {konan.resolve()}\n" in log_output.getvalue()


def test_clean_one_skips_missing_paths(tmp_path: Path) -> None:
    command = CleanCommand("Android", "~/.gradle", ("~/.gradle",))

    result = clean_one(command, tmp_path)

    assert result == CleanResult("~/.gradle", "skipped", "no matched path")


def test_clean_one_reports_missing_command(monkeypatch: pytest.MonkeyPatch) -> None:
    command = CleanCommand(
        "iOS",
        "gundam cache clean --all",
        command=("gundam", "cache", "clean", "--all"),
    )
    monkeypatch.setattr("shutil.which", Mock(return_value=None))

    result = clean_one(command, Path.home())

    assert result == CleanResult(
        "gundam cache clean --all",
        "failed",
        "command not found: gundam",
    )


def test_clean_one_reports_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    command = CleanCommand("iOS", "gundam cache clean --all", command=("gundam", "cache"))
    log_output = io.StringIO()
    run_mock = Mock(
        side_effect=subprocess.CalledProcessError(
            returncode=12,
            cmd=["/usr/local/bin/gundam", "cache"],
            output="stdout details",
            stderr="cache clean failed",
        ),
    )
    monkeypatch.setattr("shutil.which", Mock(return_value="/usr/local/bin/gundam"))
    monkeypatch.setattr(subprocess, "run", run_mock)

    result = clean_one(command, Path.home(), log_output)

    assert result == CleanResult("gundam cache clean --all", "failed", "cache clean failed")
    log = log_output.getvalue()
    assert "$ /usr/local/bin/gundam cache\n" in log
    assert "exit code: 12\n" in log
    assert "[stdout]\nstdout details\n" in log
    assert "[stderr]\ncache clean failed\n" in log


def test_clean_one_logs_successful_command_output(monkeypatch: pytest.MonkeyPatch) -> None:
    command = CleanCommand("iOS", "gundam cache clean --all", command=("gundam", "cache"))
    log_output = io.StringIO()
    run_mock = Mock(
        return_value=subprocess.CompletedProcess(
            args=["/usr/local/bin/gundam", "cache"],
            returncode=0,
            stdout="cleaned cache",
            stderr="warning line",
        ),
    )
    monkeypatch.setattr("shutil.which", Mock(return_value="/usr/local/bin/gundam"))
    monkeypatch.setattr(subprocess, "run", run_mock)

    result = clean_one(command, Path.home(), log_output)

    assert result == CleanResult("gundam cache clean --all", "done", "gundam cache")
    log = log_output.getvalue()
    assert "[stdout]\ncleaned cache\n" in log
    assert "[stderr]\nwarning line\n" in log


def test_clean_all_logs_each_command_and_keeps_going(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    konan = tmp_path / ".konan"
    konan.mkdir()
    output = io.StringIO()
    log_output = io.StringIO()
    monkeypatch.setattr("shutil.which", Mock(return_value=None))

    results = clean_all(home=tmp_path, output=output, log_output=log_output)

    assert not konan.exists()
    assert CleanResult("~/.gradle", "skipped", "no matched path") in results
    assert CleanResult("~/.konan", "done", str(konan.resolve())) in results

    log = output.getvalue()
    assert "[SECTION] Android\n" in log
    assert "[RUN] ~/.gradle\n" in log
    assert "[SKIP] ~/.gradle: no matched path\n" in log
    assert "[FAILED] gundam cache clean --all: command not found: gundam\n" in log
    assert "[SECTION DONE] KMP\n" in log

    file_log = log_output.getvalue()
    assert "[RUN] ~/.gradle\n" in file_log
    assert "matched paths: none\n" in file_log
    assert "[DONE] ~/.konan" in file_log
