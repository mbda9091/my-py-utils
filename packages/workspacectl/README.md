# workspacectl

Workspace controller for requirement and collaboration Git worktrees.

Run `workspacectl` from a workspace root containing `workspace.json` and
registered project checkouts under `projects/`. `init` also creates an
independent Git repository and ignores the disposable `projects/` and
`worktrees/` directories, so workspace control files and future skills can be
tracked normally.

```bash
workspacectl init
workspacectl status
workspacectl doctor
workspacectl create-requirement \
  --title "Merchant Search Flow" \
  --project merchant-server \
  --slug merchant-search-flow

# Join existing branches owned by collaborators.
workspacectl create-collaboration \
  --slug merchant-search-debug \
  --project-branch merchant-app:feature/alice/search-ui \
  --project-branch merchant-server:feature/bob/search-api
```

`--source-prd-url` is optional. When provided, the URL is recorded in the task
manifest; otherwise `source_prd_url` is stored as `null`.

`--slug` is required and must use lowercase ASCII letters, digits, and single
hyphens, with a letter or digit at both ends. Worktree creation only validates
the projects named by `--project`; other entries in `workspace.json` may be
missing locally. It does not require the source checkout to be clean: by
default it runs `git fetch origin HEAD` and creates from `FETCH_HEAD`. Use
`--upstream` to fetch a different upstream from `origin`.

## Requirement branch naming

New requirement branches default to `<username>/feat/<slug>`, without a
`merchant/` prefix. Both `create-requirement` and `extend-requirement` accept:

- `--git-username <username>`: override the username segment. Otherwise use
  global Git `user.name`, then the OS username. The selected username is
  sanitized before generating the branch name.
- `--branch-type <type>`: choose `feat`, `fix`, `refactor`, `docs`, `test`, or
  `chore`; the default for generated names is `feat`.
- `--branch <name>`: use a full Git branch name instead of generating one.
  This option is mutually exclusive with both `--git-username` and
  `--branch-type` (even an explicit `--branch-type feat`).

The complete generated or overridden branch name is validated as a Git branch
name before any fetch or worktree mutations.

For example, with the registered project `web`:

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

# mbda9091/chore/build-config (full-name override)
workspacectl create-requirement --title "Build configuration" --project web \
  --slug build-config --branch mbda9091/chore/build-config
```

### Extend or reuse an existing requirement

With no explicit naming flags (`--git-username`, `--branch-type`, or `--branch`),
`extend-requirement` preserves the task's recorded branch for added worktrees,
including legacy `merchant/...` names. Explicit naming flags apply only to
added worktrees; existing worktrees and branches are never renamed.

```bash
# Add a project using the recorded branch, including a legacy branch.
workspacectl extend-requirement --task-root worktrees/<date>-macos-web --add api

# Override the branch only for this added project; do not rename existing worktrees.
workspacectl extend-requirement --task-root worktrees/<date>-macos-web --add desktop \
  --git-username mbda9091 --branch-type fix
```

`create-requirement --reuse-existing` likewise preserves the recorded branch
when no naming flags are supplied, rather than recomputing the new default.
An explicit naming request that conflicts with the recorded branch is rejected;
reuse never renames an existing branch. The existing requirement must still
match the requested slug, title, PRD URL, and project scope.

## Collaboration and worktree hooks

`create-collaboration` accepts one or more explicit `project-id:branch`
mappings. It fetches each existing branch from `origin`, uses a same-named local
tracking branch, and creates all requested worktrees as one operation. It never
creates a generated requirement branch, pushes, rebases, resets, or resolves a
diverged branch. Collaboration mappings are fixed after creation and are
finished with `cleanup`; `extend-requirement` and `archive-requirement` only
accept requirement task roots.

Worktree creation and cleanup automatically detect Android, iOS, or default
projects and run the corresponding local hooks. Use `--project-type` to
override detection. Android cleanup keeps external project garbage by default;
pass `--clean-project-garbage` only when its removal is intended.

When a source checkout contains `AGENTS.local.md`, every worktree gets an
`AGENTS.local.md` symlink to that main-checkout file.

See [the design context](../../docs/workspacectl/CONTEXT.md) and
[the execution runbook](../../docs/workspacectl/execution-runbook.md) for the
workspace model and lifecycle.
