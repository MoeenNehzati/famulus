# Blueprint Used-Interface Injection Implementation Decisions

Implementation worktree: `feat/blueprint-used-interface-injection`, based on `9a16f37b`.

Status: implementation validation is complete. Final plan and documentation
review gates are green with no unresolved findings. The complete staged
candidate passed pre-commit with loopback socket capability.

This record documents decisions made while implementing
`2026-08-30-blueprint-used-interface-injection.md`. The implementation reports in
`.superpowers/sdd/2026-08-30-blueprint-used-interface-injection/` contain the full
command output and per-file measurement table; that directory is intentionally
git-ignored.

## Base and baseline

- The plan's prerequisite ownership gate is satisfied by `c96a492e`, which owns
  the existing `blueprints_from_graph()` and `validate_sync_state()` hunks.
- The initial pre-commit baseline produced 3,128 passes, 18 skips, 17 failures,
  and 4 errors. Every failure/error was in the Google OAuth loopback tests and
  arose from sandbox-denied socket creation. Focused in-scope tests therefore
  remained the implementation gate; the final pre-commit route must be run with
  loopback socket capability.

## Scope and budget rulings

### Task 2 exact exceptions and costs

| File | Final D/N/M | Ceiling D/N/M | Ruling and restoration cost |
|---|---:|---:|---|
| `skills/skill-maker/_rtx/_blueprint_syncer.py` | `314/0/3` | `298/0/8` | Keep `D +16`: the excess is only named obsolete contract/alternate-projection machinery, imports, the contract-first branch, and one unused local. Cost if wrong: restore and review the 16 excess deletion lines, reintroducing dead projection code until a narrower deletion is established. |
| `skills/skill-maker/_rtx/tests/test_blueprint_tools.py` | `244/0/2` | `238/0/5` | Keep `D +6`: every excess line belongs to tests of removed contract or alternate-projection surfaces. Cost if wrong: restore and review the six excess test deletion lines, together with any production surface they prove was still live. |
| `skills/skill-maker/blueprints/gateway.yaml` | `0/0/3` | `0/0/2` | Keep `M +1`: all three live phrases promised deleted contract-projection behavior. Cost if wrong: restore the one additional description replacement, leaving one live description stale until separately resolved. |

The Task 2 body-test name remains `test_generated_blocks_are_ignored` even though
its fixture now specifically proves legacy contract text is scanned. Renaming was
deferred because its `M=1` budget was consumed by the behavior assertion. Cost if
wrong: the broad name may mislead readers and should be renamed in a separately
budgeted cleanup; behavior is unaffected.

### Task 3 final rows, exceptions, and costs

These are the final Task-3-only rows measured against the approved Task 2
snapshot; they supersede the earlier interim rows in the task report.

| File | Final D/N/M | Ceiling D/N/M | Ruling and restoration cost |
|---|---:|---:|---|
| `skills/skill-maker/_rtx/_blueprint_syncer.py` | `94/0/12` | `60/5/14` | Keep production `D +34`: deleting `validate_gateway_declares_generated_dispatches()` and its call removes a syncer-local v5-only no-op for every v6 graph. Cost if wrong: restore exactly that isolated function and call, reintroducing the pre-v6 branch. |
| `skills/skill-maker/_rtx/tests/test_blueprint_tools.py` | `114/24/24` | `105/2/7` | Keep test `D +9`, `N +22`, and `M +17`: the row removes the complete v5-only syncer fixture/tests and retains the reviewer-required v6 public apply/check, manifest-idempotence, and absent-XDG-routing-state integration test. Cost if wrong: restore the isolated v5 helper/tests or remove the v6 integration test; the latter loses direct proof of the live public invariant. |
| `validators/skill/blueprints.py` | `1/0/1` | `2/0/3` | Within budget; no exception. |
| `tests/validate_blueprints.py` | `1/4/3` | `2/0/4` | Keep test `N +4`: explicit v5-skip and v6-call assertions protect the shared-migration/v6-sync boundary. Cost if wrong: simplify this one focused test and lose direct proof of the v5 skip branch. |

### Unbudgeted final-review fix round

The original plan did not budget this final-review round. The binding review
authorizes the smallest additional scope in
`skills/skill-maker/_rtx/_blueprint_syncer.py`,
`skills/skill-maker/_rtx/tests/test_blueprint_tools.py`,
`validators/skill/blueprints.py`, `tests/validate_blueprints.py`, this decision
record, and the schema-v6 standard closure recorded below. Cost if wrong: restore
only these final-review hunks; doing so reopens the public traceback, reversed
marker, stale-standard, or missing-evidence finding attached to each hunk.

This decision record is itself an unbudgeted new file at
`docs/plans/2026-08-30-blueprint-used-interface-injection-decisions.md`. It was
initially reviewed while untracked and was then staged in the complete candidate.
Cost if wrong: remove the record before commit, losing the durable budget, scope,
digest, and restoration-cost rationale collected here.

## Generated-artifact gate

- The read-only measurement found exactly 42 tracked generated skills, 730 old
  contract-block lines, 341 old interface-block lines, and 605 expected new
  interface-block lines. The reviewed and actual aggregate is
  `D/N/M = 1071/605/0`.
- The runtime dependency manifest measured and remained `0/0/0`.
- The exact worktree's public dispatcher interface was invoked once in mutating
  mode after the measurement gate. Its final public `--check` passes.
- Final generated semantics cover 131 direct gateway targets: 100 process-bound,
  31 instruction-only, and 13 zero-use skills. Generated labels include pinned
  versions and descriptions; dispatcher targets remain versionless. Descendant
  exports and transitive uses are absent.
- Handwritten skill bodies are byte-identical before and after generated-span
  replacement.

## Post-measurement corrections

- Existing-marker synchronization performed a global triple-newline collapse
  after marker-bounded replacement. That made byte-preserving bodies and public
  `--check` mutually incompatible for two skills. The existing-marker path now
  returns the marker-span substitution directly; frontmatter insertion retains
  its prior normalization. A focused regression proved the failure before the
  one-line repair and exact prefix/suffix preservation afterward. The regression
  adds eight test lines beyond Task 1's original test budget.
- Two live descriptions omitted from the original file table were corrected:
  - `skills/skill-maker/blueprint.yaml`: one unbudgeted `M=1` replacement,
    `generated blueprint-contract synchronization` to `generated blueprint
    interface synchronization`. Cost if wrong: restore exactly that description
    line, which reintroduces the stale contract term.
  - `skills/skill-maker/_rtx/blueprints/rtx-blueprint-syncer.yaml`: one `M=1`
  replacement, `generated blueprint blocks` to `generated skill interface
    blocks`; the Task 2 row changes from `4/0/10` to `4/0/11`, still within its
    `6/1/12` ceiling. Cost if wrong: restore exactly that description line, which
    reintroduces the underspecified generated-block promise.

## Schema-v6 standards closure

The final review authorized interface-only terminology corrections in three
canonical authorities. Stable item IDs, link keys, `standard_version` values,
frozen migration material, and other-domain policy remain unchanged. Raw-file
SHA-256 pins were propagated through every direct dependent.

`references/node-standards/authority-disposition.yaml` also pins the replacement
digest for `refactoring.standard.yaml`. It is updated from `33eaf4fe...` to the
revision-7 digest `755593fe...`. Cost if wrong: restore that one digest with the
entire refactoring-standard closure; restoring it alone would make the live
mechanical authority assertion stale.

The same disposition moves
`skill-guidelines.canonical-blueprint-ownership.requirement-004#source-block`
from the exact-unchanged legacy-leaf set to `rewritten_same_id_leaves`, reducing
the exact set from 45 to 44 and recomputing its digest as
`4921ff67b4d26bee9663f3fd5d2b0c116b59350beecdb5db955d9f24999a3f0d`.
Cost if wrong: restore
that audit entry, count, and digest together with the old contract/interface
wording; changing only the audit would misstate source fidelity.

| Standard | Revision | Final SHA-256 | Decision and restoration cost |
|---|---:|---|---|
| `references/node-standards/refactoring.standard.yaml` | `6 -> 7` | `755593fe48002bc32880cda05267086aa82ce4ccef292e4b9d5781f9f8af5af1` | Replace only stale generated contract-artifact prose with interface-artifact prose. Cost if wrong: restore eight terminology lines and revision 6, then recompute every downstream pin below. |
| `references/node-standards/node.standard.yaml` | `14 -> 15` | `440bc973b3c53e07fea4285897cf3d6ea21f64712fd3e4a829d7772dde0d23d4` | Pin refactoring revision 7 and its digest. Cost if wrong: restore this one import pin/revision and all node-dependent pins. |
| `references/node-standards/module.standard.yaml` | `14 -> 15` | `c09c9832542dad3b02c1215ededb95272df0d542b713997add3b3cc6666886da` | Pin node revision 15 and its digest. Cost if wrong: restore this pin/revision and its instruction/Python module dependents. |
| `references/node-standards/behavioral-source.standard.yaml` | `14 -> 15` | `6f11fec540ace7152e947f4ef3b418737f3d7232a3a75d9bfef4aed0cdf09e22` | Pin node revision 15 and its digest. Cost if wrong: restore this pin/revision and its instruction/Python behavioral-source dependents. |
| `references/node-standards/instruction-node.standard.yaml` | `15 -> 16` | `6f02166b1af1133ef940eead9d7e00a1020cf593d52eefd5d3a433a83111e3b0` | Pin node revision 15 and its digest. Cost if wrong: restore this pin/revision and both instruction-standard dependents. |
| `references/node-standards/python-node.standard.yaml` | `18 -> 19` | `bd735b44f9f4155c94d185a23aa0f7ed8780fc2f1929c2bbbc4f457b3afe62c5` | Pin node revision 15 and its digest. Cost if wrong: restore this pin/revision and both Python structure dependents. |
| `references/node-standards/instruction-module.standard.yaml` | `18 -> 19` | `7cfd90f85b2997019ea23781ba829e80b9123477de6a22932eca1ba8945378bc` | Replace the stale generated contract-block promise and pin refactoring 7, module 15, and instruction-node 16. Cost if wrong: restore one terminology assertion plus three pins and revision 18. |
| `references/node-standards/instruction-behavioral-source.standard.yaml` | `16 -> 17` | `6da8140fb7a8a2b5d5198d79e1d96f96390d169f01f0e6b39fc60e47987e27c7` | Replace only generated contract/block wording, align the existing test-coverage description, and pin behavioral-source 15 plus instruction-node 16. Cost if wrong: restore five terminology/evidence lines plus two pins and revision 16. |
| `references/node-standards/python-module.standard.yaml` | `20 -> 21` | `5c09c12dfcf70fffbd10987578a3a45c9fe436301f9a9ffc7501db6606638679` | Pin module 15 and python-node 19. Cost if wrong: restore two pins and revision 20. |
| `references/node-standards/python-behavioral-source.standard.yaml` | `19 -> 20` | `f2b133569553bc045529442f53222162876bba1a6a043af2eb6de61e634f0463` | Pin behavioral-source 15 and python-node 19. Cost if wrong: restore two pins and revision 19. |

`tests/test_skill_refactoring_standard.py` receives unbudgeted expectation-only
updates for the corrected live titles and prose. Cost if wrong: restore the four
expectation strings together with the refactoring terminology; restoring tests
alone would make them assert stale policy.

`tests/fixtures/standards/skill-refactoring-source-map.yaml` receives two
unbudgeted supersession entries for the stable refactoring target IDs. The
immutable legacy source text and its digests remain unchanged. Cost if wrong:
remove those two disposition entries, which makes the fidelity check require the
canonical authority to repeat obsolete contract-block terminology.

The repository registers generated Markdown only for
`references/document-standards/document-profile.standard.yaml`; none of the ten
documents in this closure has a registered view, so no view was regenerated.
No source or source-unit evidence changed, so no source digest was changed.

## Review and validation status

- Each task received an independent specification-and-quality review. Task 1 and
  Task 3 required one fix/re-review round; Tasks 2 and 4 were approved directly.
- Task 4 evidence at its review point: 102 focused tests passed, the exact-worktree public sync
  check passed, the selected blueprint validator hook passed, and
  `git diff --check` was clean.
- The public dispatcher emits a non-failing
  `certification-status-unavailable` advisory because precomputed certification
  status is unavailable; synchronization and validation still exit successfully.
- Final-review RED reproduced two uncaught `BlueprintError` tracebacks and one
  accepted reversed interface-marker pair; the corrected focused selection then
  passed all 7 collected cases. Only `BlueprintError` is converted to diagnostics,
  a focused `RuntimeError` case still propagates, and a separate validator
  integration case returns the syncer's authoring diagnostic.
- All 10 standards in the revision/digest closure passed direct schema-v6
  validation. The refactoring/fidelity and repository-validator tests passed
  `19/19`; the registered `repo/standard_documents` validator passed.
- The historical eight-selector 102-test matrix now collects 109 cases after the
  seven final-review additions; both direct pytest and the repository `tests:shared`
  route passed `109/109`.
- The exact worktree-bound public sync interface passed `--check`, the selected
  `skill-maker/blueprints` repository validator passed, and `git diff --check`
  remained clean.
- An unstaged full pre-commit run outside the socket-restricted sandbox produced
  `3149 passed, 18 skipped, 1 warning`, but the hook's staged-view semantics meant
  it validated the base rather than this candidate. The exact complete candidate
  was subsequently staged and rerun after the standards closure and integration
  fixes: `3158 passed, 18 skipped, 1 warning` in 36.33 seconds of pytest time
  (39.55 seconds for the repository test task). The sole warning is the existing
  multiprocessing fork deprecation in
  `tests/test_officina_git_provenance.py::test_fifo_replacement_returns_without_blocking`.

## Unbudgeted staged-integration fix round 2

The controller's exact fully staged pre-commit exposed six integration failures.
This round is not covered by the original task budgets. It is authorized as the
smallest expectation and validator-boundary correction set below; no canonical
standard, generated skill block, blueprint declaration, runtime manifest, or
digest changes in this round.

| Path | Unbudgeted ruling | Restoration cost if wrong |
|---|---|---|
| `skills/email-triage/tests/test_llm_routing.py` | Replace the deleted no-dispatch expectation with exact direct gateway-interface coverage for ten process routes and the one retained instruction-only triage route. | Restore the old nine-line expectation hunk; this would make the test reject the approved interface-only projection and stop checking the actual process commands. |
| `tests/test_officina_blueprint_graph.py` | Assert the generated doctor target, description, and dispatcher command, and assert the module's own unused `diagnose` export is absent from the block. | Restore the two old assertions; this would reintroduce the false same-module-export promise and discard command-level projection coverage. |
| `tests/test_standard_extractor.py` | Update the one exact Python-module document revision expectation from 20 to the already-closed revision 21. | Restore one value; the extractor test would again reject the canonical revision/digest closure without changing that authority. |
| `tests/test_migrated_standards_fidelity.py` | Replace the stale v5 hook expectation with live module/source ownership and generated interface-block synchronization checks, while retaining negative legacy-family and dependency-hook assertions. | Restore the old name/assertion hunk; the fidelity test would again require obsolete v5/generated-contract prose. |
| `tests/validate_skill_md_dispatch.py` | Keep parent prompt-export discovery assertions, but require get-weather's exact directly used process target, description, and command and exclude its own unused prompt export from the generated block. | Restore the old three assertions; this would reject the approved direct-use projection and stop checking its executable guidance. |
| `validators/platform_neutral.py` | For files named `skills/**/SKILL.md`, scan `strip_generated_blueprint_blocks(text)` instead of raw text. The helper preserves line numbers and removes only a balanced marker-bounded generated interface block; frontmatter and all authored body text remain governed. | Restore the projection import/call and its documentation; generated dispatcher arguments such as `<claude\|codex>` would again produce false positives in the authored-prose validator. |
| `tests/validate_platform_neutral.py` | Add one strict test with the identical platform-bearing dispatcher line inside and outside the generated block; only the authored line may be reported. | Remove the focused test; a future broad SKILL.md exemption or regression to raw generated-block scanning would be unprotected. |

The decision record and ignored final-fix report remain unbudgeted evidence
artifacts. The decision record remains untracked and intentionally unstaged in
this subagent round. Cost if wrong: omit it at controller staging time and lose
the durable scope/restoration rationale for both final-review fix rounds.

The controller staged the complete candidate, including this decision record,
and the exact staged pre-commit route passed with the result recorded above.
Final plan and documentation reviewers approved the candidate with no unresolved
findings; the candidate was ready for its coherent commits.
