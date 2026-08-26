---
status: accepted
---
# Durable task deliverables are requirements and implementation docs

A task does not keep a full execution snapshot. Archive removes the child code worktrees and moves the remaining requirement directory under `doc/<task-root-name>/` with its original files, directories, and relative paths preserved. The controller does not inspect or classify those artifacts; later distillation is a separate concern. The implementation doc may include a compact execution review appendix so the process can be rechecked later without keeping a separate trace file.

The appendix field list and verification shape are defined in
`docs/workspacectl/execution-runbook.md`.

## Consequences

- The workspace keeps human-facing deliverables, not session traces.
- Child worktree-local code state remains disposable.
- No fixed rename or document classification is imposed during archive.
- Process reviewability comes from the archived requirement directory and any appendix it contains.
