# AGENTS.md

Guidance for agents working in this repository.

## Project Shape

This repository is a Python CLI utilities monorepo managed by `uv` workspaces.

```text
packages/
  my-py-utils-common/   Shared helpers for CLI packages
  my-py-utils-cli/      Aggregate installer for all workspace CLI commands
  hello-cli/            Example CLI package
  video2audio/          CLI for extracting audio from video files with ffmpeg
  mac-cache-cleaner/    CLI for cleaning macOS build caches
  node-modules-cleaner/ CLI for recursively deleting node_modules directories
  skill-sync/           Git-backed personal skill registry sync CLI
tests/                  Workspace-level tests
```

The root project is not a distributable package. It owns workspace configuration,
shared tool configuration, dependency groups, and the lockfile.

## Tooling

Use `uv` for all dependency and environment operations.

Do not manually activate `.venv`. Prefer:

```bash
uv sync --all-groups --all-packages
uv run pytest
uv run ruff check .
uv run ruff format .
uv run pyright
uv build --all-packages
```

The `Makefile` wraps the same operations:

```bash
make dev
make check
make build
```

## Package Rules

Each CLI utility should live in its own package under `packages/`.

Expected shape:

```text
packages/<cli-name>/
  pyproject.toml
  README.md
  src/<import_name>/
    __init__.py
    main.py or cli.py
```

Every CLI package should define its command in `[project.scripts]`.

Example:

```toml
[project.scripts]
hello-util = "hello_cli.main:main"
```

The aggregate installer package `packages/my-py-utils-cli` is intentionally
different: it may contain almost no runtime code. It depends on the workspace CLI
packages and re-exports their console scripts from its own `[project.scripts]`.
When adding a new CLI package, also add it to `my-py-utils-cli` dependencies,
`[tool.uv.sources]`, `[project.scripts]`, and README command list.

If a package uses shared helpers, depend on the workspace package:

```toml
[project]
dependencies = ["my-py-utils-common"]

[tool.uv.sources]
my-py-utils-common = { workspace = true }
```

## Shared Code

Put reusable helpers in `packages/my-py-utils-common`.

Keep CLI packages thin:

- parse command-line arguments in the CLI package
- put reusable logic in shared helpers or package-local modules
- keep `main.py` or `cli.py` focused on I/O and orchestration

## skill-sync Rules

`skill-sync` publishes commits to a personal Git registry. Its publish path must
use a registry-specific Git identity from configuration, not the machine's
global Git identity. Keep `publish` fail-closed when this identity is missing.

Publishing local skills runs privacy and safety checks. Avoid hard-coded local
absolute paths such as `/Users/<name>/...` in publishable skill content; use
`~`, `<vault-path>`, or another placeholder instead.

## Code Quality

The project targets Python 3.11+.

Ruff is configured strictly with `select = ["ALL"]`. Current intentional
exceptions:

- `T201` is allowed for CLI `main.py` files, because CLI entry points print user output.
- `S101` is allowed in tests, because pytest assertions are expected.
- docstring rules are disabled for now.

Before handing off changes, run:

```bash
uv run ruff check .
uv run pyright
uv run pytest
```

For package/build changes, also run:

```bash
uv build --all-packages
```

## Tests

Workspace-level tests live in `tests/`.

When adding a new CLI package:

- add tests for reusable logic
- add parser or command behavior tests when the CLI has branching behavior
- avoid testing only the happy path if parsing, file I/O, or external process behavior is involved

## Dependency Management

Use `uv add` and `uv remove` for dependencies.

Use dependency groups in the root `pyproject.toml` for developer tools. Keep
runtime dependencies in the relevant package's `pyproject.toml`.

Commit `uv.lock` when dependencies change.

## Git Hygiene

Do not commit generated or local files:

- `.venv/`
- `.pytest_cache/`
- `.ruff_cache/`
- `dist/`
- `build/`
- `*.egg-info/`
- `__pycache__/`

These are already ignored by `.gitignore`.
