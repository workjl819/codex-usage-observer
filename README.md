# Codex Usage Observer

Codex Usage Observer is a standalone local tool that collects per-request usage
from Codex session transcripts across all projects, stores the result in a
single SQLite database, and serves a small dashboard for exploration.

This is intentionally plugin-independent for the core collection path, so it
can aggregate usage from any Codex project as long as the session JSONL files
exist under `~/.codex/sessions/`.

## What It Collects

For each completed Codex turn, the collector stores:

- prompt text
- project path and project name
- model
- start/completion timestamps
- duration and time-to-first-token
- input, cached-input, output, reasoning, and total tokens
- primary and secondary rate-limit usage snapshots

In the dashboard, `Rate Limit` shows the 5h and weekly used percentages at the
end of the request. `Usage Remaining` columns show `100 - used_percent`.
`5h Delta` and `Weekly Delta` show the visible percentage change from the
previous completed request.

## Files

- `collector.py`: scans `~/.codex/sessions/**/*.jsonl` into SQLite
- `dashboard.py`: serves a local dashboard and JSON API
- `web/index.html`: dashboard UI
- `state/usage.db`: generated SQLite database

## Usage

Start everything with one command:

```bash
python3 start.py
```

It will refresh the SQLite database first, then start the dashboard at:

```text
http://127.0.0.1:8765
```

While running, it will also re-scan Codex sessions every 5 seconds and the
dashboard will auto-refresh on the same interval.

You can still run the two steps separately when needed:

```bash
python3 collector.py
python3 dashboard.py
```

You can filter the dashboard by:

- project name from the dropdown
- model name with the text filter
- recent requests in the table

## Notes

- The current MVP uses `~/.codex/sessions/**/*.jsonl` only.
- Re-running the collector is safe; rows are upserted by `turn_id`.
- The dashboard reads the same SQLite database and does not require the plugin.
