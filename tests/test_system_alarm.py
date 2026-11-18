from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from system_alarm import (
    DEFAULT_SHORTCUT,
    AlarmError,
    AlarmResult,
    core,
    create_alarm,
    macos,
    windows,
)
from system_alarm import main as cli

TEST_TIMEOUT = 120
ARGPARSE_ERROR = 2


@pytest.fixture
def shortcuts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Mock:
    executable = tmp_path / "shortcuts"
    executable.touch()
    monkeypatch.setattr(core, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(macos, "SHORTCUTS_PATH", executable)
    run = Mock(return_value=subprocess.CompletedProcess(args=[], returncode=0))
    monkeypatch.setattr(subprocess, "run", run)
    return run


@pytest.mark.parametrize("time", ["00:00", "07:30", "12:00", "23:59"])
def test_create_passes_json_and_cleans_input(shortcuts: Mock, time: str) -> None:
    paths: list[Path] = []
    label = '起床 "quoted"; $(touch unwanted)\nnext line'

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[:4] == [str(macos.SHORTCUTS_PATH), "run", DEFAULT_SHORTCUT, "--input-path"]
        path = Path(command[4])
        paths.append(path)
        assert json.loads(path.read_text(encoding="utf-8")) == {"time": time, "label": label}
        if os.name == "posix":
            assert path.parent.stat().st_mode & 0o077 == 0
        assert kwargs == {
            "check": True,
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 60.0,
        }
        return subprocess.CompletedProcess(command, 0)

    shortcuts.side_effect = run
    assert create_alarm(time, label=label) == AlarmResult(time, label, DEFAULT_SHORTCUT)
    shortcuts.assert_called_once()
    assert paths
    assert not paths[0].parent.exists()


@pytest.mark.parametrize(
    "time",
    ["7:30", "24:00", "12:60", "12:00:00", " 07:30", "\uff10\uff17:\uff13\uff10", ""],
)
def test_invalid_time_does_not_execute(shortcuts: Mock, time: str) -> None:
    with pytest.raises(AlarmError, match="HH:MM"):
        create_alarm(time)
    shortcuts.assert_not_called()


@pytest.mark.parametrize("label", ["", "  ", "bad\x00label"])
def test_invalid_label(shortcuts: Mock, label: str) -> None:
    with pytest.raises(AlarmError, match="label"):
        create_alarm("07:30", label=label)
    shortcuts.assert_not_called()


@pytest.mark.parametrize("shortcut", ["", "  ", "--help", "bad\x00name"])
def test_invalid_shortcut(shortcuts: Mock, shortcut: str) -> None:
    with pytest.raises(AlarmError, match="shortcut"):
        create_alarm("07:30", shortcut=shortcut)
    shortcuts.assert_not_called()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_invalid_timeout(shortcuts: Mock, timeout: float) -> None:
    with pytest.raises(AlarmError, match="timeout"):
        create_alarm("07:30", timeout=timeout)
    shortcuts.assert_not_called()


@pytest.mark.parametrize(
    ("platform", "message"),
    [("linux", "macOS only"), ("win32", "Windows are not implemented yet")],
)
def test_platform_without_alarm_support(
    shortcuts: Mock, monkeypatch: pytest.MonkeyPatch, platform: str, message: str
) -> None:
    monkeypatch.setattr(core, "sys", SimpleNamespace(platform=platform))
    with pytest.raises(AlarmError, match=message):
        create_alarm("07:30")
    shortcuts.assert_not_called()


def test_missing_shortcuts(
    shortcuts: Mock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(macos, "SHORTCUTS_PATH", tmp_path / "missing")
    with pytest.raises(AlarmError, match="unavailable"):
        create_alarm("07:30")
    shortcuts.assert_not_called()


@pytest.mark.parametrize(
    ("error", "match"),
    [
        (subprocess.TimeoutExpired("shortcuts", 60), "may already exist"),
        (subprocess.CalledProcessError(1, "shortcuts", stderr="Shortcut not found"), "not found"),
        (subprocess.CalledProcessError(1, "shortcuts", output="Permission denied"), "Permission"),
        (subprocess.CalledProcessError(1, "shortcuts"), "No error details"),
        (OSError("Cannot launch"), "Cannot launch"),
    ],
)
def test_execution_failure_cleans_input_without_retry(
    shortcuts: Mock, error: Exception, match: str
) -> None:
    shortcuts.side_effect = error
    with pytest.raises(AlarmError, match=match):
        create_alarm("07:30")
    shortcuts.assert_called_once()
    input_path = Path(shortcuts.call_args.args[0][4])
    assert not input_path.parent.exists()


def test_input_write_failure(shortcuts: Mock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "write_text", Mock(side_effect=OSError("No space")))
    with pytest.raises(AlarmError, match="No space"):
        create_alarm("07:30")
    shortcuts.assert_not_called()


def test_custom_shortcut_and_timeout(shortcuts: Mock) -> None:
    result = create_alarm("08:00", shortcut="Personal Alarm", timeout=TEST_TIMEOUT)
    assert result.shortcut == "Personal Alarm"
    assert result.label == "Alarm"
    assert shortcuts.call_args.args[0][2] == "Personal Alarm"
    assert shortcuts.call_args.kwargs["timeout"] == TEST_TIMEOUT


def test_cli_success(shortcuts: Mock, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["create", "--time", "09:15", "--label", "会议"]) == 0
    assert "09:15 — 会议" in capsys.readouterr().out
    shortcuts.assert_called_once()


def test_windows_backend_can_return_without_shortcuts(
    shortcuts: Mock, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    backend = Mock(return_value=AlarmResult("09:15", "会议"))
    monkeypatch.setattr(core, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(windows, "create_alarm", backend)

    assert cli.main(["create", "--time", "09:15", "--label", "会议"]) == 0
    assert capsys.readouterr().out == "Alarm request completed: 09:15 — 会议.\n"
    backend.assert_called_once_with("09:15", label="会议")
    shortcuts.assert_not_called()


def test_cli_options_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    create = Mock(return_value=AlarmResult("09:15", "会议", "custom"))
    monkeypatch.setattr(cli, "create_alarm", create)
    assert (
        cli.main(
            [
                "create",
                "--time",
                "09:15",
                "--label",
                "会议",
                "--shortcut",
                "custom",
                "--timeout",
                "90",
            ]
        )
        == 0
    )
    create.assert_called_once_with("09:15", label="会议", shortcut="custom", timeout=90.0)


@pytest.mark.parametrize("time", ["invalid", "07:30"])
def test_cli_failure(shortcuts: Mock, capsys: pytest.CaptureFixture[str], time: str) -> None:
    shortcuts.side_effect = OSError("Failure")
    assert cli.main(["create", "--time", time]) == 1
    captured = capsys.readouterr()
    assert "system-alarm:" in captured.err
    assert not captured.out


@pytest.mark.parametrize(
    "argv", [[], ["create"], ["delete"], ["create", "--time", "07:30", "--bad"]]
)
def test_cli_syntax_error(shortcuts: Mock, argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == ARGPARSE_ERROR
    shortcuts.assert_not_called()
