from my_py_utils_common import greeting


def test_greeting_uses_world_for_blank_name() -> None:
    assert greeting("  ") == "Hello, world!"


def test_greeting_strips_name() -> None:
    assert greeting(" Codex ") == "Hello, Codex!"
