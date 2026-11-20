# session-scan

Discover actual local Codex and DSH session files, returning one JSON object.

M1 provides file discovery, bounded header reads, optional read-only Codex index
metadata, and the Python lazy message adapter. Directory and message-time CLI
filters are planned for M2 and are **not available yet**.

```bash
uv run session-scan --source codex --codex-home ~/.codex
uv run session-scan --source dsh --dsh-root ~/.dsh/sessions
```

No format flag is needed. Discovery never scans dialogue to populate JSON fields.
Codex files under `sessions/` and `archived_sessions/` are included, even without
an index row. Index rows without files are ignored. DSH filenames and headers
must agree on version 0, 3 or 4; both `.jsonl` and `.jsonl.zstd` are supported.
DSH's persistence backend defaults to Zstandard and also supports explicitly
configured uncompressed storage. This reader does not change either setting.

The Python API keeps discovery separate from message reading:

```python
from pathlib import Path
from session_scan.discovery import discover
from session_scan.messages import iter_messages
from session_scan.reader import SessionReader

found = discover(codex_home=Path("~/.codex").expanduser())
for session in found.files:
    with SessionReader(session.header.path, session.header.source) as reader:
        for message in iter_messages(reader):
            # Consume only as far as needed; no dialogue text is returned.
            print(message.timestamp, message.line_number)
            break
        # Errors encountered so far remain visible even if an iteration stops early.
        print(reader.errors)
```

Message adapters:

- Codex: native user/agent message events and completed `UserMessage` /
  `AgentMessage` items. Legacy `response_item/message` records are accepted;
  paginated response items are excluded as context/projection mirrors, as are
  records with retained-source or replay markers. Top-level `timestamp` is used.
- DSH 0/3/4: appended `user/message` with source `user`, or appended
  `assistant/message` with source `model`. Configuration, tools, context sources,
  replacements and packed streaming deltas do not themselves count as dialogue.
  Message events use millisecond `time`. Model `replayState` is provider state,
  not an inherited-history flag, and does not suppress a model's live message.
- Legacy `seedLength` excludes inherited sequence numbers. `isSeeded` without a
  numeric cut requires a complete streaming boundary prepass to locate the last
  inherited `session/end-seed`, then a second lazy pass on the same bounded source.
  This prepass happens only when messages are requested. A missing or unreadable
  boundary yields errors rather than attributing inherited dialogue to the child.

Unknown message origins and missing/invalid message times are reported, never
replaced with file modification times. Events are yielded in file order, which
need not be chronological. Header limit: 1 MiB; message-row limit: 16 MiB.
Reads are bounded to the source's opening size, with constant-sized decoder
buffers. Fully consumed compressed streams check frame boundaries as well as
decoder errors; early termination does not validate unvisited tails.

The reader does not guarantee historical send times, complete transcripts,
cross-file snapshots or successful Codex resume. It does not reconstruct
historical packed deltas or expand history from a different file. Errors are
structured and contain locations, not dialogue content.

Exit codes: 0 completed (including no matches), 1 partial failure, 2 invalid
arguments/source, 130 interrupted. Diagnostics belong on stderr; scan results
are JSON on stdout. `--help` and argument errors do not inspect session data.
