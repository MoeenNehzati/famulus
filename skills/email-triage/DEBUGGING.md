# Email Triage Debugging Guide

## Quick Status Check

Check the status of the last triage run by viewing the metrics in `state/status.json`:

```bash
cat state/status.json
```

This shows:
- **Metrics** — emails scanned, added to todo/triage, skipped, deduped
- **Accounts triaged** — which email accounts were processed
- **Watermark** — when the last run completed
- **Result** — success or error

## Understanding the Metrics

After each triage run, `state/status.json` contains:

```json
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
  "metrics_timestamp": "2026-07-15T18:00:50.789069-04:00",
  "watermark_advanced_at": "2026-07-15T18:01:11.789069-04:00"
}
```

**Metrics breakdown:**
- `total_scanned` — total emails examined across all accounts
- `added_todo` — emails routed to todo list (bills, replies needed, follow-ups)
- `added_triage` — emails routed to triage list (events, opportunities, optional actions)
- `skipped` — emails excluded (promotional, newsletters, informational only)
- `deduped` — emails that matched existing triage items (already tracked)

## Query Metrics Programmatically

```bash
# See the full status
cat state/status.json

# Parse JSON if jq is available
jq .metrics state/status.json        # see just the metrics
jq .accounts_triaged state/status.json   # see which accounts were triaged
jq .result state/status.json         # check if run succeeded
```

## Troubleshooting

### "Only a few emails" summary but I sent more

The triage watermark (in `state/last_run`) determines which emails are "new". If the watermark is recent, only newer emails since then are scanned. To scan older emails:

```bash
# View current watermark
cat state/last_run

# Force a fresh triage — reset watermark to 7 days ago and run again
date -d '7 days ago' --iso-8601=seconds > state/last_run
```

Then invoke email-triage again to process emails from the past week.

### Check if the recurring job is running

```bash
# See active timers
dispatcher --caller-skill recurring-tasks recurring-tasks.machine.scripts-status

# View email-triage job logs
dispatcher --caller-skill recurring-tasks recurring-tasks.machine.scripts-view-logs email-triage --lines 50

# Test the job immediately
dispatcher --caller-skill recurring-tasks recurring-tasks.machine.scripts-test email-triage
```

### See detailed email decisions

The triage log shows every email and why it was classified:

```bash
# Last 50 email decisions
tail -50 triage.log

# Emails from a specific account
grep '\[personal\]' triage.log | tail -20
grep '\[nyu\]' triage.log | tail -20

# Emails added to todo
grep 'TODO' triage.log | tail -20

# Emails added to triage
grep 'POTENTIAL' triage.log | tail -20

# Skipped emails
grep 'SKIP' triage.log | tail -20
```

## How It Works

1. **Hourly run** — `email-triage` runs via `recurring-tasks` every hour
2. **Fetch envelopes** — downloads new emails since the watermark from all configured accounts (personal, nyu)
3. **Classify** — LLM reads emails and routes to todo/triage/skip
4. **Log decisions** — every classification is logged with reason
5. **Record metrics** — counts are written to `state/status.json` with timestamp
6. **Advance watermark** — `last_run` timestamp is updated so next run only sees new emails
7. **Prune logs** — old triage entries (>30 days) are cleaned up

The metrics stay in `status.json` so you can always see what the last run found without parsing logs.

## Metric Sources

Each count comes from the `scripts-log-decision` calls in triage.log:

- **total_scanned** = count of all decisions (SKIP + NO_ACTION + TODO + POTENTIAL + DEDUP)
- **added_todo** = count of TODO decisions
- **added_triage** = count of POTENTIAL decisions
- **skipped** = count of SKIP decisions
- **deduped** = count of DEDUP decisions

The LLM collects these counts while processing each email and stores them for Step 7.
