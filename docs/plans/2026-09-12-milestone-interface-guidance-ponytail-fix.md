# Milestone interface guidance ponytail fix

## Problem

The generated milestone interface block exposes ambiguous placeholders such as
`STEP`, so an agent can supply a semantic label where Dispatcher requires a
non-negative integer. The same projection gap exists for `ATTEMPT`, run IDs,
roles, and slow thresholds. Separately, the shared evidence contract does not
explain that direct argv accepts repeats while the Dispatcher route accepts at
most one path per invocation.

## Minimal durable change

1. Keep the shared projection generator unchanged. Make the milestone-owned
   `usage` templates self-describing:
   - `ROLE` -> `NONEMPTY_ROLE`
   - `ID` and `RUN` -> `SAFE_RUN_ID`
   - `STEP` and `ATTEMPT` -> `NON_NEGATIVE_INTEGER`
   - `SECONDS` -> `POSITIVE_DECIMAL_SECONDS`
2. Make the canonical writer contract machine-true:
   - add `minimum: 0` to `step` and `attempt`;
   - describe the exact safe run-ID alphabet and 64-character limit;
   - keep evidence as a list with its empty-list default, and document that the
     Dispatcher accepts zero or one path per invocation while direct argv
     accepts up to 20 repeats.
3. Regenerate only `skills/milestone-logging/SKILL.md` from its blueprints.
4. Extend the existing generated-interface tests with the corrected milestone
   labels and singular Dispatcher evidence shape.

## Owned files

- `skills/milestone-logging/_rtx/blueprints/rtx-milestone-writer.yaml`
- `skills/milestone-logging/_rtx/blueprints/rtx-agent-timeline.yaml`
- `skills/milestone-logging/SKILL.md` (generated)
- `skills/skill-maker/_rtx/tests/test_blueprint_tools.py`
- `tests/test_famulus_mcp.py`
- this plan

## Preservation map

- Preserve all nine public interface IDs, versions, access rules, process
  entries, flags, positionals, outputs, effects, and error behavior.
- Preserve the shared evidence list contract and direct-writer support for up
  to 20 repeated `--evidence` flags; state the public Dispatcher's zero-or-one
  ceiling explicitly.
- Preserve the existing positive-decimal `--slow` grammar and safe run-ID
  grammar; change only how callers are told to populate them.
- Do not change the shared generator, Dispatcher compiler, registration,
  runtime parser, or unrelated documentation.

## Green gate

- Independent subagent audits find no material contract, projection, or test
  blocker.
- Generated milestone guidance contains the new labels and none of the
  ambiguous `ROLE`, `ID`, `RUN`, `STEP`, `ATTEMPT`, or `SECONDS` values in the
  affected positions.
- Valid numeric values compile; semantic or negative step/attempt values fail
  before execution.
- Zero or one Dispatcher evidence path compiles, repeated Dispatcher evidence
  remains explicitly unsupported, and direct repeated evidence remains intact.
- Focused milestone, projection, MCP contract, blueprint, and generated-artifact
  checks pass.

## Exclusions

- No global contract-description projection.
- No repeatable-flag framework.
- No fix for historical stale-session `interface_not_found`; current
  registration is healthy, and that lifecycle issue requires separate evidence.
