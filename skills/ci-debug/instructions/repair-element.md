# Repair One CI Element

Start from the assigned matrix element, shared debug context, and failure
ledger. Reuse that context for every targeted invocation so its stable setup and
request-scoped reports survive agent or session restarts. Keep every known
failure until a report actually executes and clears it.

Treat a stall as a failure class. Record the active element and last completed
selector, then choose the smallest runnable selector that contains the first
unresolved work. Run it with an explicit wall-clock bound; exceeding that bound
is red evidence, not a reason to extend the enclosing workflow timeout. When a
subprocess stalls, verify cleanup of its whole process tree and inherited
resources before accepting the repair.

While failures remain:

1. Choose one failure class and its smallest failure-containing selector set.
   Prefer exact failing test nodes, then the smallest set of containing test
   files when exact nodes are unavailable. Do not rerun selectors already known
   to pass or unrelated unresolved failures.
2. Diagnose that class and patch only evidence-backed paths.
3. Commit and push the repair branch only under the assigned
   `git-workflow.interface.default` envelope.
4. Use `ci-debug._rtx.interface.run-targeted-tests` for this element and only
   that selector set. Verify that the report executed every requested selector;
   an omitted selector remains unresolved even if the report says green.
5. Replace only the probed ledger entries with failures in the new report;
   retain every unprobed failure. If the same set repeats without a relevant
   code or condition change, return blocked instead of retrying indefinitely.

When the failure ledger is empty, use
`ci-debug._rtx.interface.run-targeted-tests` once for the whole matrix element.
Use a whole-element probe earlier only when the report cannot identify a test
node or containing test file. If a whole-element probe is red, add its failures
to the ledger and resume the smallest-set loop. If it is green, return the
commits, diff, and test reports to the coordinator without integration or
cleanup.

Targeted and whole-element results never establish overall CI green. Machine
reports are evidence, not Git authority.
