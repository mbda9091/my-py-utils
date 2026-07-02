from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import TextIO

Section = Literal["Android", "iOS", "Harmony", "KMP"]
LOG_DIR = Path("Library/Logs/mac-cache-cleaner")


@dataclass(frozen=True, slots=True)
class CleanCommand:
    section: Section
    label: str
    paths: tuple[str, ...] = ()
    command: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class CleanResult:
    label: str
    status: Literal["done", "failed", "skipped"]
    detail: str


CLEAN_COMMANDS: tuple[CleanCommand, ...] = (
    CleanCommand("Android", "~/.gradle", ("~/.gradle",)),
    CleanCommand("Android", "~/.m2", ("~/.m2",)),
    CleanCommand("Android", "~/.lldb (Android)", ("~/.lldb",)),
    CleanCommand("Android", "~/.kwaipilot", ("~/.kwaipilot",)),
    CleanCommand(
        "Android",
        "Android Studio vcs-log",
        ("~/Library/Caches/Google/AndroidStudio*/vcs-log",),
    ),
    CleanCommand(
        "Android",
        "Android Studio index",
        ("~/Library/Caches/Google/AndroidStudio*/index",),
    ),
    CleanCommand("iOS", "gundam cache clean --all", command=("gundam", "cache", "clean", "--all")),
    CleanCommand(
        "iOS",
        "gundam static-libs",
        ("~/Library/Caches/com.kuaishou.gundam/static-libs",),
    ),
    CleanCommand("iOS", "~/Library/Caches/CocoaPods", ("~/Library/Caches/CocoaPods",)),
    CleanCommand(
        "iOS",
        "~/Library/Developer/Xcode/DerivedData",
        ("~/Library/Developer/Xcode/DerivedData",),
    ),
    CleanCommand("iOS", "~/.lldb (iOS)", ("~/.lldb",)),
    CleanCommand("Harmony", "~/.hvigor", ("~/.hvigor",)),
    CleanCommand("Harmony", "~/.lldb (Harmony)", ("~/.lldb",)),
    CleanCommand("Harmony", "~/.ohpm", ("~/.ohpm",)),
    CleanCommand("Harmony", "~/.npm", ("~/.npm",)),
    CleanCommand(
        "Harmony",
        "~/Library/Caches/Huawei/DevEcoStudio*",
        ("~/Library/Caches/Huawei/DevEcoStudio*",),
    ),
    CleanCommand("KMP", "~/.konan", ("~/.konan",)),
)


def expand_path_pattern(pattern: str, home: Path) -> list[Path]:
    if pattern.startswith("~/"):
        return sorted(home.glob(pattern[2:]))
    return sorted(Path().glob(pattern))


def resolve_paths(command: CleanCommand, home: Path) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for pattern in command.paths:
        for path in expand_path_pattern(pattern, home):
            resolved = path.resolve()
            if resolved not in seen:
                paths.append(resolved)
                seen.add(resolved)
    return paths


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink()


def write_log(log_output: TextIO | None, message: str) -> None:
    if log_output is not None:
        log_output.write(message)
        log_output.flush()


def text_from_process_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def write_process_output(log_output: TextIO | None, name: str, value: object) -> None:
    text = text_from_process_output(value).strip()
    if not text:
        return
    write_log(log_output, f"[{name}]\n{text}\n")


def clean_paths(command: CleanCommand, home: Path, log_output: TextIO | None = None) -> CleanResult:
    paths = resolve_paths(command, home)
    if not paths:
        write_log(log_output, "matched paths: none\n")
        return CleanResult(command.label, "skipped", "no matched path")

    write_log(log_output, "matched paths:\n")
    for path in paths:
        write_log(log_output, f"- {path}\n")

    removed: list[str] = []
    for path in paths:
        write_log(log_output, f"removing: {path}\n")
        try:
            remove_path(path)
        except OSError as exc:
            write_log(log_output, f"remove failed: {path}: {exc}\n")
            return CleanResult(command.label, "failed", f"{path}: {exc}")
        removed.append(str(path))
        write_log(log_output, f"removed: {path}\n")

    return CleanResult(command.label, "done", ", ".join(removed))


def clean_shell_command(
    command: CleanCommand,
    log_output: TextIO | None = None,
) -> CleanResult:
    if command.command is None:
        write_log(log_output, "missing shell command\n")
        return CleanResult(command.label, "failed", "missing shell command")

    executable = shutil.which(command.command[0])
    if executable is None:
        write_log(log_output, f"command not found: {command.command[0]}\n")
        return CleanResult(command.label, "failed", f"command not found: {command.command[0]}")

    resolved_command = [executable, *command.command[1:]]
    write_log(log_output, f"$ {' '.join(resolved_command)}\n")
    try:
        completed = subprocess.run(  # noqa: S603
            resolved_command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        write_log(log_output, f"exit code: {exc.returncode}\n")
        write_process_output(log_output, "stdout", exc.stdout)
        write_process_output(log_output, "stderr", exc.stderr)
        reason = (
            text_from_process_output(exc.stderr).strip()
            or text_from_process_output(exc.stdout).strip()
            or f"exit code {exc.returncode}"
        )
        return CleanResult(command.label, "failed", reason)

    write_log(log_output, f"exit code: {completed.returncode}\n")
    write_process_output(log_output, "stdout", completed.stdout)
    write_process_output(log_output, "stderr", completed.stderr)
    return CleanResult(command.label, "done", " ".join(command.command))


def clean_one(
    command: CleanCommand,
    home: Path,
    log_output: TextIO | None = None,
) -> CleanResult:
    if command.command is not None:
        return clean_shell_command(command, log_output)
    return clean_paths(command, home, log_output)


def print_result(result: CleanResult, output: TextIO) -> None:
    if result.status == "done":
        output.write(f"[DONE] {result.label}: {result.detail}\n")
    elif result.status == "skipped":
        output.write(f"[SKIP] {result.label}: {result.detail}\n")
    else:
        output.write(f"[FAILED] {result.label}: {result.detail}\n")


def clean_all(
    *,
    home: Path | None = None,
    output: TextIO = sys.stdout,
    log_output: TextIO | None = None,
) -> list[CleanResult]:
    resolved_home = Path.home() if home is None else home
    results: list[CleanResult] = []
    current_section: Section | None = None

    for command in CLEAN_COMMANDS:
        if command.section != current_section:
            if current_section is not None:
                output.write(f"[SECTION DONE] {current_section}\n")
                write_log(log_output, f"[SECTION DONE] {current_section}\n")
            current_section = command.section
            output.write(f"[SECTION] {current_section}\n")
            write_log(log_output, f"[SECTION] {current_section}\n")

        output.write(f"[RUN] {command.label}\n")
        write_log(log_output, f"[RUN] {command.label}\n")
        result = clean_one(command, resolved_home, log_output)
        print_result(result, output)
        if log_output is not None:
            print_result(result, log_output)
        results.append(result)

    if current_section is not None:
        output.write(f"[SECTION DONE] {current_section}\n")
        write_log(log_output, f"[SECTION DONE] {current_section}\n")

    return results


def create_log_path(home: Path | None = None) -> Path:
    log_home = Path.home() if home is None else home
    timestamp = datetime.now(tz=UTC).astimezone().strftime("%Y%m%d-%H%M%S")
    return log_home / LOG_DIR / f"{timestamp}.log"


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        sys.stderr.write("mac-cache-cleaner does not accept arguments.\n")
        return 2

    log_path = create_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout.write(f"[LOG] {log_path}\n")
    with log_path.open("w", encoding="utf-8") as log_output:
        log_output.write(f"mac-cache-cleaner log: {log_path}\n")
        results = clean_all(log_output=log_output)
    return 1 if any(result.status == "failed" for result in results) else 0
