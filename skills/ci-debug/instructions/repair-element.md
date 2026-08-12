# Repair One CI Element

Start from the assigned matrix element and failure set.

While failures remain:

1. Diagnose one failure class and patch only evidence-backed paths.
2. Commit and push the repair branch only under the assigned
   `git-workflow.interface.default` envelope.
3. Use `ci-debug._rtx.interface.run-targeted-tests` for this element and the
   current failures.
4. Replace the failure set with the failures in the new report. If the same set
   repeats without a relevant code or condition change, return blocked instead
   of retrying indefinitely.

When the selected failures are green, use
`ci-debug._rtx.interface.run-targeted-tests` once for the whole matrix element.
If it is red, continue with its failures. If it is green, return the commits,
diff, and test reports to the coordinator without integration or cleanup.

Targeted and whole-element results never establish overall CI green. Machine
reports are evidence, not Git authority.
