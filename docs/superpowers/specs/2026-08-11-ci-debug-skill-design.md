# CI Debug Skill Design

## Purpose

Make CI repair efficient from a local machine by separating complete
certification from targeted debugging.

## Core algorithm

```text
candidate = exact pushed SHA

while run-ci(candidate) is red:
    failures = group the report by matrix element

    in bounded parallel, for each failing element:
        active = its failing selectors
        while active is not empty:
            diagnose and patch one failure class
            push the repair candidate
            report = run-targeted-tests(candidate, element, active)
            active = report.failures
            stop blocked if the same active set repeats without a relevant change

        run-targeted-tests(candidate, whole element)
        return the patch only when the whole element is green

    review and integrate returned patches sequentially
    candidate = exact pushed integrated SHA
```

Only `run-ci` can establish overall green.

## Interfaces

### Instruction interfaces

- `ci-debug.interface.default` owns the outer loop, parallel delegation,
  sequential integration, and final full-CI requirement.
- `ci-debug.interface.repair-element` owns the inner patch/targeted-test loop for
  one assigned matrix element.

### Machine interfaces

- `ci-debug._rtx.interface.run-ci` forwards a complete remote matrix request to
  the canonical repository runner.
- `ci-debug._rtx.interface.run-targeted-tests` forwards one matrix element plus
  one explicit selector, a previous report containing the active failure set,
  or a whole-element request.

The adapters add no CI policy. The repository runner owns suites, selectors,
matrix membership, OS support, workers, GitHub transport, correlation,
diagnostics, artifacts, and report schemas.

## Git and agent ownership

Repair subagents may work on isolated temporary branches when explicitly
authorized through `git-workflow`. They return commits, diffs, and test reports.
They do not integrate or clean branches. The coordinator integrates accepted
patches sequentially and reruns complete CI for the new exact SHA.

Machine interfaces never create worktrees or branches, commit, push, merge,
delete branches, or clean worktrees.

## Useful safeguards retained

- Exact SHA, not branch name, identifies tested code.
- Each repair subagent owns only one matrix element and evidence-backed path
  scope.
- A selected failure rerun is followed by a whole-element rerun.
- A whole-element pass is followed by complete CI after integration.
- The next active set comes from the latest targeted report; resolved failures
  are not retried.
- An unchanged failure set without a relevant change stops as blocked.
- Reports are evidence, not Git authority.

## Dependencies and rollout

The separate repository-runner plan must provide refined local selection plus
`remote matrix` and `remote probe`. Until those commands exist, the skill can
be structurally and locally tested but must fail closed for live remote work.

GitHub's manual workflow entry point must exist on the default branch before a
feature branch can use it. Land that bootstrap first, then implement the runner,
then exercise the skill against a temporary branch.

## Acceptance

- Full CI reports every failing matrix element.
- Independent elements can be delegated in parallel.
- A repair subagent can rerun only its assigned failures, then its whole
  element.
- The integrated exact SHA is accepted only after complete CI is green.
- No runner, transport, artifact, or persistent-state behavior is duplicated in
  the skill.
