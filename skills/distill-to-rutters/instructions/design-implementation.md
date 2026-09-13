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
test-only oracle. The first release probes the exact checked-out public Python
surface and the public Compass process-binding contract. It requires all of the
following:

1. public exports for the Rutter authoring, Voyage, registry, status,
   validation, transition, terminal-result, and VoyageDispenser values used by
   generated code;
2. one real public Rutter constructed, bound as a Voyage, validated, advanced
   to the expected terminal successor, reopened, and observed in the same
   terminal state;
3. one real public VoyageDispenser constructed from callbacks, initialized with
   exactly one Voyage, and used to validate and advance that Voyage to the
   expected terminal successor; and
4. a required public Compass string binding plus an exact-version dependency on
   the exported Rutter dispenser interface and an executable `process-interface`
   direct-I/O declaration.

The generated entrypoint must name that exact process interface, version, and
fixed arguments. Do not guess aliases or inspect private runtime paths. Typed
semantic-capability profiles remain diagnostic hardening information for a
later release; their absence alone does not block this single-Voyage first
release because the production probe executes the public Python and process
contracts directly.

Use `design-ready` only when every production compatibility probe passes. If any
probe is missing, write `design-blocked`, add one unavailable capability row
for every exact missing export or contract field, report the artifact's
gateway-computed digest, ask the user to validate the exact tuple, and pause.
Do not add core exports, adapters, or shims to make this stage pass.

Runtime discovery applies the same strict repository containment to module
roots, interface locators, and resolved symlinks. Absolute locators,
parent-directory locators, and symlink escapes are compatibility failures.

The checked-out first-release baseline is `design-ready` only while those live
exercises and the Compass process-binding declaration pass. Re-run every probe
during a real approved distillation. Do not fabricate a live
`05_implementation_design.md` without the approved `01` through `04` artifact
chain.

Do not compute or embed this artifact's own digest. After writing only this
artifact, return its path and typed outcome to the gateway. Report the
gateway-computed raw-byte SHA-256 and ask the user to validate the exact
`(path, digest, outcome)` tuple.
