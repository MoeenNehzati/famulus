# Setup interface manager interactive experiment

## Scope and evidence boundary

This campaign tests the setup manager through fresh Codex sessions in task-owned isolated homes. It keeps two evidence lanes separate:

- **Production lane:** the shipped Famulus plugin at the exact tested commit. This lane may establish behavior only for the single production managed canary, `milestone-logging.interface.setup@1`.
- **Synthetic fixture lane:** the same commit plus a reviewed, isolated-only `A -> B -> C` and `D -> C` fixture overlay. This lane tests deep and shared dependency mechanics, but it is not evidence that those fixture skills ship in release one.

Scripted tests diagnose and reproduce failures; they never substitute for a fresh interactive rerun. Raw transcripts and secret-bearing artifacts remain under the mode-`0700` task root. This report contains only redacted evidence.

The committed [evidence appendix](setup-interface-manager-interactive-evidence.md) preserves the exact redacted command templates, representative structured payloads and ledgers, final synthetic overlay identity, and complete overlay file list needed to repeat or audit the campaign after temporary raw evidence is removed.

## Installation and isolation manifest

| Item | Tested value |
|---|---|
| Base implementation | `458a2038cf97f320b38020459cfe980bd9ebc12c` for the initial production install; later plan-only commit `a46f7f68` does not alter runtime files |
| Codex | Standalone `codex-cli 0.152.0`, installed under the task root with the official installer |
| Plugin | `famulus@nullkit` version `0.0.0`, installed from the local isolated-worktree marketplace snapshot |
| Task root | Private temporary directory, redacted below as `<TASK_ROOT>` |
| Codex home | `<TASK_ROOT>/codex-home` |
| Selected plugin data | `<TASK_ROOT>/codex-home/plugins/data/agent-plugins/<plugin-id>`; the optional `FAMULUS_PLUGIN_DATA` hint was not the selected persistence root |
| Project | `<TASK_ROOT>/project` |
| Normal user state | Normal Codex home, plugin cache, plugin data, and setup ledger are outside the lane and must remain unchanged |

The official Codex CLI documentation supplies the standalone installer, and the official plugin documentation requires installation followed by a fresh session. The campaign downloaded the installer first, then executed it with task-specific `CODEX_INSTALL_DIR` and `CODEX_HOME` values so the script could be inspected and the lane recorded. The installer unexpectedly appended `<TASK_ROOT>/bin` to the normal `.bashrc`; the controller removed that exact three-line installer block immediately and verified it was absent. This was an experiment-setup side effect, not a Famulus product result.

Only `auth.json` was copied into the isolated Codex home, with mode `0600`; its contents were never placed in evidence. `codex doctor` confirmed the isolated executable, home, auth, cache, and state paths. Its network checks failed inside the controller sandbox, so user-facing sessions are run host-capably while retaining Codex's own workspace sandbox.

## Scenario inventory

| ID | Lane | Scenario | Status |
|---|---|---|---|
| P00 | Production | Installation and isolation preflight | passed |
| P01 | Production clone | Bootstrap with MCP unavailable | passed |
| P02 | Production | Generic setup prose is inert | passed |
| P03 | Production | Unmanaged target executes without receipt, claim, or flow mutation | passed |
| P04 | Production | First-use managed trigger suggests setup and stops | passed |
| P05 | Production | Follow suggested setup and resume once | passed |
| P06 | Production | Persist readiness across a fresh session | passed |
| P07 | Production | Exact setup and teardown calls redirect | passed |
| P08 | Production | Manager and ledger redact original arguments | passed |
| P09 | Production | Original request resumes exactly once | passed |
| F00 | Synthetic | Validate and install isolated deep/shared fixture | passed |
| F01 | Synthetic | Deep `A -> B -> C` setup from an owned child | passed |
| F02 | Synthetic | Duplicate call receives the existing busy flow | passed |
| F03 | Synthetic | Interrupted action recovers verifier-first | passed |
| F04 | Synthetic | Cancel clears ghost claims and retains verified prefix | passed |
| F05 | Synthetic v2 | Stale `B` reruns only dependent suffix | passed |
| F06 | Synthetic | Explicit invalidation removes live dependent suffix | passed |
| F07 | Synthetic | Shared `C` claims survive first-root teardown | passed |
| F08 | Synthetic | Malformed ledger fails closed and redacts | passed |
| F09 | Synthetic | Reverse teardown never resumes an ordinary call | passed |

The private `campaign.json` indexes every scenario and records evidence-format exceptions. Interactive scenarios normally retain a context-free card, fresh thread, action/result evidence, state comparison, verdict, and lesson. P09 is intentionally a read-only derived verdict with no new worker. P01–P03 were run before the full evidence template was enforced; their missing contemporaneous artifacts are disclosed rather than reconstructed.

**Campaign outcome: 20 of 20 scenarios passed.** P00–P09 establish the shipped single-canary behavior. F00–F09 establish deep, stale, interrupted, shared, corrupted-state, and reverse-teardown behavior only for the isolated synthetic overlay. No production product defect remained after the campaign; discarded attempts and fixture defects are classified below.

## Production-lane results

### P00 — installation and isolation preflight

**Pipeline.** Install current Codex under `<TASK_ROOT>`, copy only authentication into the isolated home, add the worktree as a local marketplace, install `famulus@nullkit`, then inspect versions, paths, modes, plugin inventory, runtime hashes, and ledger presence without starting a user-facing worker.

**Observed.** Codex installed as standalone version `0.152.0`; the isolated plugin inventory contained only enabled `famulus@nullkit` version `0.0.0`, sourced from the implementation worktree. The isolated auth file was mode `0600`, the task root was mode `0700`, and no isolated `setup/status.json` existed after inspection. SHA-256 pairs for `mcp_server.py`, `_setup_manager.py`, `_setup_state.py`, and `_setup_evaluation.py` matched between the tested worktree and installed cache. The normal setup ledger baseline was mode `0600`, 53 bytes, with digest `042cf60a...f78a27`; only its digest and metadata were recorded.

**Capability boundary.** Sandboxed `codex doctor` verified local paths and auth but could not reach ChatGPT endpoints. That is a controller-sandbox network limit. Interactive workers therefore require a host-capable outer launch while Codex retains its own workspace sandbox.

**Post-lane isolation check.** The normal setup ledger retained its initial mode, size, and digest after P09. Because unrelated long-lived MCP processes existed and writer quiescence was not established, this comparison is supporting evidence rather than a conclusive global non-mutation proof. Worker-created cache, session, ledger, lock, milestone, and evidence paths all resolved below the task root.

**Verdict: PASS.** The exact production runtime and isolated persistence boundary are reproducible, inspection created no manager ledger, and the installer-added normal shell-profile block was removed and verified absent before continuing.

**Lesson.** Verify both the requested install target and shell-profile files after standalone installation; an isolated install directory alone is insufficient proof of profile isolation.

### P01 — bootstrap with MCP unavailable

**Pipeline.** Install the same plugin into a disposable isolated Codex home. Put a reviewed task-owned `python` shim first in that lane's `PATH`; it delegates hook execution to the selected real Python but returns exit 86 only for the packaged `mcp_server.py`. Start one context-free Codex worker and ask it to report readiness, propose only supported recovery, request permission, and stop.

**Observed.** Fresh thread `01a05c74-...c98fe` identified Famulus as not ready because MCP tools were unavailable. It proposed `famulus:setup-python-environment`, described its non-mutating preflights and bounded package repair, and asked whether it should run the route. It performed only two read-only inspections of installed recovery guidance. The lane-local plugin-data directory remained absent, so no manager ledger or repair mutation occurred.

An initial worker thread was aborted after outer-sandbox network denial and was neither resumed nor used as product evidence. The host-capable rerun used a new thread.

**Verdict: PASS.** Bootstrap failure remained unmanaged, recovery was actionable and permission-gated, and no setup receipt was fabricated.

**Lesson.** Preserve aborted capability-limited sessions in the evidence index, but evaluate only a fresh host-capable thread; never resume a partially initialized worker for clean-room evidence.

### P02 — generic setup prose is inert

**Pipeline.** Start a fresh context-free worker and ask it to discuss generic Famulus setup and teardown at a high level without naming or invoking a managed target. Compare the isolated manager ledger before and after the thread.

**Observed.** Fresh thread `01a05c77-...230f` answered without a skill, tool, or manager call. The isolated setup ledger was absent before and after the thread.

**Verdict: PASS.** Ordinary setup language did not trigger managed setup.

**Lesson.** Trigger evidence must begin with a concrete managed target; prose containing the word “setup” is not a lifecycle signal.

### P03 — unmanaged target executes without receipt, claim, or flow mutation

**Pipeline.** In a fresh worker, call the installed `famulus.invoke` getter for an unmanaged interface and require the worker to stop if that MCP surface is unavailable. Compare manager state before and after the call, distinguishing canonical empty-ledger initialization from receipts or active-flow claims.

**Observed.** Fresh evaluated thread `01a05c82-...e1eb` tried three caller identities through the installed MCP surface: `codex` was not a module, `common` was not a valid host caller, and discoverable `setup-interface-manager` succeeded. Only the final attempt launched the unmanaged getter, returned a path inside the isolated lane, and invoked no manager or lifecycle interface. MCP startup created only the canonical empty ledger; it added no receipt, claim, or active flow.

Two discarded harness iterations remain in the raw evidence. The first lacked the selected Python in `PATH`, so the worker improperly bypassed the missing MCP surface with a direct import. The controller removed that task-owned empty ledger and lock, tightened the card to prohibit bypasses, and restored the absent-ledger baseline used by P04. This cleanup was observed by the controller but was not captured in a contemporaneous action log. The second exposed the real MCP tool but launched Codex with approval policy `never`, so the safe call was denied. The final rerun used a new thread and the intended approval policy.

**Verdict: PASS.** An unmanaged invocation completed normally and changed no setup lifecycle state beyond canonical empty-ledger initialization.

**Lesson.** A clean interactive harness must verify both tool discovery and approval policy before treating a worker result as product evidence; direct-import substitutes are nonconforming.

### P04 — first-use managed trigger stops for permission

**Pipeline.** Start a fresh worker with an empty production setup state and request one role-labelled milestone containing nonce `P04`. Explicitly withhold setup permission and require the worker to explain any proposed setup and stop before beginning it.

**Observed.** Fresh thread `01a05c86-...c56d` made one non-mutating manager status call for the ordinary record target. It received `setup_required`, no flow ID, root `milestone-logging.interface.setup`, and exactly one pending Markdown step. It explained that step and stopped. It made no `begin`, `run-markdown`, `settle`, `authorize`, direct setup, or original record call. The ledger remained absent and the isolated milestone directory contained zero `P04` occurrences.

**Verdict: PASS.** The first use produced an actionable setup suggestion, did not assume permission, and performed neither setup nor the original side effect.

**Lesson.** A permission-boundary test should assert both halves: a structured pending route must be visible, while the active flow and original side effect remain absent.

### P05 — follow setup and resume once

**Pipeline.** In a new thread, request a role-labelled milestone containing a fresh random nonce. Grant permission only for the managed setup suggested for that request, require every structured manager step, then recheck readiness, authorize, and resume the original request once.

**Observed.** Fresh thread `01a05c89-...c021` followed the started-call sequence `status(setup_required) -> begin -> run-markdown -> logging-path getter -> idempotent mkdir -> settle(ready) -> status(ready) -> authorize -> record`. Settlement created the version-1 receipt claimed by `milestone-logging.interface.setup`; the ledger ended with `active_flow=null`. All lifecycle responses had `resume_original=false`, while the single authorization had `resume_original=true`. Exactly one milestone file contained nonce `P05-ceed5c96-...e3ed`, and no teardown or unrelated repair ran.

The run also corrected an isolation assumption: Famulus selected persistent plugin data below the isolated Codex home at `plugins/data/agent-plugins/<plugin-id>`. The separately supplied `FAMULUS_PLUGIN_DATA` value was only an unused hint. All evaluated state remained confined to the task root.

**Verdict: PASS.** Written setup instructions were executable, settlement recorded readiness only after verification, and authorization resumed the original side effect exactly once.

**Lesson.** Resolve and record the getter-selected persistence directory; environment hints are not evidence of the path the installed runtime actually used.

### P06 — persistence across a fresh session

**Pipeline.** Stop the P05 worker, retain its ledger without copying or editing it, and start a new thread with a fresh nonce. Request an ordinary milestone and instruct the worker not to repeat setup when persisted verified state is ready.

**Observed.** Fresh thread `01a05c8f-...1592`, distinct from P05, ran `status(ready) -> authorize(resume_original=true) -> record`. It made zero `begin`, `run-markdown`, `run-python`, or `settle` calls. The nonce occurred once. The setup ledger was byte-identical before and after, including the version-1 receipt, root claim, and `active_flow=null`.

**Verdict: PASS.** A fresh process and thread reused persisted readiness, authorized normally, and did not rerun setup.

**Lesson.** Persistence evidence needs a new thread plus a byte-identical ledger comparison; a second action in the original worker would test only in-process reuse.

### P07 — exact setup and teardown calls redirect

**Pipeline.** Run two independent fresh workers. Each invokes exactly one installed lifecycle interface—setup or teardown—with a unique argument canary, then stops after the first structured response without following the returned route.

**Observed.** Setup thread `01a05c94-...6f00` and teardown thread `01a05c95-...2572` each made exactly one `famulus.invoke` call and received `setup_managed`. The responses identified the correct operation, root, and original interface and supplied the exact manager `begin` route. Neither thread called `begin` or launched a lifecycle action. The mode-`0600` ledger remained byte-identical before, between, and after the threads. Canaries appeared only in the private cards and raw invocation events, not in manager results, final responses, or ledger snapshots.

**Verdict: PASS.** Direct lifecycle exports were intercepted and redirected without execution or persistence mutation.

**Lesson.** Test direct setup and teardown independently: a shared classifier can still construct operation-specific routes incorrectly.

### P08 — manager and ledger redact original arguments

**Pipeline.** Put a unique canary in a valid ordinary record option value, run the record once in a fresh thread, then scan the exact manager results, ledger, diagnostics, worker state, final response, and report surfaces. Exclude and name the private prompt, raw transcript, original record result, and milestone output, where the requested content is expected to appear.

**Observed.** Corrected thread `01a05c9d-...3f9d` ran `status(ready) -> authorize(resume_original=true) -> record` with no setup lifecycle calls. The ledger was mode `0600` and byte-identical; one milestone line was appended. The canary had zero occurrences across every promised manager, persistence, diagnostic, and redacted reporting surface.

The first card placed a canary in `--task` without the required `--run`, producing one failed original call and no side effect or ledger mutation. It is retained as an experiment-design error. The corrected card used the canary as a valid `--role` value in a new thread.

**Verdict: PASS.** Original arguments were absent from the manager and ledger surfaces covered by the redaction contract, while the ordinary authorized action still ran once.

**Lesson.** Validate canary placement against the target interface before launching; an invalid original request tests argument validation, not redaction during successful execution.

### P09 — original request resumes exactly once

**Pipeline.** Independently re-evaluate the frozen P05 timeline and side-effect evidence; launch no new product action. Count launches before and after authorization, every lifecycle resume flag, and the nonce across isolated milestone records.

**Observed.** The pending phase launched zero original calls. `begin`, `run-markdown`, and `settle` each returned `resume_original=false`. Exactly one later authorization returned `resume_original=true`, followed by exactly one successful original record call. The P05 nonce appeared exactly once across the isolated milestone records.

**Verdict: PASS.** One authorization produced one subsequent original side effect, with no premature or duplicate resumption.

**Lesson.** Exactly-once is best reviewed as a derived verdict over frozen end-to-end evidence, so the review itself cannot create a second side effect.

## Synthetic-fixture results

The synthetic lane is not release-one feature evidence. It uses the production setup manager, MCP preflight, traversal, persistence, dispatcher, and generated-gate machinery from base commit `a46f7f68`, plus Task-10-only fixture commits: validated v1 `0ce25745` and final v2 `5968dfed`. Its graph is `A -> B -> C` plus `D -> C`; A's ordinary probe is an owned child. Fixed setup/teardown actions use getter-selected marker paths, read-only verifiers return only lifecycle booleans, and probes count a hash of a caller-held nonce outside the manager ledger.

The validated v1 overlay is commit `0ce257451c14488dff489bbbda83d296dbf23d4e`, whose base-to-v1 diff has SHA-256 `e9100ef028c4b93a1355e13bc23ee00c05455f67768abe2552eeced0c82baecd`. F05 transitioned five fixture paths to final v2 commit `5968dfed54f07e36e77a78c8c215647543630bcf`, used by F05–F09. The 49-file base-to-v2 binary diff has SHA-256 `10d0320881ab683c5dc8b6df857512e50489c8186bfd4479ab428b7d3e492f28`; its complete file list is in the evidence appendix. Production `_setup_manager.py`, `mcp_server.py`, `_setup_state.py`, and `_setup_evaluation.py` remained byte-identical to the base at both fixture commits. `_setup_dispatches.py` adds only finite fixture routes while retaining the separately asserted release action set.

### F00 — validate and install the isolated fixture

**Pipeline.** Clone exact base `a46f7f68`, write the fixture contract test first, observe its missing-fixture failures, implement the four-node overlay, run focused and expanded repository gates, commit the overlay, install it into a second isolated Codex home, then rerun focused tests against the installed cache. Resolve plugin data through the installed getter and check ledger absence before and after validation.

**Observed.** The v1 and v2-B fixture contracts each passed 5 tests; expanded manager/MCP/setup coverage passed 138; validators passed 29; exact sync checks passed; and the host-capable staged precommit passed 3140 tests with 22 skips and 98 deselections. Installed-cache fixture tests passed 5. Validation created no setup ledger. After the path-alignment failure described below, an installed-runtime RED test exposed three missing `_rtx/__init__.py` package markers. The fixture-only repair added those markers, repeated every gate, and installed a clean lane at overlay commit `0ce25745`, based on `a46f7f68`. All eight installed verifiers loaded and returned their correct clean-state objects; both probes loaded and returned their expected not-set-up result without creating state.

Two frozen REDs required controller rulings. First, the production binding test asserted that `PRODUCTION_DISPATCHES` contained only release routes even though the byte-identical manager consumes that exact constant. The fixture clone therefore amends only that expectation while separately preserving the release-canary assertion. Second, `validators/skill/skill_md_dispatch.py` demands obsolete literal raw-dispatcher prose for root process exports, while the current generator emits `famulus.invoke`; the mandated root D probe made this irreducible. The clone adds an exact four-module validator exemption instead of introducing obsolete guidance. Both are fixture-only compatibility amendments, not product runtime changes.

The first F01 attempt corrected one F00 runtime-path claim: the controller-only getter probe resolved `/fixture/plugin-data`, while an installed Codex worker selected `/fixture/codex-home/plugins/data/agent-plugins/<plugin-id>`. F00 therefore did not establish an absent ledger at the worker-selected path. The first F01 worker stopped before settlement, froze its active C flow and marker as an experiment error, and the controller then removed only that task-owned fixture ledger, lock, and marker after preserving them in evidence. Static and installed-cache validation results were unaffected.

A second fresh attempt verified the clean hashed baseline but retained the outer `FAMULUS_PLUGIN_DATA=/fixture/plugin-data` hint. The worker-facing getter created C's marker in the hashed path, while the nested verifier inherited the override and reported C incomplete. It again stopped before settlement; the controller preserved and removed only that task-owned partial state. Fixture workers must omit the optional override so all nested getters resolve the same installed Codex plugin-data root.

**Verdict: PASS.** The repaired isolated v1 fixture is statically and dynamically valid, installed from exact committed overlay `0ce25745`, uses the worker-selected hashed plugin-data path with outer `FAMULUS_HOST` and `FAMULUS_PLUGIN_DATA` unset, and leaves production state-machine code unchanged. F05 separately validates the exact v2 transition and final identity.

**Lesson.** Synthetic validation must separately freeze the release baseline and disclose every harness-only expectation or validator amendment; a green synthetic precommit alone is not release evidence.

### F01 — deep setup through an owned child

**Pipeline.** From the repaired clean installation, launch a fresh worker with outer `FAMULUS_HOST` and `FAMULUS_PLUGIN_DATA` unset. Request A's owned child probe with a unique nonce, grant permission for its managed setup, follow each exact step, recheck, authorize, and resume the probe once.

**Observed.** Fresh thread `01a05cfa-...aa61` mapped the child probe to root A and serialized pending stack `[A,B,C]`. It executed and settled C, B, then A, rechecked ready state, authorized once with `resume_original=true`, and ran one probe. The final ledger had `active_flow=null` and exact version-1 A/B/C receipts, each claimed only by A. Markers A/B/C existed, the A-probe counter had exactly one entry, and the nonce was absent from the ledger.

The two earlier attempts remain frozen as experimental setup failures. The first checked the wrong plugin-data baseline; the second retained a conflicting outer override that split the worker-facing and nested-verifier getter paths. Neither settled a receipt, authorized, or launched a probe.

**Verdict: PASS.** The production manager machinery handled a synthetic child-owned three-level dependency chain in dependency-first order and resumed the original probe exactly once.

**Lesson.** Installed plugin environments must be tested with host-injected plugin data, not controller overrides; otherwise the MCP and nested verifier can observe different state roots.

### F02 — duplicate invocation receives the existing busy flow

**Pipeline.** Reset A through its exact managed teardown, preserving the probe counter baseline. Start one fresh worker, begin A setup, and stop with C current before its action. In a second fresh worker, request the ordinary A probe and stop at the first refusal without recovering.

**Observed.** The reset worker removed A, B, and C receipts and markers. Worker 1 created flow `dc0f3b23-...b8d8` and stopped before C ran. Corrected Worker 2 received `setup_busy` with that exact flow ID and only the interface/version of `recover@1`; the route exposed no action or original arguments. The ledger was byte-identical around Worker 2, with no receipt, marker, second flow, or counter delta.

An earlier Worker 2 card called manager status directly instead of the ordinary probe. It is preserved as an experiment-card error with zero state delta and was replaced by a new thread.

**Verdict: PASS.** A second session observed the single existing flow through a redacted, argument-free recovery route and performed no duplicate work.

**Lesson.** Busy-flow acceptance must begin at the ordinary MCP target; direct manager status can verify state but does not prove enforcement at the product boundary.

### F03 — interrupted action recovers verifier-first

**Pipeline.** With F02's active flow stopped at C, the controller creates only C's exact empty marker to simulate an external Markdown action that completed before settlement. A fresh worker calls `recover(flow_id, retry)` and follows the returned flow to authorization and one probe.

**Observed.** Thread `01a05d12-...1c84` retried the active step: C's verifier passed, its receipt was recorded, and the manager advanced directly to B with zero post-retry `run-markdown(C)` calls. That worker stopped at B because its card did not make continued execution sufficiently explicit; it is retained as an experiment-instruction failure. Fresh continuation thread `01a05d16-...dee6` recovered the same flow, completed B then A, rechecked ready state, authorized once, and ran one probe. The final idle ledger contained exact A/B/C version-1 receipts, all markers existed, and the counter increased by one.

**Verdict: PASS.** Retry settled the externally completed C action by verification before any rerun, and the interrupted flow ultimately produced one authorized original probe.

**Lesson.** Recovery cards must state whether the worker should stop at the returned step or continue the entire flow; the manager's verifier-first result can be valid even when the agent card is ambiguous.

### F04 — cancellation removes ghost claims

**Pipeline.** Reset the ready A closure through managed teardown. Start a new A setup, settle C and B, and stop with A current before its action. In a fresh worker, call exact `recover(flow_id, cancel)`, then query A status read-only in another fresh worker.

**Observed.** Cancellation returned ready with `resume_original=false` and cleared the active flow. Exact C/B version-1 receipts and markers remained, but A was removed from both `required_by` sets. No A receipt, marker, authorization, or probe existed. Fresh A status returned `setup_required` with only A pending, and the probe counter was byte-identical. One controller-overlap harness iteration is preserved and excluded from the authoritative history.

**Verdict: PASS.** Cancellation retained the verified prefix while removing the incomplete root's ghost claims and never guessed that A's current action completed.

**Lesson.** Cancellation evidence must inspect retained receipts and their claimant sets separately; preserving verified installation state is different from preserving an incomplete root claim.

### F05 — stale B reruns only its dependent suffix

**Pipeline.** Complete and authorize A against fixture v1. Stop all workers, apply the reviewed B-version transition, run its gates, commit it, and reinstall the same isolated Codex home while retaining the ledger. In a fresh worker, request A's child probe with permission to repair stale setup.

**Observed.** The canonical v2 transition commit `5968dfed` changed five fixture paths: A's B pin, B's setup version, the finite binding/manager pin, and B's regenerated skill projection. The initially reviewed four-path patch omitted that generated projection; validators caught the stale block before commit or reinstall. Corrected gates passed 5 focused, 29 validators, 104 expanded, and 5 installed-cache tests. Production core hashes remained unchanged and the retained ledger was byte-identical across reinstall.

The accepted worker reported pending `[A@1,B@2]`, executed B then A, authorized once, and ran one probe. Final receipts were A1/B2/C1, claimed only by A. C's marker inode, modification time, size, and digest were unchanged. One earlier card incorrectly embedded `@1` in an interface positional, returned unmanaged, and was stopped before mutation.

**Verdict: PASS.** Version drift at B retained C and rebuilt only the dependency-first B/A suffix before one authorized probe. Independent evidence review was green.

**Lesson.** A reviewed version patch must include regenerated consumer projections; run sync validators before committing or installing a stale-variant fixture.

### F06 — explicit invalidation removes the live dependent closure

**Pipeline.** From ready A1/B2/C1, invoke public manager invalidation for C and query A status. Without editing external markers, start a separate fresh worker to rebuild the returned closure, authorize, and run one probe.

**Observed.** Fresh invalidation thread `01a05d4b-...` removed exact receipts `{C,B,A}`. The mid-ledger was empty while all markers and the four-line probe counter were unchanged. A status returned full pending stack `[A1,B2,C1]`. Fresh rebuild thread `01a05d4d-...` executed C1, B2, A1, rechecked ready, authorized once, and ran one probe. The final ledger was byte-identical to the ready baseline and the counter increased by one.

The invalidation worker read the installed manager `SKILL.md` despite a card prohibition. That read-only evidence-hygiene deviation is preserved; it changed no cache, source, state, or product result.

**Verdict: PASS.** Explicit invalidation removed the selected receipt and every live managed dependent, retained external state, and caused the next ordinary use to rebuild the complete closure.

**Lesson.** Receipt invalidation and external teardown are distinct operations; marker stability before rebuild is necessary evidence that invalidation did not impersonate teardown.

### F07 — shared C survives until its final claimant

**Pipeline.** Exercise both setup orders, A then D and D then A, using fresh workers for every authorization and teardown. After both roots are ready, inspect exact claimant sets. Teardown one root, verify shared C remains, then teardown the final root and count C's external teardown action.

**Observed.** In history one, adding D to ready A produced C claims `{A,D}`. D teardown removed D while retaining C for A; final A teardown ran A, B, C and emptied state. In history two, D setup ran C then D; adding A ran B then A without rerunning C. A teardown ran A/B and retained C/D; final D teardown ran D/C. C's teardown action occurred exactly once in each complete history. Terminal ledgers were idle and empty with no markers; probe deltas matched authorized ordinary calls.

Three malformed or version-qualified worker calls across the second history were rejected before mutation and are preserved in the experiment incident log. The authoritative ledger, action-order, and counter evidence remained invariant.

**Verdict: PASS.** Both root orders retained the shared dependency through the first teardown and removed it exactly once after the final claim was released.

**Lesson.** Shared-dependency testing needs both root orders; one history can hide order-sensitive claimant or teardown bugs.

### F08 — malformed ledger fails closed and redacts

**Pipeline.** With all workers stopped and fixture state idle, save exact ledger bytes and mode privately. Inject malformed JSON containing a unique secret into only the getter-selected fixture ledger, launch a fresh ordinary A probe, then stop the worker, restore exact bytes/mode, and verify canonical status in another fresh thread.

**Observed.** Thread `01a05d75-...` received generic exit-2 `dispatcher.runtime_misconfigured` with message `setup manager invocation failed`, not `setup_busy`. Neither probe counter nor markers changed. The secret and ledger bytes had zero occurrences in the scoped public response and diagnostic surfaces. After restoring the 56-byte mode-`0600` baseline, fresh thread `01a05d77-...` returned idle `setup_required` with A1/B2/C1; digest, mode, counters, and marker inventory matched baseline.

**Verdict: PASS.** Malformed persistence failed closed without launching or disclosing private state, and exact controller restoration returned the manager to canonical operation.

**Lesson.** Malformed-state recovery must restore offline from a private exact backup; asking the fail-closed manager to repair bytes would weaken the corruption boundary.

### F09 — reverse teardown never resumes an ordinary call

**Pipeline.** From idle empty state, complete A setup and one authorized probe in a fresh worker. Snapshot the counter, then follow exact managed A teardown to completion in a separate fresh worker and inspect every manager response.

**Observed.** Setup ran C, B, A with one authorization and one probe. Teardown thread `01a05d83-...b0a0` ran external actions A, B, C. All seven teardown manager responses, including terminal ready, had `resume_original=false`; no authorization or probe occurred. The seven-line probe counter was byte-identical throughout teardown. Final ledger was idle and empty and all markers were absent. F07 independently showed the same reverse-order property inside shared histories.

An earlier teardown attempt was rejected by the approval reviewer before `begin`; ledger, markers, and counter were unchanged. It is retained as an experiment-harness authorization failure, and the accepted rerun used explicit authorization limited to the three task-owned zero-byte markers.

**Verdict: PASS.** Teardown proceeded in reverse dependency order, removed only verified receipts, and never resumed an ordinary operation.

**Lesson.** Teardown evidence must count resume flags and original side effects independently of correct action order; reverse order alone does not prove no-resume behavior.

## Failure classification

No production behavior criterion failed. The frozen non-passing attempts were:

- **Host/capability limits:** the initial P01 worker could not reach the service through the outer controller sandbox; the evaluated run used a new host-capable thread. Sandboxed installer download and `codex doctor` network probes failed for the same boundary.
- **Experimental setup errors:** P03 first omitted the selected Python and permitted a nonconforming direct-import bypass; its second launch used approval policy `never`. P08 first put its canary in `--task` without the required `--run`. Each attempt was preserved, classified, and replaced by a new thread after correcting only the harness.
- **Synthetic fixture and harness errors:** F00 initially omitted three `_rtx/__init__.py` package markers and recorded the wrong runtime plugin-data path; both were caught before accepted deep-flow evidence. Two F01 attempts then exposed the wrong baseline and a conflicting outer plugin-data override. Later discarded calls used incomplete recovery wording, direct status instead of the ordinary boundary, malformed or version-qualified interface arguments, a read-only instruction deviation, or insufficient approval. Every such attempt stopped or was rejected before the state transition relevant to its verdict, was retained separately, and was replaced with a new thread. F05's version patch initially omitted its regenerated skill projection; validators caught it before commit or reinstall.
- **Product defects:** none observed in P00–P09.

Expectations were not weakened to obtain a pass. The P03 cleanup action predates the complete controller-action template and is disclosed as not contemporaneously logged.

## Normal-state evidence limit

The campaign conclusively rechecked only the normal setup ledger metadata/digest and absence of the restored installer marker. The normal ledger remained mode `0600`, 53 bytes, with digest `042cf60a310fb1a522a96532eb7450b66a591e14bfcd2044cd21e96ab5f78a27`; writer quiescence was not established, so even that unchanged digest is supporting rather than globally exclusive evidence. Isolated environment values and resolved child paths support confinement of plugin cache, session, ledger, lock, milestone, and evidence writes, but no contemporaneous complete byte baseline exists for the normal plugin inventory/cache or `.bashrc`. The transient installer PATH block, immediately removed and verified absent, is the sole observed normal-profile write.

## Final repository verification

The first staged precommit attempt inside the controller sandbox exposed one durable-report personal-information validator failure and the expected socket/MCP capability failures. The report path was redacted, staged validators then passed 29 tests, and the exact staged precommit gate was rerun host-capably. The final gate passed 3,135 tests with 22 skips and 98 deselections. The staged candidate contained exactly this report, its evidence appendix, and the implementation-plan status update.

## Reusable lessons ledger

| Scenario | Reliable trigger | Isolation lesson | Evidence lesson | Failure/recovery lesson | Next design change |
|---|---|---|---|---|---|
| Install | Use an explicit isolated Codex home and local marketplace snapshot | The standalone installer can still edit the normal shell profile; inspect and restore it immediately | Record exact version, paths, and the profile diff without copying credentials | Network installation requires a host-capable rerun; sandbox DNS failure is not a product defect | Put an isolated shell-profile path or disposable home around future installer tests before execution |
| P01 | Fail only the MCP Python entrypoint while preserving startup-hook execution | A separate Codex home and plugin-data root made absence of manager state observable | Record both aborted and evaluated thread IDs | Outer network denial requires a new thread, not resume | Make host-capable launch the default for future Codex worker scenarios |
| P02 | Use ordinary prose without a managed target | Reuse the production lane only after proving the ledger baseline | Absence of both tool calls and ledger creation is the relevant pair | None | Keep a negative prose control in every trigger campaign |
| P03 | Invoke one installed unmanaged getter through MCP | Canonical empty-ledger initialization is not a setup receipt | Preserve discarded harness runs separately from the evaluated thread | Missing tool discovery and `approval=never` are harness errors, not product failures | Add tool-presence and approval-policy checks to the worker-launch preflight |
| P04 | Request an ordinary managed action from an empty state | Withheld permission must leave both ledger and product side-effect directories unchanged | Preserve the structured pending response as well as absence evidence | An explanation without `setup_required` would not establish the manager trigger | Pair every permission stop with active-flow and nonce-absence checks |
| P05 | Grant permission only after the ordinary call returns `setup_required` | Inspect the getter-selected plugin-data root, not an assumed environment override | Correlate manager results, ledger receipt, authorization signal, launch count, and nonce count | A successful setup is insufficient if authorization or resumption is duplicated | Keep a started-call timeline and exactly-once nonce assertion as one evidence unit |
| P06 | Start a new process and thread against the retained ready ledger | Do not copy or normalize persisted state between workers | Pair a distinct thread ID with byte-identical before/after ledger digests | Reusing the first worker would not prove cross-session persistence | Make fresh-process persistence a mandatory post-setup scenario |
| P07 | Invoke each exact lifecycle export once and stop at `setup_managed` | Snapshot the ready ledger around both workers | Keep setup and teardown routes plus scoped canary scans separate | Following the route would confound redirection with lifecycle execution | Maintain one-card/one-call lifecycle interception tests |
| P08 | Put the secret in a schema-valid ordinary argument | Keep raw prompt and product output private and outside the promised scan | Name every included and intentionally excluded surface | An invalid canary-bearing call is a harness failure even when nothing leaks | Add target-interface argument validation to redaction preflight |
| P09 | Derive the verdict from a completed first-use run | Freeze the source evidence before independent counting | Correlate resume flags, launch events, and persisted nonce count | Replaying the action to test duplication can itself introduce duplication | Make exactly-once review read-only over frozen evidence |
| F00 | Validate an exact committed overlay before launching workers | Let the installed host inject plugin data; omit outer host/data overrides | Hash the overlay and production core separately | Installed entrypoints need package-boundary smoke tests, not only source tests | Add installed getter/verifier/probe loading to fixture preflight |
| F01 | Invoke A's owned child, not its lifecycle interface | Verify the worker-selected hashed data root is empty | Correlate owner, full stack, settlement order, claims, counter, and ledger scan | Freeze path-split attempts before settlement and restart with a new thread | Put resolved-path equality in every nested-verifier lane preflight |
| F02 | Start the duplicate at the ordinary probe boundary | Preserve one active flow between stopped workers | Require byte-identical ledger and counter around the busy call | A direct status card does not prove MCP enforcement | Generate busy cards from the ordinary target schema |
| F03 | Retry with only the persisted flow ID | Inject only the fixed marker while all workers are stopped | Prove zero reruns of the verified current step | State explicitly that the worker must continue the returned flow | Separate interruption injection from recovery-card wording review |
| F04 | Cancel after an exact verified prefix | Retain markers and receipts while inspecting claims | Compare receipts, claimant sets, active flow, and pending suffix | Controller overlap must be frozen and excluded | Serialize controller snapshots and worker launches with an explicit lane lock |
| F05 | Change only B's declared version and generated consumers | Reinstall into the same isolated home while retaining the ledger | Prove C stability using inode, time, size, and digest | Sync validation must precede stale-build installation | Generate and review a canonical variant diff automatically |
| F06 | Invalidate C through the public manager | Do not remove external markers before status | Record removed receipts and unchanged markers before rebuild | Read-only instruction deviations belong in evidence hygiene | Make controller cards prohibit unnecessary reads only when material |
| F07 | Exercise both A-first and D-first histories | Reset through verified teardown between histories | Count C teardown once per complete history | Reject malformed worker calls without rewriting authoritative state | Add schema-derived exact-call examples to fixture cards |
| F08 | Inject malformed bytes only while all workers are stopped | Backup and restore exact selected ledger bytes and mode | Scan only promised public surfaces and name private exclusions | Never ask the corrupted manager to repair its own ledger | Include offline restore verification in every corruption test |
| F09 | Use an A-only closure and a separate teardown session | Snapshot the counter immediately before teardown | Count action order, all resume flags, and counter bytes | Approval denial before begin is a harness failure with zero state delta | Preauthorize only the exact fixed marker operations needed by teardown |

## Repeat recipe

1. Create a mode-`0700` task root with separate production and synthetic homes, Codex homes, XDG roots, temporary directories, projects, plugin-data roots, and evidence trees. Copy only authentication at mode `0600`; never copy or hash its contents into evidence.
2. Install Codex and the plugin snapshot, then verify shell-profile files, resolved cache/data paths, exact commit, runtime hashes, file modes, and an absent ledger before launching a worker. Freeze this as P00 rather than reconstructing it later.
3. Give each scenario a context-free `card.md` containing only the requested action, permission boundary, and stop condition. Validate target arguments, MCP tool visibility, selected Python, host approval policy, and host-capable network access before launch.
4. Launch a new `codex exec` process and thread for every interactive case; never resume, fork, or reuse a prior thread. Capture the raw event stream and final response under the private lane.
5. Around each mutation, snapshot the exact getter-selected ledger and relevant marker/counter inventory while the worker is stopped. Record structured manager results and a started-call timeline. Use `campaign.json` to mark `planned`, `running`, `passed`, or a specific failure class.
6. For interruptions, stop after the specified manager boundary: after `begin` for busy/retry, after a verified prefix for cancel, and with all processes stopped before controller marker or malformed-ledger injection. Freeze failure state before recovery.
7. For a stale-version case, retain the ledger, rebuild only the declared fixture version and matching binding/metadata, verify the overlay diff, reinstall into the same isolated fixture home, then start a new thread.
8. Scope redaction scans to manager payloads, ledger bytes, dispatcher diagnostics, and redacted report artifacts. Explicitly exclude the private prompt, raw transcript, requested product output, and any original side-effect record that must contain the canary.
9. Before cleanup, verify report coverage, overlay digest, normal-ledger metadata, path confinement, and independent green review. Delete only the task-owned temporary root after durable evidence summaries are committed; never modify normal Codex/plugin state during cleanup.
