---
status: accepted
---
# Workspace itself is an independent git repo

The workspace root lives in its own git repository and owns the JSON config, docs, templates, and launcher artifacts. Source repositories stay separate and are only referenced from the workspace config, which keeps the control plane isolated from the mutable worktrees it creates.

## Consequences

- Workspace metadata can evolve without touching any source repo.
- The launcher and generator logic have a single home.
- Source repos remain clean inputs, not containers for workspace control files.
