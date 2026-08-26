# Design the implementation

Read the input and every approved semantic artifact. Inspect the live owning
module, public Rutter construction and execution exports, Compass binding
contract, blueprint graph, tests, and dirty state. Write
`05_implementation_design.md` in the distillation workspace.

Begin the artifact with this envelope:

```yaml
schema_version: distill-to-rutters/v1
stage: design-implementation
outcome: <design-ready|design-gap|design-blocked|partial|failed>
prerequisites:
  - kind: artifact
    path: <workspace>/04_logic_validation.md
    sha256: <approved-logic-digest>
    stage: validate-logic
    schema_version: distill-to-rutters/v1
body_schema: implementation-design/v1
```

The only allowed outcomes are `design-ready`, `design-gap`, `design-blocked`,
`partial`, and `failed`. Include exactly one fenced `distill-contract` YAML
block with `public_interface_design`, `files`, and `verification_commands`.
Every capability row names its public interface, version, availability, and
live evidence. The primary implementation unit visibly owns concrete Rutter
values, evolutions, transitions, composition, minimal dispenser construction,
and its public CLI; support owns mechanics but no routing policy.

## Production compatibility gate

Use the owned production compatibility validator used by the public
artifact route; `design-ready` is its success predicate, not a prose claim or a
test-only oracle. Probe the exact checked-out public exports and their full contracts. Do not
infer availability from an interface/version name or from private source. The
gate requires all of the following:

1. discover each public interface version from the checked-out root export and
   its resolved source blueprint; No interface version is predicted, pinned, or
   inferred from a similarly named interface;
2. operation-specific construction and execution semantic capabilities for
   public `officina.rutter.BaseRutter`, `officina.rutter.Voyage`, and
   `officina.rutter.VoyageDispenser` values, as applicable;
3. a Compass `binding.accepts` handoff that resolves an exact ready public
   construction capability and its complete nominal output type; and
4. one real bound instance advancing through an authorized public transition;
   dispatcher dry-run is insufficient.

For every discovered Rutter profile, resolve the root export's
`source_interface`, read that exact interface's declared version, and inspect
`contract.semantic_capabilities`. A qualifying capability row declares its
`role`, semantic operation, actual operation, exact `required_arguments` and
`optional_arguments` partition, semantic capability outcomes with exact class
and output references, and exact output. The operation, every argument, every
outcome, and the output must exist on that same resolved source interface. A
declared required or optional argument must match that argument's actual
`required` flag. The capability outcomes must equal the complete interface
outcome set, with identical classes and output mappings. A construction output
must be emitted by at least one declared successful construction outcome. A
construction row must have id `rutter-binding-construction`, role
`construction`, and semantic operation `construct`. An execution row must have
role `execution`, semantic operation `advance`, and a declared
`binding_argument`. Nominal `open`, `validate`, or `advance` operation names
without these declarations are insufficient.

Every qualifying construction output and execution binding/output declares a
structured nominal Python type with `language: python`, a nonempty
`qualified_class`, and a nonempty `schema` mapping. Compare the complete
nominal type and schema, not only `type.kind`. The concrete bound-Rutter output
must name `officina.rutter.BaseRutter` or another explicitly declared public
bound-Rutter nominal contract. Voyage and VoyageDispenser construction outputs
must name their public exported `officina.rutter.Voyage` and
`officina.rutter.VoyageDispenser` classes; their execution bindings must name
the same respective class.

For Compass, `binding.accepts` declares exactly `interface`, `version`,
`operation`, `output`, `capability`, and `nominal_type`. Resolve `interface`
through the checked-out Rutter root export, verify the exact source-interface
`version`, locate `operation` and `output`, require the operation-specific
`rutter-binding-construction` capability, and compare the complete declared
`nominal_type` to the producer output. A plain `kind: string`, nonexistent or
wrong output, mismatched qualified class or schema, execution-only producer, or
unrelated operation cannot constitute a handoff.

The probe must preserve every valid constructor candidate discovered from the
public root; do not select one by interface-name ordering. Resolve Compass
against the exact accepted tuple. Accept only when that tuple selects exactly
one compatible ready constructor, and reject unresolved ambiguity when
duplicate candidates match it.

Use `design-ready` only when every production compatibility probe passes. If any
probe is missing, write `design-blocked`, add one unavailable capability row
for every exact missing export or contract field, report the artifact's
gateway-computed digest, ask the user to validate the exact tuple, and pause.
Do not add core exports, adapters, or shims to make this stage pass.

Runtime discovery applies the same strict repository containment to module
roots, interface locators, and resolved symlinks. Absolute locators,
parent-directory locators, and symlink escapes are compatibility failures.

The checked-out Phase-A baseline is `hardening-complete; runtime-blocked`.
Direct Rutter construction and one real public transition are available, but
their declarations do not yet prove the required nominal public profiles. The
deterministic probe reports these exact absent fields:

- `officina.rutter.Voyage`;
- `officina.rutter.VoyageDispenser`;
- `rutter-root-export:base-rutter-construction:contract.semantic_capabilities`;
- `rutter-root-export:voyage-construction:contract.semantic_capabilities`;
- `rutter-root-export:voyage-execution:contract.semantic_capabilities`;
- `rutter-root-export:voyage-dispenser-construction:contract.semantic_capabilities`;
- `rutter-root-export:voyage-dispenser-execution:contract.semantic_capabilities`;
  and
- `using-compass.interface.default.contract.arguments.binding.accepts`.

This baseline is evidence for the current checkout, not authority to hard-code
a future result. Re-run every probe during a real approved distillation. Phase
A itself must not fabricate a live `05_implementation_design.md` without the
approved `01` through `04` artifact chain.

Do not compute or embed this artifact's own digest. After writing only this
artifact, return its path and typed outcome to the gateway. Report the
gateway-computed raw-byte SHA-256 and ask the user to validate the exact
`(path, digest, outcome)` tuple.
