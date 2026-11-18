# codex-session-export

Export local Codex sessions into a single normalized rollout format, with separate metadata and raw source snapshots. No network or running Codex server is required.

```bash
uv run codex-session-export --directory ~/Code/my-project --output ~/Downloads/codex-export
uv run codex-session-export --directory ~/Code/my-project --exact-directory --output ~/Downloads/codex-export
uv run codex-session-export --directory ~/Code/a --directory ~/Code/b --output ~/Downloads/codex-export
uv run codex-session-export --session <id> --codex-home ~/.codex --output ~/Downloads/codex-export
```

Omit filters to export all discovered sessions, including archived sessions and child agents. `--codex-home` overrides `$CODEX_HOME`, then `~/.codex`. `--directory` compares each session's recorded cwd, including descendants by default. Paths are normalized lexically; symlink aliases and case variants are not merged. Historical directories need not exist. Multiple directories use OR; session IDs and directories use AND.

Each invocation creates a unique batch:

```text
<output>/<batch>/
  manifest.json
  sessions/<id>/
    rollout.jsonl
    metadata.json
    raw/rollouts/001.jsonl
    raw/database.jsonl
```

`rollout.jsonl` is the consumer entry point for both file and database sources. Each UTF-8 line contains `timestamp`, contiguous `ordinal`, `type`, and `payload`, beginning with `session_meta`. The bundled `schemas/rollout-v1.schema.json` defines the `codex-rollout-export-v1` contract. `codex_session_export.rollout.read_standard` validates and reads it. Use `dialogue` for message views and `operations` for structured tool views; don't count `item_completed` message mirrors twice.

Raw file bytes and selected database rows are preserved separately. Metadata records provenance, generated timestamps/IDs, missing information and conversion failures. An epoch timestamp is an explicitly marked fallback when no historical time exists. `metadata_only` means only session metadata was recovered; `partial` means known truncation, loss or ambiguity. `available_content` means identified available content was converted, not that the entire original history was recovered. Attachments remain references; this is not a workspace backup or a guaranteed `codex resume` import.

Supported database layouts: `state_5.sqlite` and `thread_history_1.sqlite`. Unknown versions, unknown event/item types, malformed records and source conflicts are reported without modifying source data. Invalid records stay in raw; valid remaining events can form a partial standard file.

Exit codes: `0` completed (possibly with documented missing history or no matches), `1` partial failure, `2` invalid arguments/source, `130` interrupted. Consult `manifest.json` and each `metadata.json`; raw preservation alone does not count as a successful standard export.
