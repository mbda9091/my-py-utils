from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, NoReturn, cast

from .hooks import (
    HookError,
    HookOptions,
    HookResult,
)
from .hooks import (
    run_post_create_hook as dispatch_post_create_hook,
)
from .hooks import (
    run_pre_cleanup_hook as dispatch_pre_cleanup_hook,
)

try:
    import yaml
except ModuleNotFoundError as exc:  # pragma: no cover - fallback guard
    dependency_error = "PyYAML is required to read and write .workspace/task.yaml"
    raise RuntimeError(dependency_error) from exc


TASK_PROMPT_VERSION = "workspace-agent/v1"
AGENT_MODES = ("usage", "development")
DEFAULT_UPSTREAM = "HEAD"
REPO_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_RULE_MESSAGE = (
    "slug must contain only lowercase ASCII letters, digits, and single hyphens; "
    "it must start and end with a letter or digit. Please retry."
)
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TASK_ROOT_PATTERN = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<slug>.+)$")


class WorkspaceError(RuntimeError):
    pass


def raise_workspace_error(message: str, *, cause: BaseException | None = None) -> NoReturn:
    if cause is None:
        raise WorkspaceError(message)
    raise WorkspaceError(message) from cause


def sanitize_segment(value: str) -> str:
    value = value.strip().replace(os.sep, "-").replace("/", "-")
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-._")
    return value or "workspace"


def validate_requirement_slug(slug: str) -> None:
    if not SLUG_PATTERN.fullmatch(slug):
        raise_workspace_error(f"invalid requirement slug: {slug!r}; {SLUG_RULE_MESSAGE}")


def current_date_string() -> str:
    return datetime.now(tz=UTC).astimezone().date().isoformat()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    else:
        return True


def git_run(
    args: list[str],
    cwd: Path,
    *,
    capture: bool = True,
    check: bool = True,
) -> str:
    try:
        result = subprocess.run(  # noqa: S603 - arguments are assembled from internal Git calls
            ["git", *args],  # noqa: S607 - git is the fixed executable
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment issue
        raise_workspace_error("git command not found", cause=exc)
    else:
        if check and result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()
            raise_workspace_error(message or f"git {' '.join(args)} failed")
    return (result.stdout or "").strip() if capture else ""


def git_root(path: Path) -> Path:
    return Path(git_run(["rev-parse", "--show-toplevel"], path)).resolve()


def git_current_ref(repo_root: Path) -> str:
    branch = git_run(["branch", "--show-current"], repo_root)
    if branch:
        return branch
    return git_run(["rev-parse", "HEAD"], repo_root)


def git_local_branch_exists(repo_root: Path, branch: str) -> bool:
    result = subprocess.run(  # noqa: S603 - branch is validated before this internal call
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],  # noqa: S607 - git is the fixed executable
        cwd=str(repo_root),
        check=False,
    )
    return result.returncode == 0


def git_info_exclude_path(worktree_path: Path) -> Path:
    output = git_run(["rev-parse", "--git-path", "info/exclude"], worktree_path)
    return Path(output).resolve()


def git_status_porcelain(repo_root: Path) -> str:
    return git_run(["status", "--porcelain"], repo_root)


def git_worktree_list(repo_root: Path) -> list[dict[str, str]]:
    output = git_run(["worktree", "list", "--porcelain"], repo_root)
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        if " " in line:
            key, value = line.split(" ", 1)
        else:
            key, value = line, ""
        current[key] = value
    if current:
        entries.append(current)
    return entries


def git_worktree_entry(repo_root: Path, target: Path) -> dict[str, str] | None:
    target = target.resolve()
    for entry in git_worktree_list(repo_root):
        worktree = Path(entry.get("worktree", "")).expanduser().resolve()
        if worktree == target:
            return entry
    return None


def git_branch_from_entry(entry: dict[str, str]) -> str:
    branch_ref = entry.get("branch", "")
    prefix = "refs/heads/"
    if branch_ref.startswith(prefix):
        return branch_ref[len(prefix) :]
    return branch_ref


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise_workspace_error(f"missing workspace config: {path}", cause=exc)
    except json.JSONDecodeError as exc:
        raise_workspace_error(f"invalid JSON in {path}: {exc}", cause=exc)
    if not isinstance(data, dict):
        raise_workspace_error(f"workspace config must be a JSON object: {path}")
    data = cast("dict[str, Any]", data)
    repositories = data.get("repositories")
    if repositories is None:
        data["repositories"] = {}
    elif not isinstance(repositories, dict):
        raise_workspace_error("workspace.json repositories must be a JSON object")
    else:
        data["repositories"] = cast("dict[str, Any]", repositories)
    return data


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise_workspace_error(f"missing task manifest: {path}", cause=exc)
    except yaml.YAMLError as exc:
        raise_workspace_error(f"invalid YAML in {path}: {exc}", cause=exc)
    if data is None:
        raise_workspace_error(f"empty task manifest: {path}")
    if not isinstance(data, dict):
        raise_workspace_error(f"task manifest must be a YAML mapping: {path}")
    return cast("dict[str, Any]", data)


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True).rstrip() + "\n",
        encoding="utf-8",
    )


def task_root_name(path: Path) -> str:
    return path.resolve().name


def task_root_slug_from_name(name: str) -> str:
    match = TASK_ROOT_PATTERN.match(name)
    if not match:
        raise_workspace_error(f"invalid task root name: {name}")
    date_part = match.group("date")
    if not DATE_PATTERN.fullmatch(date_part):
        raise_workspace_error(f"invalid task root date prefix: {name}")
    try:
        date.fromisoformat(date_part)
    except ValueError as exc:
        raise_workspace_error(f"invalid task root date prefix: {name}", cause=exc)
    return match.group("slug")


def workspace_root_from_cwd() -> Path:
    return Path.cwd().resolve()


def default_config_path(root: Path) -> Path:
    return root / "workspace.json"


def default_projects_root(root: Path) -> Path:
    return root / "projects"


def default_worktrees_root(root: Path) -> Path:
    return root / "worktrees"


def default_docs_root(root: Path) -> Path:
    return root / "doc"


def default_control_root(root: Path) -> Path:
    return root / ".workspace"


def ensure_workspace_gitignore(root: Path) -> bool:
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    required = ("projects/", "worktrees/")
    changed = False
    for pattern in required:
        if pattern not in existing:
            existing.append(pattern)
            changed = True
    if changed or not path.exists():
        path.write_text("\n".join(existing).rstrip() + "\n", encoding="utf-8")
    return changed or not path.exists()


def ensure_workspace_git(root: Path) -> bool:
    git_path = root / ".git"
    if git_path.exists():
        repository_root = Path(git_run(["rev-parse", "--show-toplevel"], root)).resolve()
        if repository_root != root:
            raise_workspace_error(
                f"workspace root must be an independent Git repository: {repository_root}"
            )
        return False
    git_run(["init"], root)
    return True


def run_post_create_hook(
    source_repo: Path,
    worktree: Path,
    options: HookOptions,
) -> HookResult:
    try:
        return dispatch_post_create_hook(source_repo, worktree, options)
    except HookError as exc:
        raise_workspace_error(str(exc), cause=exc)


def run_pre_cleanup_hook(
    worktree: Path,
    options: HookOptions,
) -> HookResult:
    try:
        return dispatch_pre_cleanup_hook(worktree, options)
    except HookError as exc:
        raise_workspace_error(str(exc), cause=exc)


@dataclass
class RepositoryBinding:
    repo_id: str
    config: dict[str, Any]
    entry_path: Path
    repo_root: Path


@dataclass
class WorktreeBinding:
    project_id: str
    path: Path
    branch: str
    prompt_version: str = TASK_PROMPT_VERSION


class WorkspaceController:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or workspace_root_from_cwd()).resolve()

    @property
    def config_path(self) -> Path:
        return default_config_path(self.root)

    @property
    def projects_root(self) -> Path:
        return default_projects_root(self.root)

    @property
    def worktrees_root(self) -> Path:
        return default_worktrees_root(self.root)

    @property
    def docs_root(self) -> Path:
        return default_docs_root(self.root)

    @property
    def control_root(self) -> Path:
        return default_control_root(self.root)

    def agent_template_path(self, mode: str) -> Path:
        workspace_template = self.root / "templates" / "agents" / f"{mode}.md"
        if workspace_template.exists():
            return workspace_template
        return Path(__file__).parent / "templates" / "agents" / f"{mode}.md"

    def ensure_root_agents(self, mode: str, *, overwrite: bool) -> bool:
        template_path = self.agent_template_path(mode)
        if not template_path.exists():
            raise_workspace_error(f"missing agent template: {template_path}")
        target_path = self.root / "AGENTS.md"
        if target_path.exists() and not overwrite:
            return False
        target_path.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")
        return True

    def ensure_init(self, *, create_config: bool = True) -> dict[str, Any]:
        created: list[str] = []
        git_initialized = ensure_workspace_git(self.root)
        if git_initialized:
            created.append(".git")
        for path in (self.projects_root, self.worktrees_root, self.docs_root, self.control_root):
            if not path.exists():
                ensure_dir(path)
                created.append(str(path.relative_to(self.root)))
        gitignore_path = self.root / ".gitignore"
        gitignore_existed = gitignore_path.exists()
        gitignore_changed = ensure_workspace_gitignore(self.root)
        if gitignore_changed:
            created.append(".gitignore" if not gitignore_existed else "updated .gitignore")
        if create_config and not self.config_path.exists():
            save_json(self.config_path, {"repositories": {}})
            created.append(self.config_path.name)
        if self.ensure_root_agents("usage", overwrite=False):
            created.append("AGENTS.md")
        return {
            "workspace_root": str(self.root),
            "created": created,
            "config_path": str(self.config_path),
            "git_initialized": git_initialized,
            "gitignore_path": str(self.root / ".gitignore"),
        }

    def load_config(self) -> dict[str, Any]:
        config = load_json(self.config_path)
        repositories_value: Any = config.get("repositories", {})
        if not isinstance(repositories_value, dict):
            raise_workspace_error("workspace.json repositories must be an object")
        return config

    def save_config(self, config: dict[str, Any]) -> None:
        save_json(self.config_path, config)

    def repositories(self, config: dict[str, Any] | None = None) -> list[RepositoryBinding]:
        config = config or self.load_config()
        repositories = cast("dict[str, Any]", config.get("repositories", {}))
        result: list[RepositoryBinding] = []
        for repo_id, repo_config in repositories.items():
            result.append(self.bind_repository(repo_id, repo_config))
        return result

    def bind_repository(
        self, repo_id: str, repo_config: dict[str, Any] | None = None
    ) -> RepositoryBinding:
        if not REPO_ID_PATTERN.fullmatch(repo_id):
            raise_workspace_error(f"invalid repository id: {repo_id}")
        entry_path = self.projects_root / repo_id
        if repo_config is None:
            config = self.load_config()
            repositories_value = config.get("repositories", {})
            repositories = cast("dict[str, Any]", repositories_value)
            repo_config = repositories.get(repo_id)
        if repo_config is None:
            raise_workspace_error(f"repository not found in workspace.json: {repo_id}")
        repo_config_value: Any = repo_config
        if not isinstance(repo_config_value, dict):
            raise_workspace_error(f"workspace.json entry for {repo_id} must be an object")
        repo_config = cast("dict[str, Any]", repo_config_value)
        if not entry_path.exists():
            raise_workspace_error(f"missing project entry path: {entry_path}")
        if not entry_path.is_dir():
            raise_workspace_error(
                f"project entry is not a directory or symlink to one: {entry_path}"
            )
        try:
            repo_root = git_root(entry_path.resolve())
        except WorkspaceError as exc:
            raise_workspace_error(f"project entry is not a git repository: {entry_path}", cause=exc)
        return RepositoryBinding(
            repo_id=repo_id, config=repo_config, entry_path=entry_path, repo_root=repo_root
        )

    def validate_config_bindings(self, config: dict[str, Any]) -> None:
        repositories_value: Any = config.get("repositories", {})
        if not isinstance(repositories_value, dict):
            raise_workspace_error("workspace.json repositories must be an object")
        repositories = cast("dict[str, Any]", repositories_value)
        for repo_id, repo_config in repositories.items():
            self.bind_repository(repo_id, repo_config)

    def source_repo_clean(self, repo_root: Path) -> bool:
        return not git_status_porcelain(repo_root)

    def detect_git_username(self, override: str | None = None) -> str:
        if override:
            return sanitize_segment(override)
        global_name = ""
        try:
            global_name = git_run(["config", "--global", "--get", "user.name"], self.root)
        except WorkspaceError:
            global_name = ""
        if not global_name:
            global_name = getpass.getuser()
        return sanitize_segment(global_name)

    def task_root_path(self, task_date: str, slug: str) -> Path:
        if not DATE_PATTERN.fullmatch(task_date):
            raise_workspace_error(f"invalid task date: {task_date}")
        try:
            date.fromisoformat(task_date)
        except ValueError as exc:
            raise_workspace_error(f"invalid task date: {task_date}", cause=exc)
        validate_requirement_slug(slug)
        return self.worktrees_root / f"{task_date}-{slug}"

    def task_manifest_path(self, task_root: Path) -> Path:
        return task_root / ".workspace" / "task.yaml"

    def load_task_manifest(self, task_root: Path) -> dict[str, Any]:
        return load_yaml(self.task_manifest_path(task_root))

    def save_task_manifest(self, task_root: Path, data: dict[str, Any]) -> None:
        ensure_dir(task_root / ".workspace")
        save_yaml(self.task_manifest_path(task_root), data)

    def task_slug(self, task_root: Path) -> str:
        return task_root_slug_from_name(task_root.name)

    def active_task_roots(self) -> list[Path]:
        if not self.worktrees_root.exists():
            return []
        return [
            path
            for path in sorted(self.worktrees_root.iterdir())
            if path.is_dir() and self.task_manifest_path(path).exists()
        ]

    def archived_task_roots(self) -> list[Path]:
        if not self.docs_root.exists():
            return []
        return [
            path
            for path in sorted(self.docs_root.iterdir())
            if path.is_dir() and TASK_ROOT_PATTERN.match(path.name)
        ]

    def find_task_root_by_slug(self, slug: str) -> Path | None:
        for path in self.active_task_roots():
            if self.task_slug(path) == slug:
                return path
        return None

    def slug_collisions(self, slug: str) -> list[str]:
        collisions: list[str] = []
        for path in self.active_task_roots() + self.archived_task_roots():
            try:
                if self.task_slug(path) == slug:
                    collisions.append(str(path))
            except WorkspaceError:
                continue
        return collisions

    def current_source_ref(self, repo_root: Path, override: str | None = None) -> str:
        upstream = override or DEFAULT_UPSTREAM
        if upstream.startswith("origin/"):
            upstream = upstream.removeprefix("origin/")
        try:
            git_run(["fetch", "origin", upstream], repo_root, capture=True)
        except WorkspaceError as exc:
            raise_workspace_error(
                f"unable to fetch upstream {upstream!r} from origin for {repo_root}: {exc}",
                cause=exc,
            )
        return "FETCH_HEAD"

    def existing_worktree(self, repo_root: Path, target_path: Path) -> dict[str, str] | None:
        return git_worktree_entry(repo_root, target_path)

    def branch_name(self, username: str, slug: str) -> str:
        return f"merchant/{sanitize_segment(username)}/{slug}"

    # Rendering needs the complete task/repository context to keep generated guides accurate.
    def render_agents(  # noqa: PLR0913
        self,
        *,
        task_root: Path,
        manifest: dict[str, Any],
        project_id: str,
        worktree_path: Path,
        repo_root: Path,
        branch: str,
    ) -> str:
        scope = ", ".join(manifest.get("scope", [])) or "(empty)"
        requirement = manifest.get("requirement", {})
        lines = [
            "# AGENTS.md",
            "",
            "This worktree belongs to requirement "
            f"`{requirement.get('title', '')}` (`{requirement.get('slug', '')}`).",
            f"- task root: `{task_root.relative_to(self.root)}`",
            f"- project id: `{project_id}`",
            f"- source repo: `{repo_root}`",
            f"- worktree path: `{worktree_path}`",
            f"- branch: `{branch}`",
            f"- source PRD: `{requirement.get('source_prd_url') or ''}`",
            f"- scope: {scope}",
            f"- prompt version: `{manifest.get('prompt_version', TASK_PROMPT_VERSION)}`",
            "",
            "Keep changes confined to this worktree.",
            "Do not edit the workspace root, other project checkouts, or archived docs.",
            "Use CONTEXT.md and docs/execution-runbook.md as the control-plane reference.",
            "Keep durable requirement artifacts in the task root outside child code worktrees.",
            "",
        ]
        return "\n".join(lines)

    def write_agents(
        self,
        *,
        task_root: Path,
        manifest: dict[str, Any],
        worktree: WorktreeBinding,
        repo_root: Path,
    ) -> None:
        agents_path = worktree.path / ".workspace" / "AGENTS.md"
        ensure_dir(agents_path.parent)
        agents_path.write_text(
            self.render_agents(
                task_root=task_root,
                manifest=manifest,
                project_id=worktree.project_id,
                worktree_path=worktree.path,
                repo_root=repo_root,
                branch=worktree.branch,
            ),
            encoding="utf-8",
        )

    def ensure_worktree_excludes(self, worktree_path: Path) -> None:
        exclude_path = git_info_exclude_path(worktree_path)
        ensure_dir(exclude_path.parent)
        existing = []
        if exclude_path.exists():
            existing = exclude_path.read_text(encoding="utf-8").splitlines()
        patterns = [".workspace/"]
        changed = False
        for pattern in patterns:
            if pattern not in existing:
                existing.append(pattern)
                changed = True
        if changed or not exclude_path.exists():
            exclude_path.write_text("\n".join(existing).rstrip() + "\n", encoding="utf-8")

    def ensure_agents_local_link(self, repo_root: Path, worktree_path: Path) -> None:
        source = repo_root / "AGENTS.local.md"
        if not source.is_file():
            return
        target = worktree_path / "AGENTS.local.md"
        source_resolved = source.resolve()
        if target.is_symlink():
            if target.resolve(strict=False) == source_resolved:
                return
            target.unlink()
        elif target.exists():
            if not target.is_file() or target.read_bytes() != source.read_bytes():
                raise_workspace_error(
                    f"worktree already contains a different AGENTS.local.md: {target}"
                )
            target.unlink()
        target.symlink_to(source_resolved)

    # Worktree creation is an integration boundary, so its parameters stay explicit.
    def ensure_worktree(  # noqa: PLR0913
        self,
        *,
        binding: RepositoryBinding,
        task_root: Path,
        project_id: str,
        slug: str,
        branch: str,
        source_ref: str | None = None,
    ) -> tuple[WorktreeBinding, bool]:
        target_path = task_root / f"{project_id}-{slug}"
        existing = self.existing_worktree(binding.repo_root, target_path)
        if existing is not None:
            existing_branch = git_branch_from_entry(existing)
            if existing_branch and existing_branch != branch:
                raise_workspace_error(
                    f"existing worktree branch mismatch for {target_path}: "
                    f"{existing_branch} != {branch}"
                )
            self.ensure_worktree_excludes(target_path)
            self.ensure_agents_local_link(binding.repo_root, target_path)
            return WorktreeBinding(project_id=project_id, path=target_path, branch=branch), True
        ensure_dir(task_root)
        if git_local_branch_exists(binding.repo_root, branch):
            git_run(["worktree", "add", str(target_path), branch], binding.repo_root)
        else:
            if not source_ref:
                raise_workspace_error(
                    f"missing upstream ref for new worktree {target_path}; provide one explicitly"
                )
            git_run(
                ["worktree", "add", "-b", branch, str(target_path), source_ref], binding.repo_root
            )
        self.ensure_worktree_excludes(target_path)
        self.ensure_agents_local_link(binding.repo_root, target_path)
        return WorktreeBinding(project_id=project_id, path=target_path, branch=branch), False

    def remove_worktree(
        self, binding: RepositoryBinding, worktree_path: Path, *, force: bool = False
    ) -> None:
        cmd = ["worktree", "remove"]
        if force:
            cmd.append("--force")
        cmd.append(str(worktree_path))
        git_run(cmd, binding.repo_root, capture=True)

    def prune_worktrees(self, binding: RepositoryBinding) -> None:
        git_run(["worktree", "prune"], binding.repo_root)

    def cleanup_source_repo(self, binding: RepositoryBinding, worktree_path: Path) -> None:
        entry = self.existing_worktree(binding.repo_root, worktree_path)
        if entry is None:
            raise_workspace_error(f"worktree not registered for cleanup: {worktree_path}")

    def collect_manifest_worktrees(
        self,
        manifest: dict[str, Any],
    ) -> list[dict[str, Any]]:
        worktrees = manifest.get("worktrees", [])
        if not isinstance(worktrees, list):
            raise_workspace_error("task manifest worktrees must be a list")
        return cast("list[dict[str, Any]]", worktrees)

    # Keep manifest fields explicit so callers cannot accidentally omit required metadata.
    def build_manifest(  # noqa: PLR0913
        self,
        *,
        title: str,
        slug: str,
        source_prd_url: str | None,
        scope: list[str],
        worktrees: list[dict[str, Any]],
        prompt_version: str = TASK_PROMPT_VERSION,
    ) -> dict[str, Any]:
        return {
            "requirement": {
                "title": title,
                "slug": slug,
                "source_prd_url": source_prd_url,
            },
            "prompt_version": prompt_version,
            "scope": scope,
            "worktrees": worktrees,
        }

    def manifest_to_worktree_binding(self, entry: dict[str, Any]) -> WorktreeBinding:
        project_id = entry.get("project_id")
        path = entry.get("path")
        branch = entry.get("branch")
        if (
            not isinstance(project_id, str)
            or not isinstance(path, str)
            or not isinstance(branch, str)
        ):
            raise_workspace_error("invalid worktree entry in task manifest")
        return WorktreeBinding(
            project_id=project_id, path=(self.root / path).resolve(), branch=branch
        )

    def relative_manifest_path(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.root))

    def resolve_task_root(self, task_root_arg: str | Path) -> Path:
        task_root = Path(task_root_arg).expanduser()
        if not task_root.is_absolute():
            task_root = (self.root / task_root).resolve()
        else:
            task_root = task_root.resolve()
        if not path_is_within(task_root, self.worktrees_root.resolve()):
            raise_workspace_error(f"task root must live under {self.worktrees_root}: {task_root}")
        return task_root

    # This method coordinates validation, Git mutations, rollback, and manifest persistence.
    def create_requirement(  # noqa: C901, PLR0912, PLR0913, PLR0915
        self,
        *,
        title: str,
        source_prd_url: str | None,
        project_ids: list[str],
        slug: str,
        task_date: str | None = None,
        git_username: str | None = None,
        upstream: str | None = None,
        reuse_existing: bool = False,
        hook_options: HookOptions | None = None,
    ) -> dict[str, Any]:
        hook_options = hook_options or HookOptions()
        if not title.strip():
            raise_workspace_error("requirement title is required")
        if source_prd_url is not None and not source_prd_url.strip():
            raise_workspace_error("source PRD URL cannot be empty when provided")
        if not project_ids:
            raise_workspace_error("at least one project id is required")
        validate_requirement_slug(slug)
        collisions = self.slug_collisions(slug)
        if collisions:
            active_root = self.find_task_root_by_slug(slug)
            if not (reuse_existing and active_root is not None):
                raise_workspace_error(
                    f"slug already exists: {slug}. choose another slug or rerun with "
                    f"--reuse-existing only when the active task is the same requirement. "
                    f"matches: {', '.join(collisions)}"
                )
            task_root = active_root
            manifest = self.load_task_manifest(task_root)
            requirement = manifest.get("requirement", {})
            if requirement.get("source_prd_url") != source_prd_url:
                raise_workspace_error("existing task root does not match the provided PRD URL")
            if requirement.get("title") != title:
                raise_workspace_error(
                    "existing task root does not match the provided requirement title"
                )
            existing_scope = manifest.get("scope", [])
            if set(existing_scope) != set(project_ids):
                raise_workspace_error(
                    "existing task root scope differs from the provided project ids"
                )
        else:
            task_date = task_date or current_date_string()
            task_root = self.task_root_path(task_date, slug)
            if task_root.exists():
                raise_workspace_error(f"task root already exists: {task_root}")
            ensure_dir(task_root / ".workspace")
            manifest = self.build_manifest(
                title=title,
                slug=slug,
                source_prd_url=source_prd_url,
                scope=project_ids,
                worktrees=[],
            )
        username = self.detect_git_username(git_username)
        branch = self.branch_name(username, slug)
        bindings: list[RepositoryBinding] = []
        for project_id in project_ids:
            binding = self.bind_repository(project_id)
            bindings.append(binding)
        worktrees: list[dict[str, Any]] = []
        hook_events: list[dict[str, Any]] = []
        created: list[tuple[RepositoryBinding, Path]] = []
        branches_created: set[tuple[Path, str]] = set()
        try:
            for binding in bindings:
                source_ref = self.current_source_ref(binding.repo_root, upstream)
                branch_preexisting = git_local_branch_exists(binding.repo_root, branch)
                worktree_binding, reused = self.ensure_worktree(
                    binding=binding,
                    task_root=task_root,
                    project_id=binding.repo_id,
                    slug=slug,
                    branch=branch,
                    source_ref=source_ref,
                )
                if not reused:
                    created.append((binding, worktree_binding.path))
                    if not branch_preexisting:
                        branches_created.add((binding.repo_root, branch))
                    hook_result = run_post_create_hook(
                        binding.repo_root,
                        worktree_binding.path,
                        hook_options,
                    )
                    hook_events.append(
                        {
                            "project_id": binding.repo_id,
                            "project": hook_result.project,
                            "messages": hook_result.messages,
                        }
                    )
                worktrees.append(
                    {
                        "project_id": worktree_binding.project_id,
                        "path": self.relative_manifest_path(worktree_binding.path),
                        "branch": worktree_binding.branch,
                    }
                )
                self.write_agents(
                    task_root=task_root,
                    manifest={
                        "requirement": {
                            "title": title,
                            "slug": slug,
                            "source_prd_url": source_prd_url,
                        },
                        "prompt_version": TASK_PROMPT_VERSION,
                        "scope": project_ids,
                        "worktrees": worktrees,
                    },
                    worktree=worktree_binding,
                    repo_root=binding.repo_root,
                )
        except Exception:
            self._rollback_created_worktrees(created, branches_created)
            if not collisions and task_root.exists() and not any(task_root.iterdir()):
                shutil.rmtree(task_root, ignore_errors=True)
            raise
        manifest = self.build_manifest(
            title=title,
            slug=slug,
            source_prd_url=source_prd_url,
            scope=project_ids,
            worktrees=worktrees,
        )
        self.save_task_manifest(task_root, manifest)
        return {
            "workspace_root": str(self.root),
            "task_root": str(task_root),
            "task_root_name": task_root.name,
            "requirement": manifest["requirement"],
            "scope": project_ids,
            "branch": branch,
            "worktrees": worktrees,
            "hooks": hook_events,
            "prompt_version": TASK_PROMPT_VERSION,
        }

    def _rollback_created_worktrees(
        self,
        created: list[tuple[RepositoryBinding, Path]],
        branches_created: set[tuple[Path, str]],
    ) -> None:
        for binding, path in reversed(created):
            with suppress(Exception):
                if path.exists():
                    self.remove_worktree(binding, path, force=True)
        for repo_root, branch in branches_created:
            with suppress(Exception):
                git_run(["branch", "-D", branch], repo_root, capture=True)

    # Scope changes are transactional across multiple repositories and need explicit rollback.
    def extend_requirement(  # noqa: C901, PLR0912, PLR0913, PLR0915
        self,
        *,
        task_root: Path,
        add_ids: list[str],
        remove_ids: list[str],
        git_username: str | None = None,
        upstream: str | None = None,
        hook_options: HookOptions | None = None,
    ) -> dict[str, Any]:
        hook_options = hook_options or HookOptions()
        manifest = self.load_task_manifest(task_root)
        requirement = manifest.get("requirement", {})
        slug = requirement.get("slug")
        title = requirement.get("title")
        source_prd_url = requirement.get("source_prd_url")
        scope = list(manifest.get("scope", []))
        if not isinstance(slug, str) or not slug:
            raise_workspace_error("task manifest is missing requirement slug")
        validate_requirement_slug(slug)
        if not isinstance(title, str) or not title:
            raise_workspace_error("task manifest is missing requirement title")
        if source_prd_url is not None and (
            not isinstance(source_prd_url, str) or not source_prd_url
        ):
            raise_workspace_error("task manifest contains an invalid source PRD URL")
        if not add_ids and not remove_ids:
            raise_workspace_error("extend-requirement needs at least one add or remove project id")
        current_ids = set(scope)
        add_set = [item for item in add_ids if item not in current_ids]
        remove_set = [item for item in remove_ids if item in current_ids]
        for project_id in add_set + remove_set:
            if not REPO_ID_PATTERN.fullmatch(project_id):
                raise_workspace_error(f"invalid repository id: {project_id}")
        if not add_set and not remove_set:
            raise_workspace_error("scope delta is empty after validation")
        username = self.detect_git_username(git_username)
        branch = self.branch_name(username, slug)
        current_worktrees: dict[str, dict[str, Any]] = {
            item["project_id"]: item
            for item in cast("list[dict[str, Any]]", manifest.get("worktrees", []))
            if isinstance(item.get("project_id"), str)
        }
        bindings: dict[str, RepositoryBinding] = {}
        removed_operations: list[tuple[RepositoryBinding, Path, str]] = []
        for project_id in remove_set:
            entry = current_worktrees.get(project_id)
            if entry is None:
                raise_workspace_error(
                    f"manifest is missing worktree entry for removed project {project_id}"
                )
            binding = bindings.get(project_id) or self.bind_repository(project_id)
            relative_path = entry.get("path")
            if not isinstance(relative_path, str):
                raise_workspace_error(f"invalid worktree path for removed project {project_id}")
            worktree_path = Path(relative_path)
            if not worktree_path.is_absolute():
                worktree_path = (self.root / worktree_path).resolve()
            if git_status_porcelain(worktree_path):
                raise_workspace_error(
                    f"worktree is dirty and cannot be removed safely: {worktree_path}"
                )
            entry_branch = entry.get("branch", branch)
            if not isinstance(entry_branch, str):
                raise_workspace_error(f"invalid worktree branch for removed project {project_id}")
            removed_operations.append((binding, worktree_path, entry_branch))
        added_operations: list[tuple[str, RepositoryBinding, str, bool]] = []
        for project_id in add_set:
            binding = bindings.get(project_id) or self.bind_repository(project_id)
            source_ref = self.current_source_ref(binding.repo_root, upstream)
            branch_preexisting = git_local_branch_exists(binding.repo_root, branch)
            added_operations.append((project_id, binding, source_ref, branch_preexisting))
        removed_snapshots: list[dict[str, Any]] = []
        added_snapshots: list[tuple[RepositoryBinding, Path]] = []
        hook_events: list[dict[str, Any]] = []
        branches_created: set[tuple[Path, str]] = set()
        # Remove first so branch naming remains stable, then add new scope items.
        try:
            for binding, worktree_path, entry_branch in removed_operations:
                removed_snapshots.append(
                    {
                        "binding": binding,
                        "path": worktree_path,
                        "branch": entry_branch,
                    }
                )
                self.remove_worktree(binding, worktree_path, force=False)
            for project_id, binding, source_ref, branch_preexisting in added_operations:
                worktree_binding, reused = self.ensure_worktree(
                    binding=binding,
                    task_root=task_root,
                    project_id=project_id,
                    slug=slug,
                    branch=branch,
                    source_ref=source_ref,
                )
                if not reused:
                    added_snapshots.append((binding, worktree_binding.path))
                    if not branch_preexisting:
                        branches_created.add((binding.repo_root, branch))
                    hook_result = run_post_create_hook(
                        binding.repo_root,
                        worktree_binding.path,
                        hook_options,
                    )
                    hook_events.append(
                        {
                            "project_id": project_id,
                            "project": hook_result.project,
                            "messages": hook_result.messages,
                        }
                    )
                current_worktrees[project_id] = {
                    "project_id": project_id,
                    "path": self.relative_manifest_path(worktree_binding.path),
                    "branch": branch,
                }
        except Exception:
            self._restore_removed_worktrees(removed_snapshots, hook_options)
            self._rollback_created_worktrees(added_snapshots, branches_created)
            raise
        scope = [project_id for project_id in scope if project_id not in remove_set] + add_set
        worktrees = [current_worktrees[project_id] for project_id in scope]
        manifest["scope"] = scope
        manifest["worktrees"] = worktrees
        self.save_task_manifest(task_root, manifest)
        for entry in worktrees:
            project_id = entry["project_id"]
            binding = bindings.get(project_id) or self.bind_repository(project_id)
            worktree_path = Path(entry["path"])
            if not worktree_path.is_absolute():
                worktree_path = (self.root / worktree_path).resolve()
            self.ensure_worktree_excludes(worktree_path)
            self.ensure_agents_local_link(binding.repo_root, worktree_path)
            self.write_agents(
                task_root=task_root,
                manifest=manifest,
                worktree=WorktreeBinding(
                    project_id=project_id,
                    path=worktree_path,
                    branch=entry["branch"],
                ),
                repo_root=binding.repo_root,
            )
        return {
            "workspace_root": str(self.root),
            "task_root": str(task_root),
            "requirement": requirement,
            "scope": scope,
            "branch": branch,
            "worktrees": worktrees,
            "hooks": hook_events,
        }

    def _restore_removed_worktrees(
        self,
        removed: list[dict[str, Any]],
        hook_options: HookOptions,
    ) -> None:
        for snapshot in removed:
            binding = snapshot["binding"]
            path = snapshot["path"]
            branch = snapshot["branch"]
            if path.exists():
                continue
            source_ref = git_current_ref(binding.repo_root)
            with suppress(Exception):
                if git_local_branch_exists(binding.repo_root, branch):
                    git_run(["worktree", "add", str(path), branch], binding.repo_root)
                else:
                    git_run(
                        ["worktree", "add", "-b", branch, str(path), source_ref], binding.repo_root
                    )
                self.ensure_worktree_excludes(path)
                self.ensure_agents_local_link(binding.repo_root, path)
                run_post_create_hook(binding.repo_root, path, hook_options)

    def archive_requirement(  # noqa: C901
        self,
        *,
        task_root: Path,
        force: bool = False,
        hook_options: HookOptions | None = None,
    ) -> dict[str, Any]:
        hook_options = hook_options or HookOptions()
        manifest = self.load_task_manifest(task_root)
        worktrees = self.collect_manifest_worktrees(manifest)
        archive_root = self.docs_root / task_root.name
        ensure_dir(self.docs_root)
        if not task_root.is_dir():
            raise_workspace_error(f"task root is not a directory: {task_root}")
        if archive_root.exists():
            raise_workspace_error(f"archive target already exists: {archive_root}")
        if not path_is_within(archive_root, self.docs_root.resolve()):
            raise_workspace_error(
                f"archive target must live under {self.docs_root}: {archive_root}"
            )

        operations: list[tuple[str, RepositoryBinding, Path]] = []
        for entry in worktrees:
            project_id = entry.get("project_id")
            relative_path = entry.get("path")
            if not isinstance(project_id, str) or not isinstance(relative_path, str):
                raise_workspace_error("invalid worktree entry in manifest")
            worktree_path = (self.root / relative_path).resolve()
            if not path_is_within(worktree_path, task_root.resolve()) or worktree_path == task_root:
                raise_workspace_error(f"worktree path must live under task root: {worktree_path}")
            binding = self.bind_repository(project_id)
            if self.existing_worktree(binding.repo_root, worktree_path) is None:
                raise_workspace_error(f"worktree is not registered for archive: {worktree_path}")
            if not worktree_path.exists():
                raise_workspace_error(f"worktree is missing: {worktree_path}")
            if not force and git_status_porcelain(worktree_path):
                raise_workspace_error(
                    f"worktree is dirty; rerun with --force to remove it: {worktree_path}"
                )
            operations.append((project_id, binding, worktree_path))

        removed: list[str] = []
        hook_events: list[dict[str, Any]] = []
        for project_id, binding, worktree_path in operations:
            hook_result = run_pre_cleanup_hook(worktree_path, hook_options)
            hook_events.append(
                {
                    "project_id": project_id,
                    "project": hook_result.project,
                    "messages": hook_result.messages,
                }
            )
            self.remove_worktree(binding, worktree_path, force=force)
            self.prune_worktrees(binding)
            removed.append(str(worktree_path))

        shutil.move(str(task_root), str(archive_root))
        return {
            "workspace_root": str(self.root),
            "task_root": str(task_root),
            "archive_root": str(archive_root),
            "moved": {"source": str(task_root), "target": str(archive_root)},
            "removed": removed,
            "hooks": hook_events,
        }

    def cleanup_requirement(
        self,
        *,
        task_root: Path,
        force: bool = False,
        hook_options: HookOptions | None = None,
    ) -> dict[str, Any]:
        hook_options = hook_options or HookOptions()
        manifest = self.load_task_manifest(task_root)
        worktrees = self.collect_manifest_worktrees(manifest)
        removed: list[str] = []
        hook_events: list[dict[str, Any]] = []
        for entry in worktrees:
            project_id = entry.get("project_id")
            relative_path = entry.get("path")
            if not isinstance(project_id, str) or not isinstance(relative_path, str):
                raise_workspace_error("invalid worktree entry in manifest")
            binding = self.bind_repository(project_id)
            worktree_path = (self.root / relative_path).resolve()
            if not worktree_path.exists():
                continue
            if not force and git_status_porcelain(worktree_path):
                message = f"worktree is dirty; rerun with --force to remove it: {worktree_path}"
                raise_workspace_error(message)
            hook_result = run_pre_cleanup_hook(worktree_path, hook_options)
            hook_events.append(
                {
                    "project_id": project_id,
                    "project": hook_result.project,
                    "messages": hook_result.messages,
                }
            )
            self.remove_worktree(binding, worktree_path, force=force)
            removed.append(str(worktree_path))
            self.prune_worktrees(binding)
        if task_root.exists():
            shutil.rmtree(task_root)
        return {
            "workspace_root": str(self.root),
            "task_root": str(task_root),
            "removed": removed,
            "hooks": hook_events,
        }

    def status_report(self) -> dict[str, Any]:
        config: dict[str, Any] = (
            load_json(self.config_path) if self.config_path.exists() else {"repositories": {}}
        )
        repositories: list[dict[str, Any]] = []
        configured_repositories = cast("dict[str, Any]", config.get("repositories", {}))
        for repo_id, repo_config in configured_repositories.items():
            if not isinstance(repo_config, dict):
                repositories.append(
                    {
                        "repo_id": repo_id,
                        "path": str(self.projects_root / repo_id),
                        "exists": False,
                        "error": f"workspace.json entry for {repo_id} must be an object",
                    }
                )
                continue
            repo_config = cast("dict[str, Any]", repo_config)
            entry_path = self.projects_root / repo_id
            repo_status: dict[str, Any] = {
                "repo_id": repo_id,
                "path": str(entry_path),
                "exists": entry_path.exists(),
                "kind": repo_config.get("kind"),
                "description": repo_config.get("description"),
            }
            if entry_path.exists() and entry_path.is_dir():
                try:
                    repo_root = git_root(entry_path.resolve())
                    repo_status["repo_root"] = str(repo_root)
                    repo_status["branch"] = git_current_ref(repo_root)
                    repo_status["dirty"] = bool(git_status_porcelain(repo_root))
                except WorkspaceError as exc:
                    repo_status["error"] = str(exc)
            else:
                repo_status["error"] = "missing project entry"
            repositories.append(repo_status)
        tasks: list[dict[str, Any]] = []
        for task_root in self.active_task_roots():
            manifest = self.load_task_manifest(task_root)
            tasks.append(
                {
                    "task_root": str(task_root),
                    "task_root_name": task_root.name,
                    "requirement": manifest.get("requirement", {}),
                    "scope": manifest.get("scope", []),
                    "worktrees": manifest.get("worktrees", []),
                }
            )
        archives = [
            {
                "archive_root": str(archive_root),
                "task_root_name": archive_root.name,
                "entries": sorted(child.name for child in archive_root.iterdir() if child.is_dir()),
            }
            for archive_root in self.archived_task_roots()
        ]
        return {
            "workspace_root": str(self.root),
            "config_path": str(self.config_path),
            "repositories": repositories,
            "active_tasks": tasks,
            "archived_tasks": archives,
        }

    # Doctor intentionally reports every independent issue instead of failing at the first one.
    def doctor_report(  # noqa: C901, PLR0912, PLR0915
        self,
    ) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        if not self.config_path.exists():
            issues.append(
                {
                    "severity": "error",
                    "code": "missing_config",
                    "message": f"missing workspace config: {self.config_path}",
                }
            )
            return {"workspace_root": str(self.root), "ok": False, "issues": issues}
        try:
            config = self.load_config()
        except WorkspaceError as exc:
            issues.append(
                {
                    "severity": "error",
                    "code": "invalid_config",
                    "message": str(exc),
                }
            )
            return {"workspace_root": str(self.root), "ok": False, "issues": issues}
        configured_repositories = cast("dict[str, Any]", config.get("repositories", {}))
        for repo_id in configured_repositories:
            entry_path = self.projects_root / repo_id
            if not entry_path.exists():
                issues.append(
                    {
                        "severity": "error",
                        "code": "missing_project",
                        "message": f"missing project entry: {entry_path}",
                        "repo_id": repo_id,
                    }
                )
                continue
            try:
                repo_root = git_root(entry_path.resolve())
            except WorkspaceError as exc:
                issues.append(
                    {
                        "severity": "error",
                        "code": "invalid_project",
                        "message": str(exc),
                        "repo_id": repo_id,
                    }
                )
                continue
            if not self.source_repo_clean(repo_root):
                issues.append(
                    {
                        "severity": "error",
                        "code": "dirty_project",
                        "message": f"source repo has uncommitted changes: {entry_path}",
                        "repo_id": repo_id,
                    }
                )
        for task_root in self.active_task_roots():
            try:
                manifest = self.load_task_manifest(task_root)
            except WorkspaceError as exc:
                issues.append(
                    {
                        "severity": "error",
                        "code": "invalid_manifest",
                        "message": str(exc),
                        "task_root": str(task_root),
                    }
                )
                continue
            worktrees = self.collect_manifest_worktrees(manifest)
            for entry in worktrees:
                project_id = entry.get("project_id")
                relative_path = entry.get("path")
                branch = entry.get("branch")
                if (
                    not isinstance(project_id, str)
                    or not isinstance(relative_path, str)
                    or not isinstance(branch, str)
                ):
                    issues.append(
                        {
                            "severity": "error",
                            "code": "invalid_worktree_entry",
                            "message": f"invalid worktree entry in {task_root}",
                            "task_root": str(task_root),
                        }
                    )
                    continue
                binding = config.get("repositories", {}).get(project_id)
                if binding is None:
                    issues.append(
                        {
                            "severity": "error",
                            "code": "unknown_project",
                            "message": f"manifest references unknown project id: {project_id}",
                            "task_root": str(task_root),
                        }
                    )
                    continue
                worktree_path = (self.root / relative_path).resolve()
                if not worktree_path.exists():
                    issues.append(
                        {
                            "severity": "error",
                            "code": "missing_worktree",
                            "message": f"missing worktree path: {worktree_path}",
                            "task_root": str(task_root),
                            "project_id": project_id,
                        }
                    )
                    continue
                if not worktree_path.is_dir():
                    issues.append(
                        {
                            "severity": "error",
                            "code": "invalid_worktree_path",
                            "message": f"worktree path is not a directory: {worktree_path}",
                            "task_root": str(task_root),
                            "project_id": project_id,
                        }
                    )
                    continue
                try:
                    repo_root = git_root(self.projects_root / project_id)
                except WorkspaceError as exc:
                    issues.append(
                        {
                            "severity": "error",
                            "code": "invalid_project",
                            "message": str(exc),
                            "task_root": str(task_root),
                            "project_id": project_id,
                        }
                    )
                    continue
                worktree_entry = git_worktree_entry(repo_root, worktree_path)
                if worktree_entry is None:
                    issues.append(
                        {
                            "severity": "error",
                            "code": "unregistered_worktree",
                            "message": f"worktree is not registered in git: {worktree_path}",
                            "task_root": str(task_root),
                            "project_id": project_id,
                        }
                    )
                    continue
                actual_branch = git_branch_from_entry(worktree_entry)
                if actual_branch != branch:
                    issues.append(
                        {
                            "severity": "error",
                            "code": "branch_mismatch",
                            "message": f"worktree branch mismatch: {actual_branch} != {branch}",
                            "task_root": str(task_root),
                            "project_id": project_id,
                        }
                    )
        return {
            "workspace_root": str(self.root),
            "ok": not issues,
            "issues": issues,
        }

    def set_agent_mode(self, mode: str) -> dict[str, Any]:
        if mode not in AGENT_MODES:
            raise_workspace_error(f"invalid agent mode: {mode}")
        template_path = self.agent_template_path(mode)
        self.ensure_root_agents(mode, overwrite=True)
        target_path = self.root / "AGENTS.md"
        return {
            "workspace_root": str(self.root),
            "mode": mode,
            "template_path": str(template_path),
            "agents_path": str(target_path),
        }
