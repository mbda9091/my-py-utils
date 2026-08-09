from __future__ import annotations


class SkillSyncError(RuntimeError):
    """Error that can be shown directly to CLI users."""


class TargetSkillExistsError(SkillSyncError):
    """Raised when an install target already exists and force is disabled."""
