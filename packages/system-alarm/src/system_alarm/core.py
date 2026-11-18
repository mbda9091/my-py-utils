from __future__ import annotations

import re
import sys

from system_alarm.models import AlarmError, AlarmResult

DEFAULT_SHORTCUT = "My Py Utils - Create Alarm"
DEFAULT_TIMEOUT = 60.0


def _validate_request(time: object, label: object) -> None:
    if not isinstance(time, str) or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", time) is None:
        msg = "time must be a 24-hour local time in HH:MM format (00:00 to 23:59)."
        raise AlarmError(msg)
    if not isinstance(label, str) or not label.strip() or "\x00" in label:
        msg = "label must be non-empty text without NUL characters."
        raise AlarmError(msg)


def create_alarm(
    time: str,
    *,
    label: str = "Alarm",
    shortcut: str = DEFAULT_SHORTCUT,
    timeout: float = DEFAULT_TIMEOUT,
) -> AlarmResult:
    """Request a one-time alarm at a local time on the current platform."""
    _validate_request(time, label)
    # Load only the selected backend so native dependencies stay platform-local.
    if sys.platform == "darwin":
        from system_alarm import macos  # noqa: PLC0415

        return macos.create_alarm(time, label=label, shortcut=shortcut, timeout=timeout)
    if sys.platform == "win32":
        from system_alarm import windows  # noqa: PLC0415

        return windows.create_alarm(time, label=label)

    msg = "Native system alarms currently support macOS only."
    raise AlarmError(msg)
