from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING

import pytest
from skill_sync.adder import add_skill
from skill_sync.errors import SkillSyncError
from skill_sync.installations import read_managed_installations
from skill_sync.installer import install_skill
from skill_sync.repository import init_repository, read_repository_config

from tests.skill_sync_helpers import (
    assert_process_waiting,
    expect_process_line,
    finish_mutation,
    make_local_repository,
    make_personal_config,
    start_mutation,
    write_skill,
)

if TYPE_CHECKING:
    from pathlib import Path


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
        first = start_mutation(stack, first_action, config, first_source, pause=True)
        expect_process_line(first, "READY")
        second = start_mutation(stack, second_action, config, second_source, pause=False)
        assert_process_waiting(second)
        finish_mutation(first)
        finish_mutation(second)

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
        first = start_mutation(stack, "init-failure", config, new_repository, pause=True)
        expect_process_line(first, "READY")
        second = start_mutation(stack, "install", config, second_source, pause=False)
        assert_process_waiting(second)
        finish_mutation(first, failure=True)
        finish_mutation(second)
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
        first = start_mutation(stack, action, config, alpha, pause=True)
        expect_process_line(first, "READY")
        second = start_mutation(stack, "install", config, beta, pause=False)
        assert_process_waiting(second)
        finish_mutation(first)
        finish_mutation(second)
    assert not (target / "alpha").is_symlink()
    assert (target / "beta").readlink() == beta
    assert set(read_managed_installations(config.state_home)) == {str(target / "beta")}
