# my-py-utils

Personal Python CLI utilities managed as a uv workspace.

## Layout

```text
packages/
  my-py-utils-common/   Shared helpers used by CLI packages
  hello-cli/            Small example CLI package
  video2audio/          CLI for extracting audio from video files with ffmpeg
  mac-cache-cleaner/    CLI for cleaning macOS build caches
  node-modules-cleaner/ CLI for recursively deleting node_modules directories
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
uv run --package skill-sync skill-sync init git@github.com:mbda9091/my-skills.git --branch main --git-name mbda9091 --git-email bozieg@qq.com
```

## Install All CLI Commands

```bash
uv tool install --editable packages/my-py-utils-cli
```

This installs all workspace commands into your uv tool bin directory:

```text
hello-util
video2audio
mac-cache-cleaner
node-modules-cleaner
skill-sync
```

## Add A New CLI

1. Create `packages/<cli-name>/pyproject.toml`.
2. Put code under `packages/<cli-name>/src/<import_name>/`.
3. Add a command under `[project.scripts]`.
4. If it uses shared helpers, depend on `my-py-utils-common` and add:

```toml
[tool.uv.sources]
my-py-utils-common = { workspace = true }
```
