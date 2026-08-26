from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from codex_hook_ctl import core
from codex_hook_ctl.cli import main

EXPECTED_REGISTRATION_COUNT = 2

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def write_layer(
    root: Path,
    *,
    hook_name: str = "demo-hook",
    event: str = "PreToolUse",
    matcher: str = "^Bash$",
    source_script: str | None = None,
) -> Path:
    codex = root / ".codex"
    hook_dir = codex / "hooks"
    hook_dir.mkdir(parents=True)
    script = hook_dir / f"{hook_name}.py"
    script.write_text(source_script or f"print('{hook_name}')\n", encoding="utf-8")
    config = {
        "hooks": {
            event: [
                {
                    "matcher": matcher,
                    "hooks": [
                        {
                            "type": "command",
                            "command": f'python3 "{script}"',
                            "timeout": 10,
                            "statusMessage": "Loading demo hook",
                        },
                    ],
                },
            ],
        },
    }
    (codex / "hooks.json").write_text(json.dumps(config), encoding="utf-8")
    return script


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_record_global_splits_custom_hook_and_keeps_each_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    first_script = write_layer(home, hook_name="dynamic-agents")
    config = read_json(home / ".codex" / "hooks.json")
    hooks = config["hooks"]
    assert isinstance(hooks, dict)
    hooks["SessionStart"] = [
        {
            "matcher": "startup",
            "hooks": [
                {
                    "type": "command",
                    "command": f'python3 "{first_script}"',
                    "timeout": 3,
                },
                {
                    "type": "command",
                    "command": 'node "/outside/vendor-hook.js"',
                },
            ],
        },
    ]
    (home / ".codex" / "hooks.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    (tmp_path / "repo").mkdir()
    monkeypatch.chdir(tmp_path / "repo")

    assert main(["record"]) == 0

    output = capsys.readouterr()
    assert "Recorded hook: dynamic-agents" in output.out
    record_dir = tmp_path / "repo" / "codex-hooks" / "dynamic-agents"
    metadata = read_json(record_dir / "hook.json")
    assert metadata["source"] == "global"
    assert metadata["executable"] == "dynamic-agents.py"
    registrations = cast("list[dict[str, Any]]", metadata["registrations"])
    assert len(registrations) == EXPECTED_REGISTRATION_COUNT
    assert all(isinstance(item, dict) for item in registrations)
    assert all("{hook_file}" in item["command"] for item in registrations)
    assert not any("vendor-hook" in json.dumps(item) for item in registrations)
    assert (record_dir / "dynamic-agents.py").read_text(encoding="utf-8") == (
        "print('dynamic-agents')\n"
    )


def test_record_directory_reads_project_codex_hooks_json_and_uses_origin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    write_layer(project, hook_name="local-hook")

    def fake_origin(_directory: Path) -> str:
        return "https://example.test/team/project.git"

    monkeypatch.setattr(core, "_git_origin", fake_origin)
    monkeypatch.chdir(tmp_path)

    assert main(["record", "--directory", str(project)]) == 0

    metadata = read_json(tmp_path / "codex-hooks" / "local-hook" / "hook.json")
    assert metadata["source"] == "https://example.test/team/project.git"


def test_record_directory_falls_back_to_directory_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "named-project"
    write_layer(project, hook_name="local-hook")

    def fake_origin(_directory: Path) -> None:
        return None

    monkeypatch.setattr(core, "_git_origin", fake_origin)
    monkeypatch.chdir(tmp_path)

    assert main(["record", "--directory", str(project)]) == 0

    metadata = read_json(tmp_path / "codex-hooks" / "local-hook" / "hook.json")
    assert metadata["source"] == "named-project"


def test_inject_directory_writes_code_at_project_root_and_config_under_dot_codex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    write_layer(source, hook_name="dynamic-agents")
    repo = tmp_path / "records"
    repo.mkdir()
    monkeypatch.chdir(repo)
    assert main(["record", "--directory", str(source)]) == 0

    target = tmp_path / "target"
    target_codex = target / ".codex"
    target_codex.mkdir(parents=True)
    (target_codex / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionEnd": [
                        {
                            "matcher": "other",
                            "hooks": [{"type": "command", "command": "echo keep"}],
                        },
                    ],
                },
            },
        ),
        encoding="utf-8",
    )

    assert main(["inject", "dynamic-agents", "--directory", str(target)]) == 0

    copied = target / "codex-hooks" / "dynamic-agents.py"
    assert copied.read_text(encoding="utf-8") == "print('dynamic-agents')\n"
    injected = read_json(target / ".codex" / "hooks.json")
    injected_hooks = injected["hooks"]
    assert isinstance(injected_hooks, dict)
    assert "PreToolUse" in injected_hooks
    assert "SessionEnd" in injected_hooks
    assert str(copied) in json.dumps(injected_hooks["PreToolUse"])
    assert "echo keep" in json.dumps(injected_hooks["SessionEnd"])


def test_inject_global_writes_under_global_codex_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    write_layer(home, hook_name="global-hook")
    repo = tmp_path / "records"
    repo.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(repo)

    assert main(["record"]) == 0
    assert main(["inject", "global-hook"]) == 0

    target_file = home / ".codex" / "codex-hooks" / "global-hook.py"
    assert target_file.is_file()
    config = read_json(home / ".codex" / "hooks.json")
    assert str(target_file) in json.dumps(config["hooks"])


def test_record_and_inject_require_force_for_same_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source"
    write_layer(source, hook_name="demo-hook", source_script="print('new')\n")

    def fake_origin(_directory: Path) -> None:
        return None

    monkeypatch.setattr(core, "_git_origin", fake_origin)
    monkeypatch.chdir(tmp_path)
    assert main(["record", "--directory", str(source)]) == 0
    capsys.readouterr()

    source_script = source / ".codex" / "hooks" / "demo-hook.py"
    source_script.write_text("print('replacement')\n", encoding="utf-8")
    assert main(["record", "--directory", str(source)]) == 1
    error = capsys.readouterr()
    assert "Pass -f to overwrite it" in error.err
    assert main(["record", "--directory", str(source), "-f"]) == 0
    assert (tmp_path / "codex-hooks" / "demo-hook" / "demo-hook.py").read_text(
        encoding="utf-8"
    ) == "print('replacement')\n"

    target = tmp_path / "target"
    assert main(["inject", "demo-hook", "--directory", str(target)]) == 0
    assert main(["inject", "demo-hook", "--directory", str(target)]) == 1
    error = capsys.readouterr()
    assert "Pass -f to overwrite it" in error.err
    assert main(["inject", "demo-hook", "--directory", str(target), "-f"]) == 0
