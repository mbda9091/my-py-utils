from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from skill_sync.errors import SkillSyncError
from skill_sync.security import assert_publish_safe, scan_publish_directory

if TYPE_CHECKING:
    from pathlib import Path


def test_security_blocks_credential_files(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TOKEN=value\n", encoding="utf-8")

    with pytest.raises(SkillSyncError, match="blocked file type"):
        assert_publish_safe(tmp_path)


def test_security_blocks_token_patterns_without_printing_value(tmp_path: Path) -> None:
    sample_value = "ghp_" + "1234567890abcdef"
    (tmp_path / "SKILL.md").write_text(f"token: {sample_value}\n", encoding="utf-8")

    with pytest.raises(SkillSyncError) as error:
        assert_publish_safe(tmp_path)

    assert sample_value not in str(error.value)
    assert "GitHub token-like value" in str(error.value)


def test_security_blocks_local_absolute_paths(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text(
        "Use /Users/example/.agents/skills/foo as a placeholder.\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillSyncError, match="local absolute path"):
        assert_publish_safe(tmp_path)


def test_security_reports_low_risk_keywords(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("Mention the word token as a concept.\n", encoding="utf-8")

    findings = scan_publish_directory(tmp_path)

    assert findings[0].severity == "low"
    assert findings[0].reason == "sensitive keyword"


def test_security_uses_publish_ignore_rules(tmp_path: Path) -> None:
    ignored = tmp_path / ".venv"
    ignored.mkdir()
    (ignored / "secret.pem").write_text("BEGIN PRIVATE KEY\n", encoding="utf-8")

    assert scan_publish_directory(tmp_path) == []
