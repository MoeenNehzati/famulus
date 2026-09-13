# Optimizing Code Tests

This is the operational companion to the canonical
[Code Test Design and Performance Standard](../../references/node-standards/code-testing.standard.yaml).
The standard owns requirements, diagnostic smells, and remedies. This guide
owns the repository-specific audit and measurement workflow.

Use it only when selected work touches executable test files or their direct
fixtures or helpers and intends to reduce test runtime or repeated work. Query
the standard with `task.optimizes-test-performance=true`. Correctness-only test
work queries the same root with the fact set to `false`.

## Objective

Reduce enclosing-harness wall time and aggregate work without losing unique
behavioral evidence, isolation, failure behavior, or enforced gate ownership.
Worker-count tuning is not a substitute for removing repeated computation.

## Before editing

Record one compact evidence row:

```text
Test node or file:
Supported contract and retained owner:
Initial state, action, and exact observable:
Physical setup: repository | graph | schema | scan | copy | subprocess | other
Serial and target-worker measurements:
Host, revision, repository projection, capabilities, and competing processes:
Proposed remedy and predeclared materiality threshold:
```

If ownership or support status is unclear, retain the test and record the gap.
Do not edit until the expected eliminated work is large enough to clear the
declared threshold after copying, serialization, locking, and isolation costs.

## Public commands

Run focused tests through the repository runner:

```bash
python3 repo_checks.py --task tests:shared --jobs 1 \
  --repository-view working --selector TEST_NODE
python3 repo_checks.py --task tests:shared --jobs 8 \
  --repository-view working --selector TEST_NODE
```

Measure an equivalent precommit selection with eight workers:

```bash
python3 repo_checks.py --suite precommit --jobs 8 \
  --repository-view working --timing-output TIMING.json
```

Use `--repository-view staged` when validating the exact hook projection. Do
not compare working and staged projections as if they were the same benchmark.

## Decision order

Apply the standard's remedies in this order when reducing existing cost:

1. Delete only evidence proven obsolete or completely owned elsewhere.
2. Consolidate compatible assertions consuming the same expensive state.
3. Narrow setup to the lowest stable layer owning the observable.
4. Reuse immutable preparation with private mutable copies.
5. Move an exact broad integration only when a fast canary remains and a
   slower enforced gate mechanically selects the original evidence.
6. Retain unique physical-boundary evidence.

Prefer eliminating repository builds, graph/schema loads, scans, copies, and
processes over reducing the displayed test count.

## Smell routing

Start from the requirements query's `context_index`, select the exact family,
then request its context and remedies. Common routes are:

| Observed problem | Standard ref |
| --- | --- |
| Whole repository or process for a local claim | `code-testing.smells.boundary-inflation` |
| Duplicate live validator or weaker evidence | `code-testing.smells.duplicate-evidence-owner` |
| Repeated repository, graph, schema, parse, or scan setup | `code-testing.smells.repeated-preparation` |
| Shared mutable state, drifting fixture, or unsafe cache | `code-testing.smells.unsafe-reuse` |
| Cleanup only on success | `code-testing.smells.failure-unsafe-lifecycle` |
| Broad copy, pointless matrix, or excessive concurrency | `code-testing.smells.overbroad-input-space` |
| Unsafe token/cache/pre-parse shortcut | `code-testing.smells.unsafe-fast-path` |
| Stale hash, graph, projection, or index state | `code-testing.smells.stale-derived-state` |
| Empty result can pass without execution | `code-testing.smells.weak-execution-proof` |
| Flaky, sleeping, hanging, or order-dependent execution | `code-testing.smells.nondeterministic-or-unbounded-execution` |
| Contaminated benchmark or wall-only claim | `code-testing.smells.invalid-performance-claim` |
| Broad integration inside the fast gate | `code-testing.smells.misplaced-broad-integration` |

Do not load the full standard when a selected context/remedy projection answers
the decision.

## Repository-specific cautions

- Pytest module and session fixtures are per xdist worker. Count physical setup
  under the target worker count instead of inferring reuse from fixture scope.
- A live validator collected by `repo_checks.py` is already the canonical live
  owner. Do not add an ordinary `validate(REPO_ROOT) == []` duplicate.
- Keep exact Git bytes, index/tree modes, signing provenance, process transport,
  races, signals, file descriptors, and browser behavior physical when the
  assertion observes that boundary.
- Stop setup at an injected or mocked boundary. A pure projection test should
  not initialize Git or signing after the public adapter has separate evidence.
- Immutable templates must reproduce behavior-relevant bytes, modes, identity,
  branch, symlink shape, encoding, environment, and schema. Mutating consumers
  receive independent copies.
- Published or cross-worker snapshots use trusted run-private storage, exact
  provenance, atomic publication, corruption/failure replay, and direct-run
  fallback. Never deserialize a user-controlled cache.
- Shortcuts remain fail closed for Unicode normalization, decoding, syntax,
  discovery, aliases, multiline input, and ambiguous tokens.
- Concurrency uses barriers, hooks, events, or observed state plus bounded
  completion. Arbitrary sleep alone is not contention evidence.

## Benchmark acceptance

Hold these constant or record the difference:

- code/build revision and selected tests;
- execution mode, worker count, and input corpus/projection;
- capabilities, toolchain, platform, and host state;
- competing test or benchmark processes.

Record enclosing-runner wall, pytest wall when distinct, aggregate item/file
work, outcomes, and raw timing output. Use repeated comparable runs when
variance is material. Reject a timing comparison when unchanged files slow
systemically. A socket or browser capability denial is environment evidence,
not a product regression; rerun under the required capability.

## Verification ladder

1. Exact changed tests.
2. Fixtures/helpers and neighboring consumers.
3. Canonical validator, adapter, or physical-boundary owner.
4. Focused serial execution.
5. Focused target-worker execution.
6. Before/after collected test identities and parameter/contract map.
7. Gate-selection checks for moved evidence.
8. `git diff --check` and focused formatting/standard validation.
9. Full target gate at the cleanup-wave boundary.

Independent review must compare semantic ownership and failure behavior, not
only green output. Review mutable isolation, fixture fidelity, accumulated
mutations, permissive assertions, cache provenance, capability attribution,
and whether the measured benefit clears the declared threshold.

## Further evidence

Read the [repository testing guide](../testing.md) for suite, projection, and CI
contracts. Read the
[2026-08 cleanup retrospective](../history/2026-08-code-test-performance-cleanup.md)
only when historical measurements, rejected approaches, or reusable facilities
are relevant.
