# Analyze CI

Create `output-directory` as a new private directory outside the repository and
every worktree. Refuse an existing destination. Derive three new child paths:
`snapshot`, `failure-episodes`, and `runtime-hotspots`. Then proceed in this
fixed order:

1. Call `ci-debug._rtx.interface.fetch-github-actions-history` with the
   `snapshot` path.
2. Call `ci-debug._rtx.interface.report-test-failures-between-green-runs` with
   that snapshot and the `failure-episodes` path.
3. Call `ci-debug._rtx.interface.report-ci-runtime-hotspots` with that same
   snapshot and the `runtime-hotspots` path.

Resolve the structured repository, workflow, branch, output directory, event,
time bound, and run limit before collection. Structured arguments take
precedence over conflicting prose in `request`. The output root and all three
child destinations must be new and private.

Optional filters and resource controls are additive. They may reduce the
selected evidence or stop collection with explicit partial coverage; they never
change metric definitions or authorize another operation.

Invoke both reporters against the same published snapshot. Verify each report
receipt names the snapshot identifier and digest returned by collection, and
verify the two receipts agree before interpreting them. A missing or invalid
child publication marker, schema mismatch, or source snapshot digest mismatch
is failure, not a tolerable evidence gap. Do not create a root manifest or root
publication marker.

Report:

- the snapshot and both report paths, plus their common snapshot identity;
- selected logical-run and attempt counts;
- complete episodes, censored spans, and evidence coverage;
- recurrence bounds rather than an unjustified point estimate;
- API update span, API start delay when identified, observed job execution
  envelope, observed job-minutes, and repeated-step hotspots as separate
  descriptive measures;
- every missing, expired, malformed, truncated, denied, or unclassified input.

Never call the qualification runner from this route. Historical comparisons are
observational: do not claim billing cost, DAG critical path, removable time, or
causal speedup.
