# Inventory Transport Cleanup

Scope principle: `instructions/inventory.md` explains **how a received packet is
analyzed**, plus **bounded look-back on `cumulative_packets_file`**. Nothing
else. Everything the Voyage transport owns comes out.

## Why

The path and progress machinery is a v69 holdover. In that pipeline the gateway
spawned an inventory worker with explicit `output` and `progress` paths and the
worker wrote both files itself. Under the Voyage transport the response schema is
`["outcome", "packet_id", "inventory"]`, the inventory travels inline, and the
dispenser writes the artifact. Nothing is supplied for the worker to write to.

`_chunk_extractor.py:444-449` still computes `fragment_path` and `progress_path`
into every chunk record, but `_voyage_support.py` never surfaces either to the
worker. Dead metadata feeding dead instructions.

Measured cost: the voyage worker on run `edgetrig1` reported these requirements
as the single largest source of friction in the instruction. It performed the
underlying work and could not emit any of the mandated markers.

## Remove

| line | text | reason |
|---|---|---|
| 36 | "Write only to the supplied output and progress paths." | neither path is supplied |
| 38 | whole marker-scan paragraph: "scan only the packet's assigned-span markers to build `files` … Write and validate the empty cumulative fragment … append an `initialized` progress line" | `@@ source:` markers removed; `files` arrives pre-seeded in `prior_inventory`; no fragment to write |
| 252 | checkpoint step 4, "append a `scan-checkpoint` progress line …" | no progress path |
| 254 | "The saved artifact is the progress evidence. Do not keep finished records only in reasoning or write all checkpoints at the end." | no saved artifact; the transport forces a validated cumulative submission at every packet |
| 256 | the `span-complete` paragraph | no progress path |
| 272 | finish step 4, "append `output-written` …" | no progress path |
| 274 | "Do not write `output-written` before every span has a `span-complete` line … Return only the completed output path." | contradicts the response schema, which requires the inventory inline |

## Rewrite

**`## Output contract` (27-38).** Describe the response object, not a written
file. Keep `ir_version: 3`, `chunk_id`, `files`, and the cumulative `nodes`,
`edges`, `gaps` arrays. Line 29 ("Read `inventory.schema.json` completely before
reading source") must point at the `output_schema` supplied in the payload: as
written it names a file the worker is told never to open and is given no path to.

**`## Checkpoint while scanning` (244-257).** The cadence "after every four
closed nodes, or after 120 assigned source lines" is obsolete. The packet is the
checkpoint and validation is compulsory at each one. Keep step 2's invariant list
and step 3's schema check, reframed as "before each packet report, verify …".
That list is the only part of the section still doing work.

**`## Finish` (264-274).** Keep: close any open node, final forward
reconciliation, verify `files` and the cursor, validate. Drop the save,
`output-written`, and return-a-path steps.

## Fixes in the edge-trigger change

- `## Looking back` says a look-back "never … resolves an endpoint to a
  `local_node`". That reads as forbidding what `### Reconcile forward local
  identities` does on node closure. Scope it to look-back only.
- Fold the origin of `files` (from `prior_inventory`) into `## How you receive
  source`, since line 38 is being deleted.

## Out of scope

`### Reconcile forward local identities`, the six slots, `### Edge triggers`,
`### Proof candidates and ownership`, `## Encode a node`, `## Encode a direct
dependency`, `## Use gaps sparingly`, `## Keep records concise`. All are packet
analysis and stay.

## Follow-on outside this file

`_chunk_extractor.py:444-449` computes `fragment_path` and `progress_path` that
nothing consumes. Harmless, but worth removing with the dispenser work.
