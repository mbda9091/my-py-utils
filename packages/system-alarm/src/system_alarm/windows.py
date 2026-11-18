from system_alarm.models import AlarmError, AlarmResult


def create_alarm(time: str, *, label: str) -> AlarmResult:  # noqa: ARG001
    msg = "Native system alarms on Windows are not implemented yet."
    raise AlarmError(msg)
