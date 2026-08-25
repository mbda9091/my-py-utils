# my-py-utils

Personal Python CLI utilities managed as a uv workspace.

All commands in this README are intended to run from this repository root.

## Layout

```text
packages/
  my-py-utils-common/   Shared helpers used by CLI packages
  hello-cli/            Small example CLI package
  video2audio/          CLI for extracting audio from video files with ffmpeg
  mac-cache-cleaner/    CLI for cleaning macOS build caches
  node-modules-cleaner/ CLI for recursively deleting node_modules directories
  workspacectl/         Controller for requirement-scoped Git worktrees
  my-py-utils-cli/      Aggregate installer for all workspace CLI commands
tests/                  Workspace-level tests
```

See [docs/uv-workspace-notes.md](docs/uv-workspace-notes.md) for the concepts
behind this layout.

skill-sync 个人 GitHub 同步工具方案见
[docs/skill-sync-design.md](docs/skill-sync-design.md)。

## Common Commands

```bash
uv sync --all-groups --all-packages
uv run pytest
uv run ruff check .
uv run ruff format .
uv run pyright
uv run --package hello-cli hello-util --name Codex
uv run --package video2audio video2audio --help
uv run --package mac-cache-cleaner mac-cache-cleaner --help
uv run --package node-modules-cleaner node-modules-cleaner ~/Code --dry-run
uv run --package skill-sync skill-sync --help
uv run --package workspacectl workspacectl --help
uv run --package skill-sync skill-sync init git@github.com:mbda9091/my-skills.git --branch main --git-name mbda9091 --git-email bozieg@qq.com
```

## Initialize This Project

Install or refresh the workspace environment:

```bash
uv sync --all-groups --all-packages
```

Run the standard checks:

```bash
uv run ruff check .
uv run pyright
uv run pytest
```

For package or build changes, also verify packaging:

```bash
uv build --all-packages
```

## Install All CLI Commands

From the repository root:

```bash
uv tool install --editable packages/my-py-utils-cli
```

Refresh an existing installation after code changes:

```bash
uv tool install --editable packages/my-py-utils-cli --force
```

This installs all workspace commands into your uv tool bin directory:

```text
hello-util
video2audio
mac-cache-cleaner
node-modules-cleaner
skill-sync
workspacectl
```

## Add A New CLI

1. Create `packages/<cli-name>/pyproject.toml`.
2. Put code under `packages/<cli-name>/src/<import_name>/`.
3. Add a command under `[project.scripts]`.
4. Add the package to `packages/my-py-utils-cli` dependencies, workspace sources, and scripts.
5. If it uses shared helpers, depend on `my-py-utils-common` and add:

```toml
[tool.uv.sources]
my-py-utils-common = { workspace = true }
```
