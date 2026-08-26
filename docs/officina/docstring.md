# Docstring Contract (Lightweight)

This pipeline keeps docstring parsing/validation explicit and machine-checkable.

## 1) Policy + Syntax inputs
- `references/standards-schema/docstring.standard.yaml`: semantic policy (`required` sections, lengths, checks, toggles).
- `references/standards-schema/docstring.standard.lark`: parser grammar for docstring micro-syntax (edges, wraps, module dependencies, strict pseudocode bullets).
- `src/officina/docstring/config.yaml`: repo-local config (`allowed_abs`, dependency section names, syntax toggles, and ordered path profiles).
- Keep behavior policy in YAML; keep punctuation/shape syntax in `.lark`.
- Users should not edit Python to tune docstring policy; repo-specific knobs belong in `config.yaml`.

## 1a) Module architecture

The docstring stack has four layers:

- `officina.docstring.policy` loads the portable standard,
  repo-local config, and path profiles into one effective policy object.
- `officina.docstring.parser` parses docstring syntax into
  typed IR objects. It should not own behavioral policy beyond recognizing
  configured section names.
- `officina.docstring.validation` checks docstring-local
  format rules: sections, Lark syntax, dependency rationale shape, resources,
  dataflow, and pseudocode structure.
- `officina.validators.docstring_validator` checks Python-module behavior:
  AST-observed repo calls/products, wrapper edges, dispatch grounding,
  ownership resolution, and check-group filtering.

The package gateway is `officina.docstring`; implementation-specific imports use
`officina.docstring.parser`, `officina.docstring.policy`, or
`officina.docstring.validation`.

## 1b) Repo dependency sections

Callable dependency declarations use these configured section names by default:

- `CallsFromRepo`
- `InstantiationsFromRepo`
- `Dispatches`

These sections are intentionally repo-level only. Do not list stdlib or
third-party helpers such as `deepcopy`, `Path`, `TemporaryDirectory`, `sha256`,
or `yaml.safe_load` in `CallsFromRepo` or `InstantiationsFromRepo`. The current
design does not add `CallsStdlib` or `CallsExternal`; ordinary library calls are
omitted so the documentation graph stays focused on repo structure.

The canonical dependency syntax is a YAML-like tree. Shared logical prefixes
should be nested once and flattened by the parser into dotted paths or ids:

```yaml
CallsFromRepo:
  ._node_certifier._v4_module_renames:
    why:
      transforms: "Maps legacy subject ids to canonical review targets."
  officina.common:
    repository_paths:
      repository_relative_path:
        why:
          transforms: "Normalizes candidate blueprint paths to repo-relative format."

InstantiationsFromRepo:
  ._node_certifier.V4LegacyReviewContext:
    why:
      constructs: "Constructs typed rows describing legacy skill interface claims."

Dispatches:
  skills.skill-certifier.source.audit-interface.interface.audit:
    why:
      dispatches: "Dispatches CLI invocation to the interface entrypoint."
```

Every dependency `why` must use exactly one graphable action key:

```yaml
why:
  validates: "Checks that candidate evidence matches reviewed input."
```

Allowed action keys are `reads`, `writes`, `transforms`, `validates`,
`constructs`, `dispatches`, `serializes`, `parses`, `computes`,
`orchestrates`, `raises`, and `misc`. Use `misc` only when none of the
specific keys fit; it requires a longer concrete explanation. The checker code
for this syntax is `docstring.dependency-why-action`.

Section-specific action sets are declarative policy in
`src/officina/docstring/config.yaml` under
`dependency_why.section_actions`. The default intent is:

- `calls`: operational actions such as `computes`, `parses`, `validates`,
  `reads`, `writes`, `transforms`, `orchestrates`, `dispatches`, `serializes`,
  and `misc`.
- `instantiations`: product actions such as `constructs`, `raises`,
  `transforms`, `serializes`, and `misc`.
- `dispatches`: only `dispatches`.

If a dependency is in the wrong section, the behavioral validator emits
`docstring.module-dependency-not-observed` for the declared section and
`docstring.module-dependency-undocumented` for the observed section. Those
messages name the section to move the dependency into.

Path validity is explicit:

- Relative dependency paths must start with `.`.
- Absolute dependency paths/ids must start with a root in `config.allowed_abs`.
- Bare paths such as `_node_certifier.foo`, `common.foo`, or `skill-certifier.source.audit-interface.interface.audit` are invalid.

With the repo default config, the allowed absolute roots are `officina` and
`skills`. The checker code for this portability rule is
`docstring.absolute-dependency-not-allowed`.

Repo dependency locality is also behavioral, not just syntactic. If a declared
call or instantiation resolves through imports to stdlib or third-party code,
the validator emits `docstring.repo-dependency-not-repo`. This is the checker
that keeps `CallsFromRepo` and `InstantiationsFromRepo` limited to repo-local
logical dependencies.

Wrapper edges are behaviorally checked because they are graph structure, not
prose decoration. If a callable directly returns or forwards exactly one
repo-local call and `Wraps` does not name that target, the validator emits
`docstring.wraps-missing-thin-wrapper`. If a callable has exactly one repo-local
call but is not structurally a thin wrapper, the validator emits
`docstring.single-repo-call-review` as an advisory: either document the target in
`Wraps` or make the pseudocode/dependency rationale explain the local work around
the call.

`InstantiationsFromRepo` means product dependency, not class constructor. Use it
when a repo dependency's returned or raised value is carried forward as a
semantic value, result, error, copy, serialization, or container. Pseudocode must
show that product position with constructor/product call syntax:

```text
- record = build_record(fields)
- return build_payload(inputs)
- raise CertificationError(message)
- yield make_item(row)
```

Profiles can emit `docstring.instantiation-product-unshown` when an
`InstantiationsFromRepo` target is not shown in one of those product positions.
This stricter check is available for small functions whose compact pseudocode
can name every product edge without exceeding the pseudocode size limits.
It emits `docstring.instantiation-why-action` when the rationale uses an
operation action such as `reads`, `validates`, `parses`, `computes`,
`dispatches`, or `orchestrates` instead of a product action such as
`constructs`, `raises`, `transforms`, or `serializes`.

Dependency sections have priority: `Wraps` outranks `InstantiationsFromRepo`,
which outranks `CallsFromRepo`. The validator emits
`docstring.dependency-section-overlap` when one target is declared in multiple
priority-ranked sections; graph extraction keeps only the highest-priority edge.

Dispatch declarations are grounded behaviorally:

- `docstring.dispatch-unresolved`: declared ID is not a known public interface ID.
- `docstring.dispatch-not-observed`: declared ID is absent from dispatcher call literals when a literal dispatch is statically visible.
- `docstring.dispatch-undocumented`: a dispatcher call literal names an ID not listed in `Dispatches`.

The validator treats `skill.interface.default` and
`skills.skill.interface.default` as equivalent logical spellings, but the
portable docstring form should use the allowed root, e.g.
`skills.skill-certifier.source.audit-interface.interface.audit`.

## 1c) Resource and dataflow sections

Use `Resources` for non-call dependencies that matter to behavior or graphing:

```yaml
Resources:
  docstring.config:
    kind: file
    access: read
    why:
      reads: "Loads repo-local policy roots."
```

Use `Dataflow` for compact producer/consumer edges, not execution order:

```yaml
Dataflow:
  - from: docstring.config
    to: .load_docstring_schema
    kind: config
    why:
      transforms: "Feeds repo-local roots into effective policy."
```

This is separate from flow. `Dataflow` answers what information/resource moves
between logical nodes; execution ordering remains in `Pseudocode` for now.

## 1d) Strict pseudocode syntax

`Pseudocode` is a compact execution sketch language, not prose. Every bullet is
one graph node; every sigil resolves to one declared dependency; every indent
creates a control edge.

Allowed operation forms:

- `name = @ref(args)` or `@ref(args)` for `CallsFromRepo`
- `name = #ref(args)` or `#ref(args)` for `Dispatches`
- `name = Ref(args)`, `return Ref(args)`, `raise Ref(args)`, or
  `yield Ref(args)` for `InstantiationsFromRepo` product edges
- `set name = expression` for local computation
- `read resource_id` / `write resource_id` for `Resources`
- `if condition:`, `while condition:`, `for name in expression:`, and `else:`
- `return expression`, `raise expression`, `continue`, `break`

Only `- ` bullets are allowed. Indentation is exactly two spaces per level.
Free prose lines and old scoped refs such as `@CallsFromRepo:name` are invalid.

Example:

```text
Pseudocode
----------
- graph = @load_repository_blueprint_graph(root)
- for node in graph.nodes:
  - authority = @resolve_node_authority(node)
  - if authority is missing:
    - result = CertificationResult(status=`fail_closed`)
    - continue
  - review = #skill-certifier.source.audit-interface.interface.audit(node)
- return result
```

Reference resolution is bucket-specific:

- `@ref` resolves inside `CallsFromRepo`.
- `#ref` resolves inside `Dispatches`.
- `Ref(args)` in assignment, `return`, `raise`, or `yield` resolves inside
  `InstantiationsFromRepo`.

Resolution uses exact match first, then unique dot-segment suffix matching. If no
dependency matches, the checker emits `docstring.pseudocode-ref-unresolved`. If
multiple dependencies match, it emits `docstring.pseudocode-ref-ambiguous`.

Graphable pseudocode should avoid placeholder dataflow. Configured checks can
reject generic assignment targets or arguments such as `value`, `out`, `state`,
`args`, and `data`, and can require assigned dependency outputs to be used by a
later step. Related checker codes are:

- `docstring.pseudocode-placeholder-variable`
- `docstring.pseudocode-placeholder-argument`
- `docstring.pseudocode-output-unused`

Repeated-template detection can also run at module scope. It normalizes
callable and dependency names, then reports copied prose templates with
`docstring.repeated-template`.

## 1e) Repo-local profiles

Profiles in `src/officina/docstring/config.yaml` are ordered path
overrides. Later matching profiles override earlier matching profiles for the
settings they mention. Profile names are labels only; behavior comes from
`applies_to`, `callable`, and `checks`.

Current repo policy:

- `production_graphable` applies rich graphable documentation checks to non-test Python under `src/**/*.py` and `skills/*/_rtx/**/*.py`.
- `tests_lightweight` applies after the production profile for test paths and relaxes callable docstring requirements.
- Test files may have ordinary pytest prose docstrings; if absent, callable docstrings are not required.
- Production files should use the full graphable format.

Supported profile shape:

```yaml
profiles:
  production_graphable:
    applies_to:
      - src/**/*.py
      - skills/*/_rtx/**/*.py
    checks:
      dependency_why_action: true
      pseudocode_output_use: true
      repeated_template_detection: true
  tests_lightweight:
    applies_to:
      - tests/**/*.py
      - skills/*/_rtx/tests/**/*.py
    callable:
      require_docstrings: false
      required_sections: []
      min_pseudocode_steps: 0
    checks:
      pseudocode_output_use: false
      repeated_template_detection: false
```

Config loading validates profile keys so misspelled checks fail early instead
of silently disabling enforcement. Supported check keys are
`dependency_why_action`, `instantiation_product_pseudocode`,
`pseudocode_dataflow`, `pseudocode_output_use`, and `repeated_template_detection`.

The standard includes phrase-level guards for known filler and a
`docstring.repeated-template` behavioral check for generated boilerplate. The
validator intentionally avoids broad single-phrase bans that would require large
manual rewrites without proving semantic usefulness.

## 1f) Canonical visualization pipeline

Visualization follows a two-stage path:

1. **Extract graph JSON from docstrings**
- `officina.visualization.from_docstring.json_extractor.extract_docstring_dependency_json(module_path)`
- Or call the public helpers in `officina.visualization.from_docstring`
  for custom extraction/rendering control.
2. **Render JSON through shared core**
  - `officina.visualization.from_docstring` prepares docstring graph
payloads and renders them through `officina.visualization.ElkHtmlRenderer`.
3. **Compatibility**
- `officina.visualization.from_docstring` exposes the extractor contract
  and now contains `DocstringCoreJsonExtractor` for `BaseGraphBuilder`.

### 1f) Core serving API

- `officina.visualization.start_graph_server(directory, host='127.0.0.1', port=8765, ...)`
  - starts a background `http.server` subprocess for a directory
  - returns `GraphServer(process, host, port, directory)`
  - exposes `.url` for opening the viewer and `.stop()` for teardown.

## 2) Runtime flow
1. Loader reads the standard via `officina.docstring.load_docstring_schema(...)`.
2. Repo config from `config.yaml` is injected into the portable policy.
3. Module validation applies ordered path profiles to produce the effective per-file schema.
4. Parser (`officina.docstring.parser`) parses raw docstring text:
   - callable docs -> `FunctionSpec`
   - module pipeline docs -> `PipelineSpec`
   - module AST scan -> `parse_function_graphs`
5. Validator (`officina.docstring.validation`) validates against policy and returns `ParserIssue` objects.
6. Issues flow back as structured codes/messages (`docstring.*`) to CI or local tooling.

### 2a) Checkers, groups, and semantics

`officina.validators.docstring_validator.validate_module_docstrings(...)` runs one or two checker layers:

- `syntax` (parse/format checks)
- `behavioral` (callable-to-code relation checks)
- `all` (all configured checks for non-test modules; for test modules this is normalized to `syntax`)

Why test files are special:

- `validate_module_docstrings(check_group="all")` currently resolves to `syntax` when the target path is inside `tests/`, `hooks/tests/`, `src/officina/wakeup/tests/`, `skills/*/tests`, or `skills/*/_rtx/tests`.
- The `tests_lightweight` profile also disables callable docstring requirements and full graph section requirements for test paths.
- For behavior checks on test modules, request `check_group="behavioral"` explicitly.

Why class files are special:

- Class docstrings describe the class interface/responsibility.
- Method-body dependency observation is not rolled into class docstrings by default.
- Method dependencies belong on the method docstrings that actually use them.

## 3) Key module entry points
- `officina.docstring.parse_graph_block(docstring)`
- `officina.docstring.parse_pipeline(docstring)`
- `officina.docstring.parse_function_graphs(ast_tree)`
- `officina.docstring.parse_ownable_registry(docstring)`
- `officina.docstring.validate_edge_expression(text)`

## 4) Key check entry points
- `officina.docstring.check_graph_docstring(docstring)`
- `officina.docstring.check_pipeline_docstring(docstring)`
- `officina.docstring.validate_pipeline_docstring(docstring)`
- `officina.docstring.check(docstring, kind="callable"|"pipeline"|"module"|"function"|"method"|"class")`

## 5) Why this shape
- Parsing focuses on extracting structured fields from docstrings.
- Validation focuses on quality/conformance and issue codes.
- Standard/grammar edits should drive most behavior changes without touching parser internals.
