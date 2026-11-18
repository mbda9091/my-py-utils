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
5. Work inside the generated `worktrees/<date>-<slug>/<project-id>-<slug>/` directory. Keep durable requirement artifacts in the task root outside child worktrees.
6. Archive the finished requirement:
   ```bash
   workspacectl archive-requirement --task-root worktrees/<date>-<slug>
   ```

## Choose a requirement branch

New branches default to `<username>/feat/<slug>`, with no `merchant/` prefix.
Both `create-requirement` and `extend-requirement` accept these naming flags:

- `--git-username <username>` overrides the username. Without it, detection uses
  global Git `user.name`, then the OS username. The selected username is sanitized.
- `--branch-type <type>` accepts `feat`, `fix`, `refactor`, `docs`, `test`, or
  `chore`; generated names default to `feat`.
- `--branch <full-name>` overrides the complete Git branch name. Do not combine
  it with `--git-username` or `--branch-type`, even `--branch-type feat`.

The complete branch name is validated before any fetch or worktree mutations.
Examples for a registered project `web`:

```bash
# mbda9091/feat/macos-web
workspacectl create-requirement --title "macOS web" --project web \
  --slug macos-web --git-username mbda9091
# mbda9091/fix/web-startup
workspacectl create-requirement --title "Fix web startup" --project web \
  --slug web-startup --git-username mbda9091 --branch-type fix
# mbda9091/refactor/plugin-loader
workspacectl create-requirement --title "Refactor plugin loader" --project web \
  --slug plugin-loader --git-username mbda9091 --branch-type refactor
# mbda9091/chore/build-config
workspacectl create-requirement --title "Build configuration" --project web \
  --slug build-config --branch mbda9091/chore/build-config
```

## Extend or reuse a requirement

```bash
workspacectl extend-requirement --task-root worktrees/<date>-<slug> --add <id>
```

With no explicit naming flags, added worktrees use the recorded branch,
including legacy `merchant/...` names. Explicit `--git-username`, `--branch-type`,
or `--branch` applies only to added worktrees; no existing worktree or branch is
renamed.

For `create-requirement --reuse-existing`, omitting naming flags also preserves
the recorded branch. A conflicting explicit naming request is rejected, not
used to rename it. Reuse still requires matching slug, title, PRD URL, and scope.

## Join existing collaboration branches

Use a collaboration task when the branches already exist on `origin` and are
shared with other developers:

```bash
workspacectl create-collaboration \
  --slug <slug> \
  --project-branch <project-id>:<existing-branch> \
  --project-branch <project-id>:<existing-branch>
```

Each project maps to one branch. The mappings cannot be extended or archived;
finish the collaboration with:

```bash
workspacectl cleanup --task-root worktrees/<date>-<slug>
```

## What to remember

- `status` shows the current state.
- `doctor` checks for problems.
- `update` refreshes the workspace view.
- `archive-requirement` removes child worktrees and moves the complete task root into `doc/`.
- Requirement branch names are managed for you; collaboration branches are explicit.
- Worktree hooks auto-detect Android, iOS, and default projects; use
  `--project-type` when detection needs an override.
- The worktree agent should stay inside its own worktree.

## If you need a single command picture

- Start: `init`
- Prepare: `create-requirement`
- Work: inside the generated worktree
- Finish and archive: `archive-requirement`
- Join shared branches: `create-collaboration`
- Finish shared-branch work: `cleanup`
