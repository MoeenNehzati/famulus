# Boundaries Validator Performance Refactor

## Scope

Refactor `validators/skill/boundaries.py` for speed without changing its
conformance semantics, public `validate(repo_root)` API, finding text, or finding
order.

## Current bottleneck

The validator rebuilds three target-specific regular-expression strings for
every source line and every other skill. On the current repository this produces
about 3.95 million `re.escape` calls and 3.95 million regex searches. Reading the
217 candidate runtime files takes about 14 milliseconds and is not the
bottleneck.

## Design

Prepare three compiled repository-wide direct-path matchers from the complete
skill-name set before scanning files. Each matcher captures the referenced skill
name. For every relevant line:

1. Run the three prepared matchers.
2. Collect captured direct-path skill names and remove the source skill.
3. When the line is a `sys.path.insert` candidate, collect other skill names
   mentioned by the existing substring rule.
4. Walk other skills alphabetically. For each skill, prefer its direct-path
   finding, then its `sys.path.insert` finding, exactly matching the current
   nested-loop ordering.

The prepared matchers belong to one `validate` invocation. No pytest fixture is
introduced because this validator exposes one conformance item; its repetition
is inside the scan rather than across test items.

## Verification

Characterization tests must cover `_rtx`, `_cx`, same-skill references, multiple
cross-skill references and their ordering, and `sys.path.insert`. A preparation
test must show that matcher construction is independent of the number of source
lines.

The focused tests and full validator suite must pass. Five warm direct-validator
and repository-runner timings will establish the improvement and distinguish
suite work from fixed runner overhead.
