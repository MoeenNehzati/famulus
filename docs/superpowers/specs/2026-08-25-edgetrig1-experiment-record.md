# Experiment Record: Run `edgetrig1`

First measured run of the edge-trigger instruction. This file records the fixed
conditions, exactly what the worker was given, where every artifact lives, and
how the numbers were computed.

## 1. Fixed conditions

| | |
|---|---|
| Date | 2026-08-25 |
| Hypothesis | Adding explicit edge triggers (E1 reference / E2 proof use / E3 prose use) to `inventory.md` raises inventory-stage **edge recall** without an unacceptable precision cost. |
| Source | `skills/math-dependency-graph/assets/inference-from-random-restarts/`, entrypoint `appendix.tex` |
| Scope measured | `appendix/appendix-dynamics.tex` 1-422 and `appendix/appendix-prevalence.tex` 1-198 (voyage 1 of a 2-chunk split) |
| Gold | `assets/inference-from-random-restarts/results/inventory-gold.json` — 83 nodes, 98 edges; 42 nodes and 53 edges fall in scope |
| Dispenser | `math-dependency-graph._rtx.interface.inventory-voyage-dispenser@9` |
| Mode | `default` (no diagnosis hooks), `--chunk-count 2`, `--run-prefix edgetrig1` |
| Worker model | Opus, one fresh context-free agent |
| `packet_chars` | 3000 (9 packets for chunk 1) |
| Baseline | 2026-08-24 voyage run of the pre-edge-trigger instruction, rescored on the identical span |

**Known confounds.** The baseline ran at `packet_chars: 12000` (3 packets); this
run at 3000 (9 packets). Instruction and packet granularity both changed, so the
measured delta is not attributable to the instruction alone. A control run of
the baseline instruction at 3000 is outstanding. Separately, the chunk boundary
differs between runs, which is handled by restricting both to the span above.

**Contract deviation.** `using-compass` requires an agent assigned to every
returned Voyage before any starts. Only voyage 1 was run. Voyage 2
(`edgetrig1-voyage-76c8aa5cdf9c4f0f9e1865a3d23d4e6f`) remains initialized and
unassigned. Nothing was released.

## 2. Initiation

```
dispatcher ... math-dependency-graph._rtx.interface.inventory-voyage-dispenser@9 \
  initiate default \
  --run-prefix edgetrig1 \
  --doc-entrypoint <worktree>/skills/math-dependency-graph/assets/inference-from-random-restarts/appendix.tex \
  --chunk-count 2
```

The dispenser is not reachable through the installed `dispatcher` launcher; it
resolves blueprints against installed skills, where `math-dependency-graph` is
v69 and has no voyage interfaces. The working route runs the worktree CLI under
the managed-runtime interpreter:

```
PYTHONPATH=<worktree>/src \
<home>/.local/share/famulus/runtime/releases/2026-08-24T23-59-17Z-eb01c6/venv/bin/python \
  -m officina.dispatcher.cli \
  --repository-config <worktree>/officina.toml \
  --caller-skill math-dependency-graph \
  math-dependency-graph._rtx.interface.inventory-voyage-dispenser@9 <op> [args]
```

`--caller-skill using-compass` is rejected; the interface is namespace-internal
and requires `math-dependency-graph`.

## 3. What the worker was given

The worker received, from the Voyage payload only:

- `inventory_instruction` — the full text of `inventory.md` as frozen at
  `initiate`, 21585 characters. Preserved verbatim at
  `artifacts/edgetrig1/evaluation/inventory.md.frozen-edgetrig1.md`.
- `cumulative_packets_file` — path to the growing line-numbered packet
  transcript.
- One packet per report step, `prior_inventory`, and `output_schema`.

It received **no** gold, no benchmark context, no baseline numbers, no prior
run output, and no statement of the hypothesis. Its dispatch brief was:

> You are a Voyage agent. Drive exactly one Voyage to terminal. Do not invoke any Skill tool.
>
> Your assigned voyage_id (use ONLY this one, never any other):
>   edgetrig1-voyage-20c6e026cb324009902a18c5ede98c8e
>
> Invoke the dispenser with this exact command shape, substituting the operation and arguments:
>
>   [the invocation route above]
>
> Operations: `status <voyage-id>`, `validate <voyage-id> --response-file <path> --responding-to <evolution_entry_id>`, `advance <voyage-id> [--response-file <path> --responding-to <entry-id>]`.
> Every call prints `warning:` lines on stderr; those are harmless, ignore them.
>
> Protocol:
> 1. `status` your voyage. Read the returned instruction and its `instructions.response_schema`.
> 2. For a ready Message: perform the instruction using the supplied payload, compose a response satisfying that step's `response_schema` exactly, write it to a JSON file, `validate` it, and only after validation succeeds `advance` with the same `--response-file` and `--responding-to <evolution_entry_id>`.
> 3. For ready automatic work, `advance` with no response.
> 4. Read a fresh `status` after every successful advance. Stop on terminal, fault, uncertain, malformed, or unknown status.
> 5. Do NOT invoke `release`. Leave the working directory intact.
>
> The payload carries `inventory_instruction` (your complete task specification) and `cumulative_packets_file` (a path). Follow `inventory_instruction` exactly and literally — it is the object under test. Obey its rules on reading, look-back, and record-keeping precisely as written, including any limits it states. Do not substitute your own judgement about a better way to inventory a document, and do not read source files other than through what the Voyage gives you.
>
> Work directly from the mathematics. Do not write scripts, loops, or lexical matchers to generate nodes or edges.
>
> When you reach terminal, report: the terminal status, the output artifact path, and the final node / edge / gap counts. Also report anything in `inventory_instruction` that was ambiguous, self-contradictory, or impossible to comply with, and any point where a validate call rejected your response and why. Be specific. Do not paste large file contents.

## 4. Where the results are

All paths relative to
`skills/math-dependency-graph/_rtx/_inventory_pipeline/`.

| artifact | path |
|---|---|
| Inventory fragment (the result) | `artifacts/edgetrig1/inventories/edgetrig1-voyage-20c6e026cb324009902a18c5ede98c8e.json` |
| Cumulative packet transcript | `artifacts/edgetrig1/source-packets/edgetrig1-voyage-20c6e026cb324009902a18c5ede98c8e.txt` |
| Chunk records | `artifacts/edgetrig1/chunks/inventory-001.json`, `inventory-002.json` |
| Chunk index | `artifacts/edgetrig1/inventory-chunks.json` |
| Voyage state and full turn history | `voyages/edgetrig1/edgetrig1-voyage-20c6e026cb324009902a18c5ede98c8e/inventory-voyage.reckoning.json` |
| Frozen instruction under test | `artifacts/edgetrig1/evaluation/inventory.md.frozen-edgetrig1.md` |
| Baseline instruction | `artifacts/edgetrig1/evaluation/inventory.md.baseline-2026-08-24.md` |
| Scorer | `artifacts/edgetrig1/evaluation/score_restricted.py` |
| Baseline fragment | `artifacts/inventories/voyage-3c07c04ccd404a56a6a9e7eab33640a2.json` |

## 5. How the numbers were computed

`score_restricted.py` remaps each fragment's `files` indices onto the gold's
canonical file list, restricts nodes and edges to the scope span, and matches by
**span overlap on both endpoints** under **one-to-one** assignment. Run as:

```
./score_restricted.py "label=<path-to-fragment.json>" ...
```

Two derived conventions were applied by hand in the diagnostic pass and are
**not** yet in the script:

- **proof-collapse** — map each proof node onto the result it proves via its
  `proves` edge, drop the structural proof-to-result edge, then compare. Applied
  to both sides symmetrically.
- **pair deduplication** — collapse multiple evidence-distinct records for the
  same prerequisite-dependent pair, which `inventory.md` explicitly permits.

Both are required to read the results correctly and should be folded into the
scorer before the next run.

## 6. Results

| | nodes recall | nodes prec | edges recall | edges prec |
|---|---:|---:|---:|---:|
| baseline | 83.3% (35/42) | 94.6% | 54.7% (29/53) | 76.3% |
| `edgetrig1`, literal | 92.9% (39/42) | 95.1% | 84.9% (45/53) | 66.2% |
| `edgetrig1`, collapsed + deduplicated | — | — | 84.8% (28/33) | 77.8% (28/36) |

Node F1 0.886 to 0.940. Edge F1 0.637 to 0.744.

Run shape: 41 nodes, 85 edges, 2 gaps, 9 packets. All 10 `validate` calls passed
on first attempt with zero issues. Edge basis composition: 29
`explicit-reference`, 29 `proof-use`, 16 `explicit-prose`, 11
`mathematical-inference`. 19 of 170 endpoint slots unresolved; forward
reconciliation resolved 6.

Residual after collapse and dedup: 5 missed pairs, 8 extra pairs. Three were
adjudicated against source — one verified worker error (ambient-assumption
attribution), one verified gold omission, one supporting the worker. The other
ten are unadjudicated.

Analysis and proposed changes: `2026-08-25-inventory-post-run-changes.md`.
