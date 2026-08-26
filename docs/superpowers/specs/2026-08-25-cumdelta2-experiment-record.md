# Experiment Record: Run `cumdelta2`

Fresh subagent run of the worker-owned cumulative inventory and packet-local
diagnostic-response contract. This pass measures inventory-stage behavior; it
does not isolate transport causally because the frozen instruction differs from
`edgetrig1`.

## Conditions

| | |
|---|---|
| Date | 2026-08-25 |
| Source | `skills/math-dependency-graph/assets/inference-from-random-restarts/appendix.tex` |
| Mode | `default`, two chunks, run prefix `cumdelta2` |
| Interface | `math-dependency-graph._rtx.interface.inventory-voyage-dispenser@10` |
| Rutter definition | version 7 |
| Packet limit | 3,000 characters |
| Workers | two fresh context-free subagents, one fixed Voyage each |
| Worker model/reasoning | not exposed by the subagent interface |
| Instruction | `inventory.md`, SHA-256 `09a3c921b471335f9e6b44dc72fa0345d5130887f70e91c67fa65739ae4f2f7b`, 17,946 UTF-8 bytes |
| Setup outer latency | 1.011 seconds |
| Question | Does the simplified transport satisfy the requested packet/file contract, and what inventory quality results? |

The workers received only the assigned Voyage binding and ID. Through the
Voyage they received the frozen instruction, `cumulative_packets_file` path,
`inventory_file` path, and one exact packet string per report. They were
prohibited from reading source files, chunk records, schemas, gold, prior runs,
other Voyages, or benchmark material. Both terminal Voyages were preserved for
evaluation and were not released.

## Results

| Chunk | Voyage | Packets | Nodes | Edges | Gaps | Validation rejections | Worker elapsed |
|---|---|---:|---:|---:|---:|---:|---:|
| `inventory-001` | `cumdelta2-voyage-11227c1500ce42948192aa04c4b445bb` | 9 | 35 | 62 | 2 | 0 | about 25 minutes |
| `inventory-002` | `cumdelta2-voyage-672ededbe5f84952a597b5b1afd01ed9` | 10 | 35 | 84 | 7 | 0 | about 20 minutes |

Both public statuses independently reported `terminal/complete` with no fault.
Worker elapsed times are rough reports, not persisted machine measurements.

Restricted chunk-1 scoring uses the same scorer and span as `edgetrig1`:
42 gold nodes, 33 proof-collapsed and pair-deduplicated gold edges, and 53
literal gold edges.

| Run | Node recall | Node precision | Collapsed edge recall | Collapsed edge precision | Literal edge recall | Literal edge precision |
|---|---:|---:|---:|---:|---:|---:|
| `cumdelta2` | 76.2% (32/42) | 91.4% (32/35) | 66.7% (22/33) | 78.6% (22/28) | 66.0% (35/53) | 74.5% (35/47) |
| `edgetrig1` | 92.9% (39/42) | 95.1% (39/41) | 84.8% (28/33) | 77.8% (28/36) | 84.9% (45/53) | 66.2% (45/68) |

This is a material recall regression. It is not a transport-only comparison:
the instruction snapshots differ substantially, and `cumdelta2` no longer has
the explicit E1/E2/E3 trigger section used by `edgetrig1`.

## Contract audit

A fresh read-only subagent independently audited implementation and artifacts.
All requested clauses were verified:

- introduction payload keys are exactly `inventory_instruction`,
  `cumulative_packets_file`, and `inventory_file`;
- all 19 report payloads are strings, with no source path, `packet_id`,
  `source_file`, or `@@ source:` marker;
- concatenated displayed report strings equal the two cumulative packet files
  byte-for-byte;
- the worker owns the cumulative inventory and accepted report arrays are the
  newly appended suffixes;
- final report ID sequences equal final cumulative ID sequences; the only
  earlier-record difference is a permitted forward endpoint reconciliation;
- final locations are canonical three-coordinate locations; and
- every prepare and record machine result has an empty value object.

The packet transcript hashes are:

- chunk 1: `8a577b620fc735bd72f04116721c69f196816f01f32469fcb0371c926786e19c`;
- chunk 2: `d7c7f7a935bc571f1b5102e8a8bd9b0fcdd368870c314dc9b7c63e4b18d8b57a`.

Intermediate `inventory_file` bytes are not archived, so historical snapshots
cannot be replayed after completion. The accepted turns and validator establish
the suffix contract at acceptance time.

## Quality audit

The clearest verified defect is named-tool nodeization. `edgetrig1` contains six
`external-result` nodes; `cumdelta2` contains none and leaves the same tools only
as unresolved endpoints: Nagurney, McShane extension, dominated convergence and
Cesaro averaging, the preimage theorem, Sard's theorem, and rank-nullity. It
also omits the substantive remark at prevalence lines 196-198.

Edge-basis counts changed as follows:

| Basis | `cumdelta2` | `edgetrig1` |
|---|---:|---:|
| explicit-reference | 16 | 29 |
| proof-use | 24 | 29 |
| explicit-prose | 22 | 16 |
| mathematical-inference | 0 | 11 |

The report-local IDs are contiguous and sum exactly to final counts, so there is
no truncation, overwrite, or delta-accounting signature. The leading explanation
is instruction/model behavior, especially removal of mandatory edge-trigger
checks; the current evidence does not establish that packet-local delta transport
caused a semantic loss. This audit did not fully adjudicate every discrepancy.

Chunk 2 has an additional quality risk: 48 of 84 edges contain unresolved
endpoints and it also has zero `external-result` nodes. It was not scored by the
chunk-1 restricted scorer.

## Size and speed

| Artifact | Chunk 1 | Chunk 2 |
|---|---:|---:|
| Source-packet transcript | 32,975 bytes | 28,674 bytes |
| Final inventory | 32,812 bytes | 46,211 bytes |
| Reckoning | 279,131 bytes | 295,871 bytes |

The cleaned chunk-1 reckoning is 62.7% smaller than `edgetrig1`'s 748,202-byte
reckoning. Summed packet-local report evidence is 33,005 bytes versus 231,134
bytes of cumulative responses in `edgetrig1`. The transport cleanup therefore
reduced retained state substantially, but semantic reading and cumulative
schema-safe inventory maintenance still dominated the reported 20-25 minute
worker times. Exact per-step timings are not exposed and remain a measurement
gap.

## Next controlled experiment

Run a replicated transport A/B on the identical frozen chunk, worker model and
reasoning level, packet assignment, and `edgetrig1` edge-trigger instruction.
Vary only cumulative-response transport versus packet-local deltas plus a
worker-owned cumulative file, with at least three fresh workers per arm. Compare
named-tool retention, edge-basis counts, restricted recall and precision,
schema failures, history bytes, and persisted timing. This isolates transport
from the instruction and single-worker variance that confound `cumdelta2`.

## Artifacts

- chunk-1 inventory SHA-256: `c90705bf1379ac58fbe7907f7f9bbb29931ad00aced5b7038dc566d9aff17879`;
- chunk-2 inventory SHA-256: `83b7d397f794bdbbc9707387158d18d07d8b2a69778c44c80385c5b3c9af4166`;
- chunk-1 reckoning SHA-256: `68e4435bfcefaad066fe1ad3ddd32b4dfc56f85faa94043413b6e258aec005bc`;
- chunk-2 reckoning SHA-256: `9f256f1146a802a0593ff9b0c628e93c0b8af2b9178aa0e511bf6659f0ff5c2a`.

All run artifacts are under
`skills/math-dependency-graph/_rtx/_inventory_pipeline/{artifacts,voyages}/cumdelta2/`.
