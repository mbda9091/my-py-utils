import json
import math
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from system_alarm.models import AlarmError, AlarmResult

SHORTCUTS_PATH = Path("/usr/bin/shortcuts")


def _validate_options(shortcut: object, timeout: object) -> None:
    if (
        not isinstance(shortcut, str)
        or not shortcut.strip()
        or shortcut.startswith("-")
        or "\x00" in shortcut
    ):
        msg = "shortcut must be a non-empty name or identifier, without NUL or a leading '-'."
        raise AlarmError(msg)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        msg = "timeout must be a finite number greater than zero."
        raise AlarmError(msg)


def create_alarm(time: str, *, label: str, shortcut: str, timeout: float) -> AlarmResult:
    """Run the macOS shortcut once with a JSON file containing the local time and label."""
    _validate_options(shortcut, timeout)
    if not SHORTCUTS_PATH.is_file():
        msg = "macOS Shortcuts CLI is unavailable at /usr/bin/shortcuts."
        raise AlarmError(msg)

    try:
        with TemporaryDirectory(prefix="system-alarm-") as directory:
            input_path = Path(directory) / "alarm.json"
            input_path.write_text(
                json.dumps({"time": time, "label": label}, ensure_ascii=False),
                encoding="utf-8",
            )
            subprocess.run(  # noqa: S603
                [str(SHORTCUTS_PATH), "run", shortcut, "--input-path", str(input_path)],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
    except subprocess.TimeoutExpired as exc:
        msg = (
            "Shortcuts timed out; the alarm may already exist. Check Clock before retrying "
            "to avoid duplicates. Complete any permission prompts in Shortcuts first."
        )
        raise AlarmError(msg) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "No error details returned.").strip()
        msg = (
            f"Shortcut {shortcut!r} failed: {detail}\n"
            "Check that the shortcut is installed and configured with the Create Alarm action, "
            "and run it manually to grant permissions. Check Clock before retrying; "
            "the alarm may already exist."
        )
        raise AlarmError(msg) from exc
    except OSError as exc:
        msg = f"Unable to invoke macOS Shortcuts: {exc}"
        raise AlarmError(msg) from exc

    return AlarmResult(time=time, label=label, shortcut=shortcut)
