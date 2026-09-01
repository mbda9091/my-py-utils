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
