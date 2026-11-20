from __future__ import annotations


class SkillSyncError(RuntimeError):
    """Error that can be shown directly to CLI users."""


class TargetSkillExistsError(SkillSyncError):
    """Raised when existing target content prevents installing a personal skill."""


class LegacyConfigError(SkillSyncError):
    """Raised when local repository commands encounter a legacy registry config."""
