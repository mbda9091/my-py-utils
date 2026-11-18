from dataclasses import dataclass


class AlarmError(Exception):
    """An alarm request failed validation or could not be handled by the platform."""


@dataclass(frozen=True, slots=True)
class AlarmResult:
    """A completed alarm request. The macOS backend reports the shortcut it invoked."""

    time: str
    label: str
    shortcut: str | None = None
