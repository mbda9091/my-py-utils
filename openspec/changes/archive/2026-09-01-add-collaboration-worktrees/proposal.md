## Why

`workspacectl create-requirement` is designed for independent development: it creates a new generated branch for every selected repository. When joining work already underway by other people, users instead need a safe way to fetch existing remote branches and prepare the same multi-repository worktree environment without deriving personal development branches.

## What Changes

- Add a `create-collaboration` command that accepts a collaboration slug and one or more explicit `project-id:branch` mappings.
- Create one collaboration task root containing at most one worktree per registered repository, while allowing different repositories to use different existing remote branches.
- Fetch each branch from `origin`, create or reuse its same-named local tracking branch safely, and never reset, rebase, force-update, push, or generate a requirement branch.
- Make multi-repository creation transactional for worktree directories, generated metadata, and newly created local branches.
- Record collaboration identity and per-project branch mappings in a typed task manifest, and generate collaboration-specific worktree guidance.
- Make `status`, `update`, `doctor`, and `cleanup` recognize collaboration tasks.
- Keep collaboration scope immutable after creation: `extend-requirement` and `archive-requirement` reject collaboration task roots with a clear error; users finish them with `cleanup`.

## Capabilities

### New Capabilities

- `collaboration-worktrees`: Create, inspect, refresh, diagnose, and clean up multi-repository collaboration worktrees that check out explicitly selected existing branches.

### Modified Capabilities

None.

## Impact

- Affects the `workspacectl` CLI parser, output rendering, controller task/manifest model, Git worktree preparation, generated agent guidance, status/update/doctor handling, and cleanup validation.
- Adds workspace-level tests for argument parsing, branch-state safety, transactional rollback, manifest/output behavior, lifecycle compatibility, and hooks.
- Updates the `workspacectl` README, execution runbook, context/ADR documentation, and packaged usage/development agent templates.
- Does not add runtime dependencies or change existing requirement task behavior.
