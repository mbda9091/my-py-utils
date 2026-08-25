# workspacectl

Workspace controller for requirement-scoped Git worktrees.

Run `workspacectl` from a workspace root containing `workspace.json` and
registered project checkouts under `projects/`.

```bash
workspacectl init
workspacectl status
workspacectl doctor
workspacectl create-requirement \
  --title "Merchant Search Flow" \
  --source-prd-url "https://docs.example/prd/42" \
  --project merchant-server \
  --slug merchant-search-flow
```

See [the design context](../../docs/workspacectl/CONTEXT.md) and
[the execution runbook](../../docs/workspacectl/execution-runbook.md) for the
workspace model and lifecycle.
