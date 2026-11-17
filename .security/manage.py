"""Standalone installation engine, also imported by the CLI; stdlib only."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence

TEMPLATE_VERSION = "0.2.0"
GITLEAKS_VERSION = "8.30.1"
CHECKSUMS = {
    ("Linux", "x86_64"): "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
    ("Linux", "aarch64"): "e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080",
    ("Darwin", "x86_64"): "dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709",
    ("Darwin", "arm64"): "b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5",
}
MANIFEST = ".security/install-manifest.json"
WORKFLOW = ".github/workflows/repo-security.yml"
HOOKS_PATH = ".security/hooks"
IGNORE_ENTRIES = (
    "/.security/.venv/",
    "/.security/bin/",
    "/.security/__pycache__/",
    "/.security/install-manifest.json",
)
RUNTIME_NAMES = (
    "setup.py",
    "manage.py",
    "scan.py",
    "pii.py",
    "selftest.py",
    "run",
    "pyproject.toml",
    "uv.lock",
    "gitleaks.toml",
    "semgrep.yml",
    "policy.json",
    "hooks/pre-commit",
    "hooks/pre-push",
)
CUSTOMIZABLE = {".security/gitleaks.toml", ".security/semgrep.yml", ".security/policy.json"}


class RepoSecurityError(Exception):
    pass


class FileRecord(TypedDict):
    template_sha256: str
    installed_sha256: str
    executable: bool


class InstallRecord(TypedDict):
    schema_version: int
    template_version: str
    status: str
    phase: str
    files: dict[str, FileRecord]
    original_local_hooks: list[str]
    gitleaks: dict[str, str]


@dataclass(frozen=True)
class TemplateFile:
    destination: str
    content: bytes
    executable: bool

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True)
class HooksState:
    local: tuple[str, ...]
    effective: str | None
    origin: str | None


@dataclass(frozen=True)
class InstallPlan:
    root: Path
    resources: tuple[TemplateFile, ...]
    hooks: HooksState
    previous: InstallRecord | None
    create: tuple[str, ...]
    keep: tuple[str, ...]
    repair: tuple[str, ...]
    conflicts: tuple[str, ...]
    ignore_before: bytes | None
    ignore_after: bytes

    def describe(self) -> tuple[str, ...]:
        return (
            f"Repository: {self.root}",
            *(f"ADD {name}" for name in self.create),
            *(f"KEEP {name}" for name in self.keep),
            *(f"CHMOD {name}" for name in self.repair),
            *(f"CONFLICT {reason}" for reason in self.conflicts),
            f"Git hooks: {self.hooks.effective or '(default)'} -> {HOOKS_PATH} (local)",
            f"Hooks source: {self.hooks.origin or '(unset)'}",
            "Install/verify: Python 3.13, locked Semgrep/Presidio, SHA-256 checked Gitleaks.",
            "May download into .security and uv's Python/package caches; no business history scan.",
        )


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        env.pop(key, None)
    return subprocess.run(  # noqa: S603 -- fixed executable, explicit arguments, no shell
        ["git", "-C", str(root), *args],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=30,
    )


def repository_root(path: Path) -> Path:
    if not path.is_dir():
        msg = "Target must be an existing Git repository directory"
        raise RepoSecurityError(msg)
    result = git(path.resolve(), "rev-parse", "--show-toplevel")
    if result.returncode:
        msg = "Target must be a non-bare Git repository"
        raise RepoSecurityError(msg)
    root = Path(result.stdout.rstrip("\n"))
    if not (root / ".git").is_dir() or (root / ".git").is_symlink():
        msg = "Linked worktrees and submodule checkouts are not supported"
        raise RepoSecurityError(msg)
    return root


def hooks_state(root: Path) -> HooksState:
    local = git(root, "config", "--local", "--get-all", "core.hooksPath")
    effective = git(root, "config", "--show-origin", "--get", "core.hooksPath")
    if local.returncode not in (0, 1) or effective.returncode not in (0, 1):
        msg = "Cannot inspect Git hooks configuration"
        raise RepoSecurityError(msg)
    if effective.returncode == 1:
        return HooksState(tuple(local.stdout.splitlines()), None, None)
    origin, value = effective.stdout.rstrip("\n").split("\t", 1)
    return HooksState(tuple(local.stdout.splitlines()), value, origin)


def path_problem(root: Path, relative: str, *, directory: bool = False) -> str | None:
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or ".." in parts:
        return f"{relative}: invalid managed path"
    current = root
    for index, part in enumerate(parts):
        current /= part
        if current.is_symlink():
            return f"{relative}: symlink at {current.relative_to(root)}"
        is_directory = index < len(parts) - 1 or directory
        if current.exists() and (current.is_dir() if is_directory else current.is_file()) is False:
            return f"{relative}: unexpected file type at {current.relative_to(root)}"
    return None


def validate_file_records(entries: dict[str, Any]) -> None:
    allowed = {".security/" + name for name in RUNTIME_NAMES} | {WORKFLOW}
    if not {".security/" + name for name in RUNTIME_NAMES}.issubset(entries):
        raise ValueError
    for name, entry in entries.items():
        if name not in allowed:
            raise ValueError
        for key in ("template_sha256", "installed_sha256"):
            value = entry[key]
            if not isinstance(value, str) or len(value) != hashlib.sha256().digest_size * 2:
                raise ValueError
            int(value, 16)
        if not isinstance(entry["executable"], bool):
            raise TypeError


def validate_record(data: dict[str, Any]) -> InstallRecord:
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError
    if data["template_version"] != TEMPLATE_VERSION:
        raise ValueError
    if data["status"] not in ("incomplete", "ready") or not isinstance(data["phase"], str):
        raise ValueError
    if not isinstance(data["files"], dict) or not isinstance(data["gitleaks"], dict):
        raise TypeError
    original = data["original_local_hooks"]
    if not isinstance(original, list) or not all(
        x == HOOKS_PATH for x in cast("list[object]", original)
    ):
        raise ValueError
    validate_file_records(cast("dict[str, Any]", data["files"]))
    if not all(
        isinstance(k, str) and isinstance(v, str)
        for k, v in cast("dict[object, object]", data["gitleaks"]).items()
    ):
        raise TypeError
    return cast("InstallRecord", data)


def read_record(root: Path) -> InstallRecord | None:
    problem = path_problem(root, MANIFEST)
    if problem:
        raise RepoSecurityError(problem)
    path = root / MANIFEST
    if not path.exists():
        return None
    try:
        return validate_record(json.loads(path.read_bytes()))
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        msg = "Invalid or different-version install manifest; no automatic upgrade"
        raise RepoSecurityError(msg) from error


def hook_conflicts(root: Path, hooks: HooksState) -> list[str]:
    if hooks.effective is not None:
        if hooks.effective != HOOKS_PATH or any(x != HOOKS_PATH for x in hooks.local):
            return [
                f"core.hooksPath from {hooks.origin}: existing hook manager; "
                "merge .security/run staged and pre-push manually"
            ]
        return []
    conflicts: list[str] = []
    for name in ("pre-commit", "pre-push"):
        result = git(root, "rev-parse", "--git-path", "hooks/" + name)
        if result.returncode:
            msg = "Cannot locate existing Git hooks"
            raise RepoSecurityError(msg)
        path = Path(result.stdout.rstrip("\n"))
        if not path.is_absolute():
            path = root / path
        if path.exists() or path.is_symlink():
            conflicts.append(f"Existing {name}; merge .security/run staged and pre-push manually")
    return conflicts


def merged_ignore(original: bytes | None) -> bytes:
    text = (original or b"").decode("utf-8")
    lines = text.splitlines()
    additions = [x for x in IGNORE_ENTRIES if x not in lines and x.lstrip("/") not in lines]
    if not additions:
        return original or b""
    newline = "\r\n" if "\r\n" in text else "\n"
    separator = newline if text and not text.endswith(("\n", "\r")) else ""
    return (text + separator + newline.join(additions) + newline).encode("utf-8")


def make_plan(  # noqa: C901, PLR0912, PLR0915 -- aggregate all conflicts before any writes
    root: Path,
    resources: tuple[TemplateFile, ...],
    *,
    github_actions: bool = False,
    locked: bool = False,
) -> InstallPlan:
    conflicts: list[str] = []
    previous = None
    try:
        previous = read_record(root)
    except RepoSecurityError as error:
        conflicts.append(str(error))
    selected = tuple(
        x
        for x in resources
        if x.destination != WORKFLOW
        or github_actions
        or (previous is not None and WORKFLOW in previous["files"])
    )
    hooks = hooks_state(root)
    conflicts.extend(hook_conflicts(root, hooks))
    if (platform.system(), platform.machine()) not in CHECKSUMS:
        conflicts.append("Unsupported platform; use Linux/macOS x64/arm64 or WSL")
    if shutil.which("uv") is None:
        conflicts.append("Install uv before setup")
    if not os.access(root, os.W_OK) or not os.access(root / ".git", os.W_OK):
        conflicts.append("Repository and Git directory must be writable")
    if not locked and (
        (root / ".git/repo-security-setup.lock").exists()
        or (root / ".git/repo-security-setup.lock").is_symlink()
    ):
        conflicts.append(
            "Setup lock exists; after confirming no setup is running, "
            "remove .git/repo-security-setup.lock manually"
        )
    create: list[str] = []
    keep: list[str] = []
    repair: list[str] = []
    for resource in selected:
        problem = path_problem(root, resource.destination)
        if problem:
            conflicts.append(problem)
            continue
        target = root / resource.destination
        if not target.exists():
            create.append(resource.destination)
        elif hashlib.sha256(target.read_bytes()).hexdigest() != resource.digest:
            conflicts.append(f"{resource.destination}: custom or different-version content")
        elif (
            previous
            and resource.destination in previous["files"]
            and previous["files"][resource.destination]["installed_sha256"] != resource.digest
        ):
            conflicts.append(f"{resource.destination}: differs from recorded installation")
        else:
            keep.append(resource.destination)
            if resource.executable and not os.access(target, os.X_OK):
                repair.append(resource.destination)
    for relative in (".security/.venv", ".security/bin"):
        problem = path_problem(root, relative, directory=True)
        if problem:
            conflicts.append(problem)
    for relative in (".security/bin/gitleaks", ".gitignore"):
        problem = path_problem(root, relative)
        if problem:
            conflicts.append(problem)
    ignore_before = None
    ignore_after = b""
    if not path_problem(root, ".gitignore"):
        ignore = root / ".gitignore"
        ignore_before = ignore.read_bytes() if ignore.exists() else None
        try:
            ignore_after = merged_ignore(ignore_before)
        except UnicodeDecodeError:
            conflicts.append(".gitignore must be UTF-8 text")
        if ignore_after != ignore_before:
            create.append(".gitignore (merge)")
    return InstallPlan(
        root,
        selected,
        hooks,
        previous,
        tuple(create),
        tuple(keep),
        tuple(repair),
        tuple(conflicts),
        ignore_before,
        ignore_after,
    )


@contextlib.contextmanager
def setup_lock(root: Path) -> Generator[None, None, None]:
    path = root / ".git/repo-security-setup.lock"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        msg = "Setup lock exists; do not remove it while another setup is running"
        raise RepoSecurityError(msg) from error
    identity = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(f"pid={os.getpid()}\n")
        yield
    finally:
        current = path.lstat() if path.exists() or path.is_symlink() else None
        if current and (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
            path.unlink()


def atomic_write(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    if path.is_symlink():
        raise RepoSecurityError("Refusing to replace symlink: " + path.name)
    if path.exists() and path.read_bytes() == content:
        return
    descriptor, name = tempfile.mkstemp(prefix=".repo-security-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_record(root: Path, record: InstallRecord) -> None:
    problem = path_problem(root, MANIFEST)
    if problem:
        raise RepoSecurityError(problem)
    atomic_write(root / MANIFEST, (json.dumps(record, indent=2, sort_keys=True) + "\n").encode())


def create_resource(path: Path, resource: TemplateFile) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".repo-security-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(resource.content)
        temporary.chmod(0o755 if resource.executable else 0o644)
        os.link(temporary, path)  # Exclusive publication: never overwrite a concurrent file.
    finally:
        temporary.unlink(missing_ok=True)


def record_for(plan: InstallPlan) -> InstallRecord:
    return {
        "schema_version": 1,
        "template_version": TEMPLATE_VERSION,
        "status": "incomplete",
        "phase": "deploying",
        "files": {
            x.destination: {
                "template_sha256": x.digest,
                "installed_sha256": x.digest,
                "executable": x.executable,
            }
            for x in plan.resources
        },
        "original_local_hooks": (
            plan.previous["original_local_hooks"] if plan.previous else list(plan.hooks.local)
        ),
        "gitleaks": plan.previous["gitleaks"] if plan.previous else {},
    }


def deploy(plan: InstallPlan) -> None:
    for resource in plan.resources:
        problem = path_problem(plan.root, resource.destination)
        if problem:
            raise RepoSecurityError(problem)
        path = plan.root / resource.destination
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != resource.content:
                raise RepoSecurityError("File changed during setup: " + resource.destination)
        else:
            create_resource(path, resource)
        if resource.executable and not os.access(path, os.X_OK):
            path.chmod(path.stat().st_mode | 0o111)
    problem = path_problem(plan.root, ".gitignore")
    if problem:
        raise RepoSecurityError(problem)
    ignore = plan.root / ".gitignore"
    current = ignore.read_bytes() if ignore.exists() else None
    if current != plan.ignore_before:
        msg = ".gitignore changed during setup; preserving external edits"
        raise RepoSecurityError(msg)
    if plan.ignore_after != current:
        atomic_write(
            ignore,
            plan.ignore_after,
            mode=stat.S_IMODE(ignore.stat().st_mode) if ignore.exists() else 0o644,
        )


def run_tool(
    command: Sequence[str], *, cwd: Path, env: dict[str, str] | None = None, timeout: int = 180
) -> str:
    result = subprocess.run(  # noqa: S603 -- explicit argv; never forward potentially sensitive stderr
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode:
        msg = f"{Path(command[0]).name} failed (exit {result.returncode}); no clean result claimed"
        raise RepoSecurityError(msg)
    return result.stdout.strip()


def uv_environment() -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "UV_PROJECT_ENVIRONMENT",
        "VIRTUAL_ENV",
        "UV_WORKING_DIR",
        "UV_PROJECT",
        "UV_PYTHON",
        "PYTHONPATH",
        "PYTHONHOME",
    ):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def install_gitleaks(security: Path, previous: dict[str, str]) -> dict[str, str]:
    key = (platform.system(), platform.machine())
    checksum = CHECKSUMS[key]
    binary = security / "bin/gitleaks"
    if (
        binary.is_file()
        and not binary.is_symlink()
        and previous.get("archive_sha256") == checksum
        and previous.get("version") == GITLEAKS_VERSION
        and previous.get("sha256") == hashlib.sha256(binary.read_bytes()).hexdigest()
    ):
        if not os.access(binary, os.X_OK):
            binary.chmod(binary.stat().st_mode | 0o111)
        return previous
    arch = "x64" if key[1] == "x86_64" else "arm64"
    asset = f"gitleaks_{GITLEAKS_VERSION}_{key[0].lower()}_{arch}.tar.gz"
    url = f"https://github.com/gitleaks/gitleaks/releases/download/v{GITLEAKS_VERSION}/{asset}"
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 -- fixed HTTPS release URL
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != checksum:
        msg = "Gitleaks checksum mismatch; installation blocked"
        raise RepoSecurityError(msg)
    directory = security / "bin"
    directory.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        member = archive.getmember("gitleaks")
        stream = archive.extractfile(member) if member.isfile() else None
        if stream is None:
            msg = "Invalid Gitleaks archive executable"
            raise RepoSecurityError(msg)
        content = stream.read()
        atomic_write(binary, content, mode=0o755)
        if not os.access(binary, os.X_OK):
            binary.chmod(binary.stat().st_mode | 0o111)
        for name in ("LICENSE", "LICENSE.txt", "NOTICE", "NOTICE.txt"):
            with contextlib.suppress(KeyError):
                notice = archive.getmember(name)
                source = archive.extractfile(notice) if notice.isfile() else None
                if source:
                    atomic_write(directory / ("GITLEAKS-" + name), source.read(), mode=0o644)
    return {
        "version": GITLEAKS_VERSION,
        "archive_sha256": checksum,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def verify_runtime(security: Path) -> None:
    python = security / ".venv/bin/python"
    command = (
        "from importlib.metadata import version; "
        "assert version('semgrep') == '1.185.0'; "
        "assert version('presidio-analyzer') == '2.2.366'"
    )
    env = uv_environment()
    run_tool([str(python), "-B", "-c", command], cwd=security, env=env)
    version = run_tool([str(security / "bin/gitleaks"), "version"], cwd=security)
    if version.removeprefix("v") != GITLEAKS_VERSION:
        msg = "Unexpected Gitleaks version"
        raise RepoSecurityError(msg)
    run_tool([str(python), "-B", str(security / "selftest.py")], cwd=security, env=env)


def install_runtime(root: Path, record: InstallRecord) -> None:
    security = root / ".security"
    uv = shutil.which("uv")
    if uv is None:
        msg = "Install uv before setup"
        raise RepoSecurityError(msg)
    command = [
        uv,
        "sync",
        "--locked",
        "--no-config",
        "--python",
        "3.13",
        "--project",
        str(security),
    ]
    env = uv_environment()
    try:
        run_tool([*command, "--check"], cwd=security, env=env)
    except RepoSecurityError:
        run_tool(command, cwd=security, env=env, timeout=600)
    record["gitleaks"] = install_gitleaks(security, record["gitleaks"])
    prior = read_record(root)
    if not prior or prior["status"] != "ready" or prior["gitleaks"] != record["gitleaks"]:
        save_record(root, record)  # Preserve verified archive provenance on partial failure.
    verify_runtime(security)


def activate_hooks(plan: InstallPlan) -> None:
    if hooks_state(plan.root) != plan.hooks:
        msg = "Hooks configuration changed during setup; preserving external edits"
        raise RepoSecurityError(msg)
    if plan.hooks.local == (HOOKS_PATH,) and plan.hooks.effective == HOOKS_PATH:
        return
    if git(
        plan.root, "config", "--local", "--replace-all", "core.hooksPath", HOOKS_PATH
    ).returncode:
        msg = "Cannot enable repository-local hooks"
        raise RepoSecurityError(msg)
    try:
        if hooks_state(plan.root).effective != HOOKS_PATH:
            msg = "Hooks configuration did not take effect"
            raise RepoSecurityError(msg)  # noqa: TRY301 -- rollback follows this verification failure
    except (RepoSecurityError, OSError, ValueError, subprocess.SubprocessError):
        current = hooks_state(plan.root)
        if current.local == (HOOKS_PATH,):
            git(plan.root, "config", "--local", "--unset-all", "core.hooksPath")
            for value in plan.hooks.local:
                git(plan.root, "config", "--local", "--add", "core.hooksPath", value)
        raise


def restore_hooks(plan: InstallPlan) -> None:
    current = hooks_state(plan.root)
    if current.local != (HOOKS_PATH,) or current.effective != HOOKS_PATH:
        return  # A concurrent writer owns this value; do not overwrite it.
    if current.local == plan.hooks.local:
        return  # Existing protection must stay active on a failed repair.
    if git(plan.root, "config", "--local", "--unset-all", "core.hooksPath").returncode:
        msg = "Cannot restore original local hooks setting; inspect Git configuration"
        raise RepoSecurityError(msg)
    for value in plan.hooks.local:
        if git(plan.root, "config", "--local", "--add", "core.hooksPath", value).returncode:
            msg = "Cannot restore original local hooks value; inspect Git configuration"
            raise RepoSecurityError(msg)


def execute_setup(  # noqa: C901 -- phase checkpoints, hook activation and recovery stay ordered
    root: Path,
    resources: tuple[TemplateFile, ...],
    *,
    github_actions: bool = False,
    install_hooks: bool = True,
) -> InstallPlan:
    initial = make_plan(root, resources, github_actions=github_actions)
    if initial.conflicts:
        raise RepoSecurityError("\n".join(initial.conflicts))
    with setup_lock(root):
        plan = make_plan(root, resources, github_actions=github_actions, locked=True)
        if plan.conflicts:
            raise RepoSecurityError("\n".join(plan.conflicts))
        record = record_for(plan)
        activated = False
        updating = (
            not plan.previous
            or plan.previous["status"] != "ready"
            or bool(plan.create or plan.repair)
        )
        (root / ".security").mkdir(exist_ok=True)
        try:
            if updating:
                save_record(root, record)
            deploy(plan)
            record["phase"] = "deployed"
            if updating:
                save_record(root, record)
            install_runtime(root, record)
            record["phase"] = "tools-verified"
            if updating:
                save_record(root, record)
            # Revalidate managed content and hook state after slow external operations.
            final = make_plan(root, resources, github_actions=github_actions, locked=True)
            if final.conflicts or final.create or final.repair:
                msg = "Installation changed during verification; rerun setup after review"
                raise RepoSecurityError(msg)  # noqa: TRY301 -- persist incomplete state on failure
            if install_hooks:
                activate_hooks(plan)
                activated = True
                record["status"], record["phase"] = "ready", "ready"
            else:
                record["phase"] = "tools-verified"
                if plan.hooks.local == (HOOKS_PATH,) and plan.hooks.effective == HOOKS_PATH:
                    record["status"], record["phase"] = "ready", "ready"
            save_record(root, record)
        except (
            OSError,
            ValueError,
            RepoSecurityError,
            subprocess.SubprocessError,
            tarfile.TarError,
        ):
            record["status"] = "incomplete"
            if record["phase"] == "ready":
                record["phase"] = "tools-verified"
            if activated:
                restore_hooks(plan)
            save_record(root, record)
            raise
    return plan


def diagnose(root: Path, resources: tuple[TemplateFile, ...]) -> tuple[bool, tuple[str, ...]]:
    messages: list[str] = []
    healthy = True
    plan = make_plan(root, resources)
    for conflict in plan.conflicts:
        if any(conflict.startswith(name + ": custom") for name in CUSTOMIZABLE):
            messages.append("CUSTOM " + conflict)
        else:
            messages.append("ERROR " + conflict)
            healthy = False
    if plan.create or plan.repair:
        messages.extend("ERROR missing/needs repair: " + x for x in (*plan.create, *plan.repair))
        healthy = False
    if not plan.previous or plan.previous["status"] != "ready":
        messages.append("ERROR installation manifest missing or incomplete; run setup")
        healthy = False
    if plan.hooks.effective != HOOKS_PATH:
        messages.append("ERROR repository security hooks are not active")
        healthy = False
    unsafe = any(
        not any(c.startswith(name + ": custom") for name in CUSTOMIZABLE) for c in plan.conflicts
    )
    unsafe = unsafe or any(x != ".gitignore (merge)" for x in plan.create) or bool(plan.repair)
    if not trusted_binary(root, plan.previous):
        messages.append(
            "ERROR Gitleaks provenance or executable digest missing/mismatched; run setup"
        )
        unsafe = True
    if not unsafe:
        try:
            verify_runtime(root / ".security")
            messages.append("OK real scanner versions, local rules and synthetic detection")
        except (OSError, RepoSecurityError, subprocess.SubprocessError):
            messages.append(
                "ERROR scanner environment, versions or local rules failed verification"
            )
            healthy = False
    else:
        healthy = False
    messages.append(
        "CI: " + ("present" if (root / WORKFLOW).is_file() else "not configured (optional)")
    )
    return healthy, tuple(messages)


def trusted_binary(root: Path, record: InstallRecord | None) -> bool:
    binary = root / ".security/bin/gitleaks"
    if record is None or path_problem(root, ".security/bin/gitleaks") or not binary.is_file():
        return False
    provenance = record["gitleaks"]
    return (
        provenance.get("version") == GITLEAKS_VERSION
        and provenance.get("archive_sha256")
        == CHECKSUMS.get((platform.system(), platform.machine()))
        and provenance.get("sha256") == hashlib.sha256(binary.read_bytes()).hexdigest()
    )


def standalone_main(security: Path, *, install_hooks: bool) -> None:
    root = repository_root(security.parent)
    for name in RUNTIME_NAMES:
        problem = path_problem(root, ".security/" + name)
        if problem:
            raise RepoSecurityError(problem)
    resources = tuple(
        TemplateFile(
            ".security/" + name,
            (security / name).read_bytes(),
            name == "run" or name.startswith("hooks/"),
        )
        for name in RUNTIME_NAMES
    )
    if (root / WORKFLOW).is_file():
        resources += (TemplateFile(WORKFLOW, (root / WORKFLOW).read_bytes(), executable=False),)
    execute_setup(
        root, resources, github_actions=(root / WORKFLOW).is_file(), install_hooks=install_hooks
    )
