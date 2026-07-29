# Docstring Contract (Lightweight)

This pipeline keeps docstring parsing/validation explicit and machine-checkable.

## 1) Policy + Syntax inputs
- `references/standards/docstring.standard.yaml`: semantic policy (`required` sections, lengths, checks, toggles).
- `references/standards/docstring.standard.lark`: parser grammar for docstring micro-syntax (edges, wraps, module dependencies).
- Keep behavior policy in YAML; keep punctuation/shape syntax in `.lark`.

## 1a) Dependency references in pseudocode (new)

`Pseudocode` and any configured `dependency_reference_sections` can mark explicit
dependency links with scoped markers:

- `@parse_node`
- `@NodeHashState(...)`
- `@CallsFromModule:parse_node(...)`
- `@InstantiationsFromModule:NodeHashState`

Rules:

- `@name` and `@name(...)` are accepted when the name is unambiguous across declaration
  sections.
- `@Section:name` is recommended when a dependency name appears in multiple declaration
  buckets (`CallsFromModule`, `InstantiationsFromModule`, `Wraps`, `NonInferableCalls`).
- References are parsed into `pseudocode_dependency_refs` and validated against those declaration
  buckets so ambiguity becomes a checker issue rather than a hidden assumption.

To include additional sections, set `callable.dependency_reference_sections` in
`references/standards/docstring.standard.yaml`.

The marker grammar is intentionally shared across declaration buckets. For a marker
to be unscoped (`@name`), the identifier must be unambiguous across all
declaration buckets (`CallsFromModule`, `InstantiationsFromModule`, `Wraps`,
`NonInferableCalls`). Use `@Section:name` when a name appears in multiple buckets.

Examples:

- `@NodeHashState` (unscoped, resolved uniquely)
- `@CallsFromModule:parse_node` (explicitly points at one declaration bucket)
- `@InstantiationsFromModule:NodeHashState`

## 2) Runtime flow
1. Loader reads the standard via `officina.common.docstring.load_docstring_schema(...)`.
2. Parser (`officina.common.docstring.docstring_parser`) parses raw docstring text:
   - callable docs -> `FunctionSpec`
   - module pipeline docs -> `PipelineSpec`
   - module AST scan -> `parse_function_graphs`
3. Validator (`officina.common.docstring.docstring_validation`) validates against policy and returns `ParserIssue` objects.
4. Issues flow back as structured codes/messages (`docstring.*`) to CI or local tooling.

### 2a) Checkers, groups, and semantics

`officina.validators.docstring_validator.validate_module_docstrings(...)` runs one or two checker layers:

- `syntax` (parse/format checks)
- `behavioral` (callable-to-code relation checks)
- `all` (all configured checks for non-test modules; for test modules this is normalized to `syntax`)

Why test files are special:

- `validate_module_docstrings(check_group="all")` currently resolves to `syntax` when the target path is inside `tests/`, `hooks/tests/`, `skills/*/tests`, or `skills/*/_rtx/tests`.
- For behavior checks on test modules, request `check_group="behavioral"` explicitly.

## 3) Key module entry points
- `officina.common.docstring.parse_graph_block(docstring)`
- `officina.common.docstring.parse_pipeline(docstring)`
- `officina.common.docstring.parse_function_graphs(ast_tree)`
- `officina.common.docstring.parse_ownable_registry(docstring)`
- `officina.common.docstring.validate_edge_expression(text)`

## 4) Key check entry points
- `officina.common.docstring.check_graph_docstring(docstring)`
- `officina.common.docstring.check_pipeline_docstring(docstring)`
- `officina.common.docstring.validate_pipeline_docstring(docstring)`
- `officina.common.docstring.check(docstring, kind="callable"|"pipeline"|"module"|"function"|"method"|"class")`

## 5) Why this shape
- Parsing focuses on extracting structured fields from docstrings.
- Validation focuses on quality/conformance and issue codes.
- Standard/grammar edits should drive most behavior changes without touching parser internals.
