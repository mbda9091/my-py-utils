from __future__ import annotations

import selectors
import subprocess
import sys
from contextlib import ExitStack
from typing import TYPE_CHECKING

import pytest
from skill_sync.adder import add_skill
from skill_sync.errors import SkillSyncError
from skill_sync.installations import read_managed_installations
from skill_sync.installer import install_skill
from skill_sync.repository import init_repository, read_repository_config

from tests.skill_sync_helpers import make_local_repository, make_personal_config, write_skill

if TYPE_CHECKING:
    from pathlib import Path

    from skill_sync.repository import RepositoryConfig

_WORKER = """
import sys
from pathlib import Path
from skill_sync import adder, installer, repository, uninstaller, remover

action, pause, state, repo, source, target = sys.argv[1:]
config = repository.RepositoryConfig(Path(repo), Path(state))
if pause == "yes":
    module = {"add": adder, "uninstall": uninstaller, "remove": remover}.get(action, installer)
    if action == "init-failure":
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


def _start(
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
    _expect_line(process, "START")
    return process


def _expect_line(process: subprocess.Popen[bytes], expected: str) -> None:
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


def _assert_waiting(process: subprocess.Popen[bytes]) -> None:
    assert process.stdout is not None
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert not selector.select(timeout=0.2), "Second mutation bypassed the state lock"
    assert process.poll() is None


def _finish(process: subprocess.Popen[bytes], *, failure: bool = False) -> None:
    output, error = process.communicate(input=b"continue\n", timeout=15)
    assert process.returncode == (1 if failure else 0), error
    assert output.decode().strip() == ("FAILED" if failure else "DONE")


@pytest.mark.parametrize("first_action", ["add", "install"])
@pytest.mark.parametrize("second_action", ["add", "install", "init"])
def test_process_mutations_preserve_all_installation_records(
    tmp_path: Path, first_action: str, second_action: str
) -> None:
    config = make_personal_config(tmp_path)
    first_source = write_skill(
        tmp_path / "external" if first_action == "add" else config.repository_path / "skills",
        "alpha",
    )
    if second_action == "init":
        second_source = make_local_repository(tmp_path / "new")
    else:
        second_source = write_skill(
            tmp_path / "external" if second_action == "add" else config.repository_path / "skills",
            "beta",
        )
    target = tmp_path / "installed"
    with ExitStack() as stack:
        first = _start(stack, first_action, config, first_source, pause=True)
        _expect_line(first, "READY")
        second = _start(stack, second_action, config, second_source, pause=False)
        _assert_waiting(second)
        _finish(first)
        _finish(second)

    first_link = first_source if first_action == "add" else target / "alpha"
    expected = {str(first_link)}
    if second_action != "init":
        second_link = second_source if second_action == "add" else target / "beta"
        expected.add(str(second_link))
        assert second_link.resolve() == config.repository_path / "skills" / "beta"
    else:
        assert read_repository_config(config.state_home).repository_path == second_source
    assert first_link.resolve() == config.repository_path / "skills" / "alpha"
    assert set(read_managed_installations(config.state_home)) == expected


def test_failed_binding_rollback_does_not_erase_another_process_install(tmp_path: Path) -> None:
    config = make_personal_config(tmp_path)
    write_skill(config.repository_path / "skills", "alpha")
    second_source = write_skill(config.repository_path / "skills", "beta")
    target = tmp_path / "installed"
    install_skill("alpha", config, target_root=target)
    new_repository = make_local_repository(tmp_path / "new")
    with ExitStack() as stack:
        first = _start(stack, "init-failure", config, new_repository, pause=True)
        _expect_line(first, "READY")
        second = _start(stack, "install", config, second_source, pause=False)
        _assert_waiting(second)
        _finish(first, failure=True)
        _finish(second)
    assert read_repository_config(config.state_home) == config
    assert set(read_managed_installations(config.state_home)) == {
        str(target / "alpha"),
        str(target / "beta"),
    }
    assert (target / "beta").resolve() == second_source


@pytest.mark.parametrize("operation", ["add", "install"])
def test_mutation_rejects_configuration_obtained_before_rebinding(
    tmp_path: Path, operation: str
) -> None:
    config = make_personal_config(tmp_path)
    write_skill(config.repository_path / "skills")
    original = write_skill(tmp_path / "external")
    current = init_repository(make_local_repository(tmp_path / "new"), state_home=config.state_home)
    if operation == "add":
        with pytest.raises(SkillSyncError, match="binding changed"):
            add_skill(original, config)
    else:
        with pytest.raises(SkillSyncError, match="binding changed"):
            install_skill("demo-skill", config, target_root=tmp_path / "installed")
    assert read_repository_config(config.state_home) == current
    assert not original.is_symlink()
    assert not read_managed_installations(config.state_home)


@pytest.mark.parametrize("action", ["uninstall", "remove"])
def test_removal_process_serializes_with_install(tmp_path: Path, action: str) -> None:
    config = make_personal_config(tmp_path)
    alpha = write_skill(config.repository_path / "skills", "alpha")
    beta = write_skill(config.repository_path / "skills", "beta")
    target = tmp_path / "installed"
    install_skill("alpha", config, target_root=target)
    with ExitStack() as stack:
        first = _start(stack, action, config, alpha, pause=True)
        _expect_line(first, "READY")
        second = _start(stack, "install", config, beta, pause=False)
        _assert_waiting(second)
        _finish(first)
        _finish(second)
    assert not (target / "alpha").is_symlink()
    assert (target / "beta").readlink() == beta
    assert set(read_managed_installations(config.state_home)) == {str(target / "beta")}
