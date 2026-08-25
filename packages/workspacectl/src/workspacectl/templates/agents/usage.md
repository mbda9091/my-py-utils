# Workspace Quick Start

This repo is used through commands. Do not reason about internal terms or rules unless you are changing the workspace itself.
This template is injected by `workspacectl init` when a fresh workspace root does not already have `AGENTS.md`.

## Default loop

1. Initialize once:
   ```bash
   workspacectl init
   ```
   This creates the workspace Git repository and ignores `projects/` and
   `worktrees/`; workspace control files and skills remain trackable.
2. Update `workspace.json` with the projects you want to use.
3. Make sure each `projects/<id>` points to a real source checkout or symlink.
4. Create a requirement:
   ```bash
   workspacectl create-requirement \
     --title "..." \
     --project <id> \
     --project <id> \
     --slug <slug>
   ```
   `--source-prd-url "..."` is optional.
5. Work inside the generated `worktrees/<date>-<slug>/<project-id>-<slug>/` directory.
6. Archive finished docs:
   ```bash
   workspacectl archive-requirement --task-root worktrees/<date>-<slug>
   ```
7. Clean up the disposable worktree:
   ```bash
   workspacectl cleanup --task-root worktrees/<date>-<slug>
   ```

## What to remember

- `status` shows the current state.
- `doctor` checks for problems.
- `update` refreshes the workspace view.
- `cleanup` comes after `archive-requirement`.
- The branch name is managed for you.
- Worktree hooks auto-detect Android, iOS, and default projects; use
  `--project-type` when detection needs an override.
- The worktree agent should stay inside its own worktree.

## If you need a single command picture

- Start: `init`
- Prepare: `create-requirement`
- Work: inside the generated worktree
- Finish docs: `archive-requirement`
- Remove disposable state: `cleanup`
