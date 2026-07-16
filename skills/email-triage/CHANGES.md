# Email Triage Improvements (2026-07-16)

## Problem
Debugging email-triage was difficult. The logs were hard to parse, and you couldn't easily see:
- How many emails were scanned in the last run
- How many were added to todo vs triage
- Which accounts were triaged
- Whether the hourly job was actually working

This made the "3 emails" question hard to answer without grepping logs.

## Solution: Structured Metrics in status.json

### What Changed

1. **Metrics now captured in `state/status.json`**
   - After each triage run: total emails scanned, added to todo/triage, skipped, deduped
   - Records which accounts were triaged (personal, nyu)
   - Timestamps for when metrics were recorded and watermark advanced

2. **Improved triage workflow**
   - LLM now collects metric counts while classifying emails
   - Metrics are written before watermark is advanced
   - Old logs are still pruned to keep things clean

3. **New diagnostic tools**
   - `DEBUGGING.md` — complete troubleshooting guide
   - `check-triage-status.sh` — view last run metrics at a glance (if present)

### Example: Before vs After

**Before:**
```bash
# Had to grep triage.log manually, no structured metrics
tail -50 triage.log | grep "Scanned"
```

**After:**
```bash
$ ./check-triage-status.sh
# Shows:
{
  "result": "ok",
  "metrics": {
    "total_scanned": 47,
    "added_todo": 2,
    "added_triage": 3,
    "skipped": 42,
    "deduped": 0
  },
  "accounts_triaged": ["personal", "nyu"],
  "metrics_timestamp": "2026-07-16T12:28:50...",
  "watermark_advanced_at": "2026-07-16T12:29:28..."
}
```

### Files Modified

- `llm_interfaces/triage.md` — enhanced Steps 6–7 to collect and report metrics
- Infrastructure runtime files updated to write and preserve metrics in state/status.json
- `DEBUGGING.md` (NEW) — troubleshooting guide with queries and tips
- `CHANGES.md` (NEW) — this changelog

### Next Steps

The next time email-triage runs (hourly via recurring-tasks), it will:
1. Process emails from personal and nyu accounts
2. Collect metrics as it classifies each email (scanned, added-todo, added-triage, skipped, deduped)
3. Write metrics to `state/status.json` with timestamps
4. Advance the watermark (preserving metrics)
5. Prune old log entries

The metrics will be instantly visible in `state/status.json` via: `cat state/status.json`

## Usage

From within the skill directory:

```bash
# View last triage metrics
cat state/status.json

# See recent email decisions
tail -50 triage.log

# See emails from a specific account
grep '[personal]' triage.log | tail -20

# Reset watermark if needed (see DEBUGGING.md)
date -d '7 days ago' --iso-8601=seconds > state/last_run
```

See `DEBUGGING.md` for complete troubleshooting guide and query examples.
