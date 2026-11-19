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
  skill-sync/           Local personal skill repository management and symlink installation
  repo-security/        Repository-local scanners, Git hooks, and installation diagnostics
tests/                  Package-scoped tests stored centrally
```

The root project is not a distributable package. It owns workspace configuration,
shared tool configuration, dependency groups, and the lockfile.

## Tooling

Use `uv` for all dependency and environment operations.

Do not manually activate `.venv`. Prefer:

```bash
make dev
uv sync --all-groups --all-packages
uv run pytest tests/test_repo_security*.py
uv run ruff check .
uv run ruff format .
uv run pyright
uv build --all-packages
```

Use `make dev` to initialize a fresh clone: it configures repository security
checks and Git hooks before syncing workspace dependencies. Direct `uv sync`
only manages workspace dependencies.

The pytest command above is an example for one package; select tests according
to the rules below. Do not run the entire test suite by default.

The `Makefile` provides workspace-wide convenience commands. They are not the
default validation scope for a package-local change:

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

`skill-sync` binds one persistent local personal skill repository. It absorbs
explicitly selected skills and installs them as symlinks, preserving unrelated
skills in the client directory. Git staging, commits, branches, identity and
remote synchronization belong to the user. The tool's only Git subprocess is
a fixed read-only repository-root check; do not add generic Git execution or
restore the removed `publish`/`sync` workflows.

Keep v1 configuration and installation-record support read-only until explicit
migration. Back up old state, preserve unresolved provenance, and never use
unknown or stale installation records to authorize overwriting local content.

## Code Quality

The project targets Python 3.11+.

Ruff is configured strictly with `select = ["ALL"]`. Current intentional
exceptions:

- `T201` is allowed for CLI `main.py` files, because CLI entry points print user output.
- `S101` is allowed in tests, because pytest assertions are expected.
- docstring rules are disabled for now.

Before handing off code changes, run the static checks below and the tests
selected under **Tests**. Do not add a full-workspace pytest run as a routine
handoff step.

```bash
uv run ruff check .
uv run pyright
```

For package/build changes, also run:

```bash
uv build --all-packages
```

## Tests

Tests live centrally in `tests/`, but are organized by package. This location
does not make them a single mandatory workspace-wide test suite. CLI packages
are independent; the aggregate installer couples dependency declarations and
console-script registration, not their business behavior.

Select validation based on the actual change:

- For a package-local change, run that package's test files only. For example,
  use `uv run pytest tests/test_repo_security*.py` or
  `uv run pytest tests/test_skill_sync_*.py`.
- For shared helper changes, run the helper tests and the tests of affected
  consumers, including indirect consumers where behavior can change. Do not
  include unrelated CLI packages.
- For aggregate installer changes, verify dependency resolution, installation,
  and the affected console-script entry points. Do not run every CLI package's
  business tests just because an aggregate dependency or script was added.
- A change to `uv.lock` alone does not justify testing all packages. Inspect
  which dependencies changed and test their affected consumers.
- Run the full test suite only when explicitly requested, or when a cross-package
  change has an impact that cannot reasonably be bounded. Explain that scope
  before running it.
- Documentation-only changes need document/diff checks, not runtime tests.

Report the selected test scope and results. Distinguish real integration runs
from mocked checks and skipped tests; a skip is not evidence of passing behavior.

Save reports and logs intended for user review under the repository's `.tmp/`
directory, which is Git-ignored. Internal scanner snapshots and raw reports may
continue to use private, automatically cleaned temporary directories.

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
