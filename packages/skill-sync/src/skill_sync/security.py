from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from skill_sync.errors import SkillSyncError

if TYPE_CHECKING:
    from pathlib import Path

Severity = Literal["high", "low"]

IGNORED_DIR_NAMES = {".git", ".venv", "__pycache__"}
IGNORED_FILE_NAMES = {".DS_Store"}
IGNORED_SUFFIXES = {".pyc"}
BLOCKED_FILE_NAMES = {"id_rsa", ".env", ".npmrc", ".pypirc", ".netrc"}
BLOCKED_FILE_ENDINGS = (".pem", ".key", ".p12", ".zip", ".tar", ".tar.gz", ".tgz", ".dmg")
LOW_RISK_WORDS = re.compile(r"\b(token|password|secret)\b", re.IGNORECASE)
LOCAL_ABSOLUTE_PATH = re.compile(r"/Users/[^/\s]+/[^\s)]*")
HIGH_RISK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ghp_[A-Za-z0-9_]{8,}"), "GitHub token-like value"),
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "OpenAI token-like value"),
    (re.compile(r"AKIA[A-Z0-9]{16}"), "AWS access key-like value"),
    (re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"), "private key block"),
    (re.compile(r"Authorization:\s*Bearer\s+\S+", re.IGNORECASE), "bearer token header"),
    (LOCAL_ABSOLUTE_PATH, "local absolute path"),
)
MAX_TEXT_SCAN_BYTES = 2_000_000
LOW_RISK_SIZE_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class SecurityFinding:
    path: Path
    severity: Severity
    reason: str


def should_ignore_path(path: Path) -> bool:
    return (
        any(part in IGNORED_DIR_NAMES for part in path.parts)
        or path.name in IGNORED_FILE_NAMES
        or path.suffix in IGNORED_SUFFIXES
    )


def iter_publish_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        relative_path = path.relative_to(root)
        if should_ignore_path(relative_path):
            continue
        if path.is_file() or path.is_symlink():
            files.append(path)
    return files


def scan_publish_directory(root: Path) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    for path in iter_publish_files(root):
        relative_path = path.relative_to(root)
        findings.extend(_scan_path(path, relative_path))
    return findings


def assert_publish_safe(root: Path) -> list[SecurityFinding]:
    findings = scan_publish_directory(root)
    high_risk = [finding for finding in findings if finding.severity == "high"]
    if high_risk:
        detail = "\n".join(
            f"- {finding.path}: {finding.reason}" for finding in sorted(high_risk, key=_finding_key)
        )
        msg = f"Publish blocked by high-risk content:\n{detail}"
        raise SkillSyncError(msg)
    return findings


def _scan_path(path: Path, relative_path: Path) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    name = path.name
    if name in BLOCKED_FILE_NAMES or any(name.endswith(ending) for ending in BLOCKED_FILE_ENDINGS):
        findings.append(
            SecurityFinding(relative_path, "high", "blocked file type or credential file"),
        )

    if path.is_symlink():
        target = path.readlink()
        if target.is_absolute() and LOCAL_ABSOLUTE_PATH.search(str(target)) is not None:
            findings.append(
                SecurityFinding(relative_path, "high", "symlink points to a local path"),
            )
        return findings

    try:
        size = path.stat().st_size
    except OSError as exc:
        findings.append(SecurityFinding(relative_path, "high", f"unable to inspect file: {exc}"))
        return findings

    if size > LOW_RISK_SIZE_BYTES:
        findings.append(SecurityFinding(relative_path, "low", "large file"))

    text = _read_scannable_text(path)
    if text is None:
        return findings

    findings.extend(
        SecurityFinding(relative_path, "high", reason)
        for pattern, reason in HIGH_RISK_PATTERNS
        if pattern.search(text) is not None
    )
    if LOW_RISK_WORDS.search(text) is not None and not any(
        finding.severity == "high" for finding in findings
    ):
        findings.append(SecurityFinding(relative_path, "low", "sensitive keyword"))
    return findings


def _read_scannable_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > MAX_TEXT_SCAN_BYTES:
        data = data[:MAX_TEXT_SCAN_BYTES]
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _finding_key(finding: SecurityFinding) -> tuple[str, str]:
    return (str(finding.path), finding.reason)
