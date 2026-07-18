# Downstream Email and List Workflow Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` or, with explicit delegation approval, `superpowers:subagent-driven-development`.

**Goal:** Make fresh email/list workflows robust to malformed headers, immediately usable initialization, concurrent classifications, historical rescans, and finalization failures.

**Architecture:** Email decoding normalizes missing headers. List schemas own usable defaults. Cloud mutations batch all category changes into one validated upload. Triage finalization orders metrics, watermark, and pruning behind one machine interface, with every mutable triage artifact under the shared Famulus state root.

**Tech Stack:** Python 3.11, PyYAML, dispatcher, cloud-files/list-manager/email-triage machine interfaces, pytest.

## Global constraints

- Inherit program-wide constraints and sequencing from the [umbrella](README.md), and consume structured failures from [dispatcher Task 2](02-dispatcher-contracts.md). This subplan owns list/triage workflow contracts.
- No concurrent read-modify-write calls may target the same cloud list.
- Batch validation failure uploads nothing.
- Historical rescan deduplicates before classification and never moves the normal watermark backward.
- Finalization failure before watermark advancement preserves the previous watermark bytes.
- Email-triage status, watermark, logs, and failure sentinels never default to a skill, plugin-cache, checkout, or activated-release path.
- Skill/blueprint changes use `skill-maker` and regenerate contracts.

## Source feedback owned here

Items 12 and 23-28 in the umbrella traceability table.

---

## Verified prerequisite: missing-Subject email decoding

The reported crash is already repaired in the live runtime and covered by this regression:

```python
def test_decode_mime_words_accepts_missing_header() -> None:
    assert mail.decode_mime_words(None) == ""
```

Before implementing this subplan, run `python3 -m pytest -q skills/email-client/tests/test_mail.py` and require it to pass. If the regression is absent or fails, stop and restore `decode_mime_words(None) -> ""` as a separate prerequisite repair; do not create a no-op commit.

---

### Task 1: Make newly initialized todo and triage lists immediately usable

**Files:**
- Modify: `skills/list-manager/_rtx/_yaml_store.py`
- Modify: `skills/list-manager/tests/test_lists.py`
- Modify: `skills/list-manager/tests/test_validation.py`
- Modify: `skills/list-manager/references/list-structure.md`

**Interfaces:**
- Produces: `default_categories(schema_name: str) -> list[dict[str, object]]`.
- Changes: `init --schema todo|triage` creates a `Personal` domain with `Replies`, `Payments`, `Reading`, `Writing`, `Tasks`, `Misc`, and `Shop` subcategories.
- Preserves: `init --schema default` creates an empty generic list.

- [ ] **Step 1: Replace the empty-initialization expectation**

```python
def test_init_todo_creates_immediately_usable_personal_tree(tmp_path) -> None:
    path = tmp_path / "todo.yaml"
    result = run(["init", str(path), "--schema", "todo"])
    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [category["name"] for category in data["categories"]] == ["Personal"]
    assert [category["name"] for category in data["categories"][0]["categories"]] == [
        "Replies", "Payments", "Reading", "Writing", "Tasks", "Misc", "Shop"
    ]
```

Add the same assertion for triage and a cloud-mode test proving an entry can be added immediately after initialization.

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m pytest -q skills/list-manager/tests/test_lists.py skills/list-manager/tests/test_validation.py`

Expected: current initialization returns `categories: []`.

- [ ] **Step 3: Implement schema-owned defaults**

Add one constant tuple for the seven Personal subcategories and return fresh dict/list objects for each initialization. Do not infer defaults from arbitrary schema titles at runtime; support exactly `todo` and `triage`, leaving `default` unchanged.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m pytest -q skills/list-manager/tests/test_lists.py skills/list-manager/tests/test_validation.py skills/list-manager/tests/test_python_machine_interfaces.py`

Expected: all pass and existing files are never rewritten by `init`.

- [ ] **Step 5: Commit after review**

Commit with message `fix: initialize usable personal lists`.

---

### Task 2: Prevent triage lost updates and make finalization retry-safe

**Files:**
- Create: `skills/email-triage/_rtx/_finalize_run.py`
- Create: `skills/email-triage/tests/test_finalize_run.py`
- Create: `skills/email-triage/tests/test_state_paths.py`
- Modify: `skills/email-triage/_rtx/_envelope_gate.py`
- Modify: `skills/email-triage/_rtx/_decision_sink.py`
- Modify: `skills/email-triage/_rtx/_failure_clearer.py`
- Modify: `skills/email-triage/_rtx/_failure_sentinel.py`
- Modify: `skills/email-triage/_rtx/_log_compactor.py`
- Modify: `skills/email-triage/_rtx/_watermark_floor.py`
- Modify: `skills/email-triage/_rtx/_watermark_writer.py`
- Modify or remove after caller audit: `skills/email-triage/_rtx/_write_metrics.py`
- Modify: `skills/email-triage/llm_interfaces/triage.md`
- Modify through `skill-maker`: `skills/email-triage/blueprint.yaml`
- Modify through `skill-maker`: `skills/list-manager/blueprint.yaml`
- Modify: `skills/list-manager/schemas/types/action.json`
- Modify: `skills/list-manager/schemas/types/triage_action.json`
- Modify: `skills/list-manager/references/action-structure.md`
- Modify: `skills/list-manager/references/list-structure.md`
- Modify: `skills/list-manager/_rtx/_yaml_store.py`
- Modify: `skills/list-manager/_rtx/_cloud_transport.py`
- Modify: `skills/list-manager/tests/test_python_machine_interfaces.py`
- Modify: `skills/list-manager/tests/test_validation.py`
- Modify: `skills/list-manager/tests/test_lists.py`
- Modify: `skills/email-triage/tests/test_filter_envelopes.py`
- Modify: `skills/email-triage/tests/test_llm_routing.py`
- Modify: `skills/email-triage/tests/test_watermark.py`
- Regenerate: affected `SKILL.md` and LLM-interface injected blocks

**Interfaces:**
- Produces: `list-manager.machine.cloud-apply-batch <list-name> --operations <yaml-file>`; one download, multiple category mutations, one upload.
- Produces: `email-triage.machine.scripts-finalize-run --run-id ID --total-scanned N --added-todo N --added-triage N --skipped N --deduped N --accounts CSV`.
- Produces: `filter_destination_duplicates(envelopes: Sequence[Mapping[str, object]], destination_entries: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]`; identity is `(account, normalized Message-ID)` when present and `(account, server UID)` otherwise.
- Produces: `email-triage.machine.fetch-filtered-envelopes --rescan-after YYYY-MM-DD` for an explicit one-off historical scan that deduplicates against existing destination entries and does not move the normal watermark backward.
- Replaces: separate metrics, watermark, and pruning calls in the LLM workflow.
- Changes: todo/triage action schemas accept one optional immutable `source` object with required `account` and at least one of `message_id` or `uid`.

- [ ] **Step 1: Add a lost-update regression test**

Provide an operations YAML containing entries for multiple categories:

```yaml
operations:
  - target: Personal/Replies
    entries:
      - title: Reply to A
        deadline: "2026-07-20"
  - target: Personal/Misc
    entries:
      - title: Review event B
```

Assert one `download_list` call, one `upload_list` call, both mutations present in the uploaded document, and a nonzero result when any operation fails validation. The implementation must not launch parallel read-modify-write calls.

- [ ] **Step 2: Add retry-safe finalization tests**

Require finalization to:

1. validate all counts and accounts before writing;
2. refuse when a failure sentinel is active;
3. upsert metrics under the stable `run-id`, so retrying the same run cannot double-count;
4. prune only the logs eligible after that successful run;
5. advance the watermark as the final commit point; and
6. return nonzero and leave the old watermark unchanged if metrics, pruning, or watermark persistence fails.

Inject a failure after each stage. Retrying with the same `run-id` must replace/reuse the existing metrics record, repeat pruning safely, and advance the watermark at most once. A pruning failure therefore cannot occur after watermark advancement, and a watermark-write failure leaves its previous bytes intact.

- [ ] **Step 3: Add historical-rescan tests**

An authorized `--rescan-after` date overrides envelope selection for that invocation only. Seed the existing todo/triage destination entries with source identity for one historical message, then present that message and one new historical message:

```python
destination_entries = [
    {"source": {"account": "personal", "message_id": "<existing@example.com>"}}
]
envelopes = [
    {"account": "personal", "message_id": "<existing@example.com>", "uid": "41"},
    {"account": "personal", "message_id": "<new@example.com>", "uid": "42"},
]

kept = filter_destination_duplicates(envelopes, destination_entries)
assert [item["uid"] for item in kept] == ["42"]
```

Add the same regression for a missing Message-ID using the `(account, uid)` fallback. Assert the duplicate contributes to the `deduped` count, produces no batch mutation, and cannot be re-added even though it predates the normal watermark. Assert the saved watermark bytes are identical before and after rescan filtering/finalization; normal mode retains its existing advancement behavior.

Add schema/round-trip tests for this exact compatible extension:

```json
{
  "source": {
    "account": "personal",
    "message_id": "<normalized@example.com>",
    "uid": "42"
  }
}
```

`source` requires a nonempty `account`; `anyOf` requires nonempty `message_id` or `uid`; `additionalProperties` is false. Both identifiers may coexist. Existing entries without `source` remain valid. Once present, `source` is immutable through normal update/batch operations. Normalize Message-ID before persistence and comparison. Test YAML read/write and beautified rendering so metadata is not dropped.

- [ ] **Step 4: Run focused tests and verify RED**

Run: `python3 -m pytest -q skills/list-manager/tests/test_python_machine_interfaces.py skills/email-triage/tests/test_finalize_run.py skills/email-triage/tests/test_state_paths.py skills/email-triage/tests/test_filter_envelopes.py skills/email-triage/tests/test_llm_routing.py skills/email-triage/tests/test_watermark.py`

Expected: batch/finalizer interfaces are absent and rescan mode is unsupported.

- [ ] **Step 5: Implement one-process batch mutation**

Parse the operations file, load the remote list once, apply each operation using the same validation/defaulting helpers as `create-entry`, validate the final document, then upload once. On failure, upload nothing.

Update triage instructions to collect classified additions first, issue one batch mutation per destination list, and explicitly forbid concurrent mutations to the same cloud list.

Add the optional `source` definition to both action schemas and their references, then store the canonical source identity on every new todo/triage entry. Before a historical rescan produces operations, read both destination lists once, combine their source identities, and pass the fetched envelopes through `filter_destination_duplicates`. Deduplication is mandatory in rescan mode and runs before classification or list mutation; `--rescan-after` bypasses only the watermark cutoff. Legacy entries without source identity cannot be safely deduplicated and remain an explicit limitation; do not use heuristic title/date matching.

- [ ] **Step 6: Implement one ordered finalizer**

Move the existing metrics payload construction, log pruning, and watermark update guard behind `scripts-finalize-run`. Update every listed state/log consumer together and resolve paths from the shared Famulus email-triage state root; `EMAIL_TRIAGE_STATE_DIR`/`EMAIL_TRIAGE_LOG_FILE` are explicit overrides for scheduler/tests, but no default may point at `skills/email-triage/state` or `skills/email-triage/triage.log`. Add a read-only-skill-tree test exercising decision append, failure latch/clear, cutoff/envelope read, compaction, and successful finalization. Validate first, idempotently upsert per-run metrics by `run-id`, prune, and make watermark replacement the final commit point. `status.json` keeps a latest `metrics` object containing its `run_id`; retrying that run replaces the object rather than adding counts, so no unbounded history or cumulative double-count is introduced. Keep the old interfaces temporarily only if another declared caller still uses them; otherwise remove them through `skill-maker` in the same contract change.

- [ ] **Step 7: Correct generated usages**

Declare every required named metrics flag in the blueprint usage/pattern contract. Regenerate the SKILL block and assert the displayed command is executable as written.

- [ ] **Step 8: Run focused suites**

Run: `python3 -m pytest -q skills/list-manager/tests skills/email-triage/tests`

Expected: all pass, including a proof that multi-category additions survive one atomic application.

- [ ] **Step 9: Commit after review**

Commit with message `fix: make email triage updates transactional`.

---
