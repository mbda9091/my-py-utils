from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from skill_sync.catalog import find_local_skill, list_local_skill_names
from skill_sync.errors import SkillSyncError
from skill_sync.installations import installation_status, read_managed_installations
from skill_sync.locking import mutation_lock
from skill_sync.repository import RepositoryConfig, require_current_binding

Query = Literal["head", "branch", "upstream", "status", "git-dir"]
_QUERIES: dict[Query, tuple[str, ...]] = {
    "head": ("rev-parse", "--verify", "HEAD"),
    "branch": ("symbolic-ref", "--quiet", "HEAD"),
    "upstream": ("rev-parse", "--symbolic-full-name", "@{upstream}"),
    "status": (
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=normal",
        "--ignore-submodules=none",
    ),
    "git-dir": ("rev-parse", "--absolute-git-dir"),
}
_QUERY_ERRORS: dict[Query, str] = {
    "head": "Update requires a committed HEAD; this repository may be empty.",
    "branch": "Update requires a current branch; detached HEAD is unsupported.",
    "upstream": "Configure an upstream for the current branch before updating.",
    "status": "Unable to inspect repository changes.",
    "git-dir": "Unable to locate the working repository's Git metadata.",
}


@dataclass(frozen=True, slots=True)
class UpdateResult:
    repository_path: Path
    before_head: str
    after_head: str
    warnings: tuple[str, ...]


def update_repository(config: RepositoryConfig) -> UpdateResult:
    with mutation_lock(config.state_home):
        require_current_binding(config)
        executable = shutil.which("git")
        if executable is None:
            msg = "git command not found."
            raise SkillSyncError(msg)
        repository = config.repository_path
        git_dir = Path(_inspect(executable, repository, "git-dir").strip())
        markers = (
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "rebase-merge",
            "rebase-apply",
            "sequencer",
        )
        if any((git_dir / marker).exists() for marker in markers):
            msg = "Finish the in-progress Git operation before updating."
            raise SkillSyncError(msg)
        _inspect(executable, repository, "branch")
        before = _inspect(executable, repository, "head").strip()
        _inspect(executable, repository, "upstream")
        if _inspect(executable, repository, "status"):
            msg = "Repository has local changes or untracked files; handle them before updating."
            raise SkillSyncError(msg)
        # Invalid local records must fail before any network or worktree modification.
        read_managed_installations(config.state_home)
        result = _pull(executable, repository)
        if result.returncode:
            try:
                current = _inspect(executable, repository, "head").strip()
            except SkillSyncError:
                current = "unavailable"
            detail = _safe_git_error(result.stderr or result.stdout)
            msg = (
                f"Git pull failed (exit {result.returncode}): {detail}\n"
                f"HEAD before: {before}; current HEAD: {current}. "
                "Git may have updated remote refs; inspect it manually."
            )
            raise SkillSyncError(msg)
        after = _inspect(executable, repository, "head").strip()
        return UpdateResult(repository, before, after, tuple(_collect_warnings(config)))


def _inspect(executable: str, repository: Path, query: Query) -> str:
    try:
        result = subprocess.run(  # noqa: S603
            [executable, "--no-optional-locks", *_QUERIES[query]],
            cwd=repository,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise SkillSyncError(_QUERY_ERRORS[query]) from exc
    if result.returncode:
        raise SkillSyncError(_QUERY_ERRORS[query])
    return result.stdout


def _pull(executable: str, repository: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603
            [
                executable,
                "pull",
                "--ff-only",
                "--no-rebase",
                "--no-autostash",
                "--no-recurse-submodules",
            ],
            cwd=repository,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        msg = f"Unable to start Git pull: {exc}"
        raise SkillSyncError(msg) from exc


def _safe_git_error(message: str) -> str:
    redacted = re.sub(r"(?:[a-zA-Z][\w+.-]*://|[\w.-]+@)[^\s'\"<>]+", "<remote>", message)
    return redacted.strip()[:2000] or "Git did not provide an error message."


def _collect_warnings(config: RepositoryConfig) -> list[str]:
    warnings: list[str] = []
    records = read_managed_installations(config.state_home)
    # Installed skills remain validation candidates even if their descriptor vanished.
    names = {
        record.skill
        for record in records.values()
        if record.source_path == config.repository_path / "skills" / record.skill
    }
    try:
        names.update(list_local_skill_names(config.repository_path))
    except (OSError, SkillSyncError) as exc:
        warnings.append(f"Git updated successfully, but the skill catalog is invalid: {exc}")
    for name in sorted(names):
        try:
            find_local_skill(config.repository_path, name)
        except (OSError, SkillSyncError) as exc:
            warnings.append(f"Invalid skill after Git update: {name}: {exc}")
    for record in records.values():
        if record.source_path != config.repository_path / "skills" / record.skill:
            continue
        try:
            status = installation_status(record, config.repository_path)
        except (OSError, RuntimeError) as exc:
            warnings.append(f"Cannot inspect installation {record.path}: {exc}")
            continue
        if status != "ok":
            warnings.append(
                f"Installation {status}: {record.path}; use uninstall/remove if appropriate."
            )
    return warnings
