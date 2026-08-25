# Workspace Development Guide

This repo is the workspace control-plane implementation. Use this mode only when changing the workspace itself.

## Read first

1. `CONTEXT.md`
2. `docs/execution-runbook.md`
3. `workspace.json`

These files define the model, lifecycle, and verification surface.

## Source files

- `packages/workspacectl/`
- `tests/test_workspacectl.py`
- `pyproject.toml`
- `docs/workspacectl/`

## Runtime state

- `projects/`
- `worktrees/`
- `doc/`
- `.workspace/`

These are runtime directories. Do not treat them as source unless you are testing the controller behavior.

## Development loop

1. Inspect the relevant docs and command behavior.
2. Change the controller code.
3. Add or update tests.
4. Run:
   ```bash
   uv run pytest tests/test_workspacectl.py
   ```
5. Smoke test the CLI:
   ```bash
   uv run workspacectl --help
   ```

## Guardrails

- Keep `workspace.json` as the source of truth.
- Keep lifecycle commands explicit and boring.
- Do not add hidden generated project catalogs.
- Do not make `init` do update or reconcile work.
- Do not make `cleanup` delete branches.
- Keep user-facing mode simple; move implementation detail here.

## Return to usage

When you finish changing the workspace itself, switch the root guide back with:

```bash
uv run workspacectl set-agent-mode usage
```
