from __future__ import annotations

import json
import selectors
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from skill_sync.config import config_path
from skill_sync.repository import RepositoryConfig, init_repository

if TYPE_CHECKING:
    from collections.abc import Sequence
    from contextlib import ExitStack
    from pathlib import Path


def run_git(args: Sequence[str], *, cwd: Path | None) -> str:
    executable = shutil.which("git")
    if executable is None:
        message = "Git is required for skill-sync integration tests."
        raise RuntimeError(message)
    result = subprocess.run(  # noqa: S603
        [executable, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def make_local_repository(root: Path) -> Path:
    repository = root / "personal-skills"
    run_git(["init", "--initial-branch=main", str(repository)], cwd=None)
    return repository


def make_personal_config(root: Path) -> RepositoryConfig:
    return init_repository(make_local_repository(root), state_home=root / "local-state")


@dataclass(frozen=True, slots=True)
class GitRepository:
    path: Path
    remote: Path


def make_committed_repository(root: Path) -> GitRepository:
    remote = root / "remote.git"
    checkout = root / "source"
    run_git(["init", "--bare", "--initial-branch=main", str(remote)], cwd=None)
    run_git(["init", "--initial-branch=main", str(checkout)], cwd=None)
    for key, value in (
        ("user.name", "Test User"),
        ("user.email", "test@example.com"),
        ("commit.gpgSign", "false"),
        ("core.hooksPath", str(root / "no-hooks")),
    ):
        run_git(["config", key, value], cwd=checkout)
    (checkout / "README.md").write_text("Personal skill repository\n", encoding="utf-8")
    run_git(["add", "README.md"], cwd=checkout)
    run_git(["commit", "-m", "Initialize repository manually"], cwd=checkout)
    run_git(["remote", "add", "origin", str(remote)], cwd=checkout)
    run_git(["push", "--set-upstream", "origin", "main"], cwd=checkout)
    return GitRepository(checkout, remote)


def write_legacy_config(state_home: Path, *, branch: str = "main") -> Path:
    state_home.mkdir(parents=True, exist_ok=True)
    checkout = state_home / "repository"
    config_path(state_home).write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "repository": "git@example.com:me/skills.git",
                "branch": branch,
                "checkout": str(checkout),
            }
        ),
        encoding="utf-8",
    )
    return checkout


def write_skill(root: Path, name: str = "demo-skill") -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Demo skill.\n---\n\nBody\n",
        encoding="utf-8",
    )
    return skill


_WORKER = """
import sys
from pathlib import Path
from skill_sync import adder, installer, repository, uninstaller, remover, updater

action, pause, state, repo, source, target = sys.argv[1:]
config = repository.RepositoryConfig(Path(repo), Path(state))
if pause == "yes":
    module = {"add": adder, "uninstall": uninstaller, "remove": remover}.get(action, installer)
    if action == "update":
        original = updater._pull
        def paused_pull(executable, repo):
            print("READY", flush=True)
            sys.stdin.readline()
            return original(executable, repo)
        updater._pull = paused_pull
    elif action == "init-failure":
        original = repository.atomic_write_json
        def paused_write(path, payload):
            if path.name == "config.json":
                print("READY", flush=True)
                sys.stdin.readline()
                raise OSError("injected binding write failure")
            original(path, payload)
        repository.atomic_write_json = paused_write
    else:
        original = module.write_managed_installations
        def paused_write(records, state_home):
            print("READY", flush=True)
            sys.stdin.readline()
            original(records, state_home)
        module.write_managed_installations = paused_write
print("START", flush=True)
if action == "add":
    adder.add_skill(Path(source), config)
elif action == "update":
    updater.update_repository(config)
elif action == "remove":
    remover.agents_skills_root = lambda: Path(target)
    remover.remove_skill(Path(source).name, config)
elif action == "uninstall":
    uninstaller.agents_skills_root = lambda: Path(target)
    uninstaller.uninstall_skill(Path(source).name, config)
elif action == "install":
    installer.install_skill(Path(source).name, config, target_root=Path(target))
else:
    try:
        repository.init_repository(Path(source), state_home=Path(state))
    except OSError:
        print("FAILED", flush=True)
        sys.exit(1)
print("DONE", flush=True)
"""


def start_mutation(
    stack: ExitStack,
    action: str,
    config: RepositoryConfig,
    source: Path,
    *,
    pause: bool,
) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-c",
            _WORKER,
            action,
            "yes" if pause else "no",
            str(config.state_home),
            str(config.repository_path),
            str(source),
            str(config.state_home.parent / "installed"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    stack.enter_context(process)
    stack.callback(process.kill)
    expect_process_line(process, "START")
    return process


def expect_process_line(process: subprocess.Popen[bytes], expected: str) -> None:
    assert process.stdout is not None
    # Unbuffered byte reads keep readiness checks bounded, including failures
    # before READY.
    line = b""
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while not line.endswith(b"\n"):
            assert selector.select(timeout=10), f"Timed out waiting for {expected}: {line}"
            character = process.stdout.read(1)
            if not character:
                _, error = process.communicate(timeout=5)
                pytest.fail(f"Worker exited before {expected}: {line}; {error.decode()}")
            line += character
    assert line.decode().strip() == expected


def assert_process_waiting(process: subprocess.Popen[bytes]) -> None:
    assert process.stdout is not None
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert not selector.select(timeout=0.2), "Second mutation bypassed the state lock"
    assert process.poll() is None


def finish_mutation(process: subprocess.Popen[bytes], *, failure: bool = False) -> None:
    output, error = process.communicate(input=b"continue\n", timeout=15)
    assert process.returncode == (1 if failure else 0), error
    assert output.decode().strip() == ("FAILED" if failure else "DONE")
