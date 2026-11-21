# session-scan

Find actual local Codex and DSH session files by working directory and message
time, returning one JSON object. Files without an independent record are ignored.

```bash
uv run session-scan --source codex --codex-home ~/.codex
uv run session-scan --source dsh --dsh-root ~/.dsh/sessions
uv run session-scan --directory ~/Code/project
uv run session-scan --directory ~/Code/a --directory ~/Code/b --exact-directory
uv run session-scan --since 2026-11-19 --until 2026-11-20
uv run session-scan --directory ~/Code/project --since 2026-11-19T10:00:00+08:00
```

No format flag is needed. Directory filters match the recorded working directory,
including descendants by default. Multiple directories use OR; directories and
time use AND. Paths are normalized lexically, without resolving cwd symlinks or
folding case; historical directories need not exist.

Date arguments use the system local timezone. `--since` includes its boundary;
`--until YYYY-MM-DD` includes the entire date, while a timezone-aware ISO 8601
`--until` is exclusive. Exact datetime arguments must include `Z` or an offset.
Either boundary may be omitted. Parsed UTC boundaries and local date offsets are
returned in `filters`, including any daylight-saving differences.

Without time conditions, only headers and necessary index metadata are read.
With time conditions, directory exclusions happen first; remaining files are
scanned lazily for an actual user/assistant message in the interval. File mtime
and index update time do not pre-exclude old messages in a long-running session.
The first matching message in **file order** stops the scan; results include its
time, decoded JSONL line number and available ordinal/seq, not a full count or
transcript. A file with incomplete time information is reported as indeterminate,
not a confirmed non-match; a valid hit can still be returned with errors.

Output contains `results`, effective `filters`, `summary`, `warnings` and `errors`.
No matches still returns JSON with an empty results array. Unknown cwd is excluded
only by directory queries. The summary separates metadata-only reads, body scans,
early stops, directory exclusions and incomplete time scans; incomplete files can
also have valid matches, so those counts overlap. Metadata-only results sort by
last update time; time matches sort by the first encountered hit, descending, then
by source and absolute path. Message content is never included.

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
  Source-attributed `runtime-context`, `time-context` and `tool-jobs` user-role
  records are context injections, not human messages.
  Scheduled deliveries, approval-policy notices, workflow context and Codex
  subagent placeholder notices are also excluded by their explicit source kinds.
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
