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
