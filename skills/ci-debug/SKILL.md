---
name: ci-debug
description: Use when CI history needs analysis, a local branch needs exact-SHA GitHub Actions qualification, CI is red, or one matrix failure needs isolated repair.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus_dispatcher.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `ci-debug._rtx.interface.fetch-github-actions-history` — Fetch one bounded GitHub Actions workflow history with complete attempt enumeration and frozen Git provenance into a private snapshot.
  - Caller: `ci-debug`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--branch": "BRANCH", "--event": "EVENT", "--repo-root": "REPO", "--run-limit": "N", "--since": "ISO_8601", "--snapshot-dir": "NEW_DIR", "--timeout": "SECONDS", "--workers": "N", "--workflow": "WORKFLOW"}, "positionals": [], "stdin": null}
    Required options: ["--branch", "--repo-root", "--snapshot-dir", "--workflow"]; positional arity: 0..0; stdin: forbidden
- `ci-debug._rtx.interface.report-ci-runtime-hotspots` — Report descriptive API timing observed job execution envelopes job-minutes and heuristic repeated-step hotspots from one snapshot.
  - Caller: `ci-debug`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--report-dir": "NEW_DIR", "--snapshot-dir": "SNAPSHOT"}, "positionals": [], "stdin": null}
    Required options: ["--report-dir", "--snapshot-dir"]; positional arity: 0..0; stdin: forbidden
- `ci-debug._rtx.interface.report-test-failures-between-green-runs` — Report one canonical test incidence per complete green-to-green episode with recurrence bounds frozen provenance and explicit coverage gaps.
  - Caller: `ci-debug`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--report-dir": "NEW_DIR", "--snapshot-dir": "SNAPSHOT"}, "positionals": [], "stdin": null}
    Required options: ["--report-dir", "--snapshot-dir"]; positional arity: 0..0; stdin: forbidden
- `ci-debug._rtx.interface.run-ci` — Start or poll one durable complete remote CI matrix for an exact pushed candidate.
  - Caller: `ci-debug`
  - Version: 2
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--context": "DIR", "--expected-sha": "SHA", "--ref": "REF", "--repo-root": "REPO", "--timeout": "SECONDS"}, "positionals": [], "stdin": null}
    Required options: ["--context", "--expected-sha", "--ref", "--repo-root"]; positional arity: 0..0; stdin: forbidden
- `ci-debug._rtx.interface.run-targeted-tests` — Run one selected failure set or complete matrix element for an exact candidate.
  - Caller: `ci-debug`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--context": "DIR", "--expected-sha": "SHA", "--jobs": "N", "--os": "OS", "--profile": "PROFILE", "--ref": "REF", "--repo-root": "REPO", "--selector": "NODE", "--task": "TASK", "--timeout": "SECONDS"}, "positionals": [], "stdin": null}
    Required options: ["--context", "--expected-sha", "--os", "--ref", "--repo-root", "--selector", "--task"]; positional arity: 0..0; stdin: forbidden
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--context": "DIR", "--expected-sha": "SHA", "--jobs": "N", "--os": "OS", "--profile": "PROFILE", "--ref": "REF", "--repo-root": "REPO", "--selectors-json": "JSON", "--task": "TASK", "--timeout": "SECONDS"}, "positionals": [], "stdin": null}
    Required options: ["--context", "--expected-sha", "--os", "--ref", "--repo-root", "--selectors-json", "--task"]; positional arity: 0..0; stdin: forbidden
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--context": "DIR", "--expected-sha": "SHA", "--from-report": "PATH", "--jobs": "N", "--os": "OS", "--profile": "PROFILE", "--ref": "REF", "--repo-root": "REPO", "--task": "TASK", "--timeout": "SECONDS"}, "positionals": [], "stdin": null}
    Required options: ["--context", "--expected-sha", "--from-report", "--os", "--ref", "--repo-root", "--task"]; positional arity: 0..0; stdin: forbidden
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {"--context": "DIR", "--expected-sha": "SHA", "--jobs": "N", "--os": "OS", "--profile": "PROFILE", "--ref": "REF", "--repo-root": "REPO", "--task": "TASK", "--timeout": "SECONDS", "--whole-element": true}, "positionals": [], "stdin": null}
    Required options: ["--context", "--expected-sha", "--os", "--ref", "--repo-root", "--task", "--whole-element"]; positional arity: 0..0; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `ci-debug.source.instructions-analyze-ci.interface.analyze-ci@1` — Collect one bounded immutable workflow-history snapshot and report deduplicated green-to-green failures plus descriptive runtime hotspots without dispatching CI.
- `ci-debug.source.instructions-qualify-ci.interface.qualify-ci@2` — Coordinate structured local-branch qualification and evidence-bounded CI repair through optional target publication.
- `ci-debug.source.instructions-repair-element.interface.repair-element@1` — Repair and verify one assigned CI matrix element without integrating it or claiming overall CI success.
- `git-workflow.interface.default@1` — Check branch and ownership boundaries first, then perform only explicitly authorized and exactly scoped Git mutations.
<!-- END BLUEPRINT INTERFACES -->

# CI Debug

Route by requested outcome before taking any action:

- Historical failures, recurrence, timing, elapsed job-minutes, or hotspots:
  use `ci-debug.interface.analyze-ci`.
- Branch or exact-SHA qualification, full-matrix repair, or optional target
  publication: use `ci-debug.interface.qualify-ci`.
- One explicitly assigned matrix element: use
  `ci-debug.interface.repair-element`.

Mixed historical-analysis and mutating qualification requests require the user
to choose or explicitly authorize both operations. Never silently select one.

"Cost" means historical observed job-minutes unless the user says otherwise.
Monetary pricing is unsupported and must be reported as a gap. A controlled
benchmark is outside this skill and requires clarification. The words cost or
benchmark never authorize a workflow dispatch.

Analysis may create only the requested private evidence tree. It never
dispatches, cancels, reruns, repairs, commits, or changes Git or GitHub state.
Qualification and repair retain their own explicit mutation boundaries.
