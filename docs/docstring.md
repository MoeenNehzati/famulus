# Docstring Contract (Lightweight)

This pipeline keeps docstring parsing/validation explicit and machine-checkable.

## 1) Policy + Syntax inputs
- `references/standards/docstring.standard.yaml`: semantic policy (`required` sections, lengths, checks, toggles).
- `references/standards/docstring.standard.lark`: parser grammar for docstring micro-syntax (edges, wraps, module dependencies).
- Keep behavior policy in YAML; keep punctuation/shape syntax in `.lark`.

## 2) Runtime flow
1. Loader reads the standard via `officina.common.docstring.load_docstring_schema(...)`.
2. Parser (`officina.common.docstring.docstring_parser`) parses raw docstring text:
   - callable docs -> `FunctionSpec`
   - module pipeline docs -> `PipelineSpec`
   - module AST scan -> `parse_function_graphs`
3. Validator (`officina.common.docstring.docstring_validation`) validates against policy and returns `ParserIssue` objects.
4. Issues flow back as structured codes/messages (`docstring.*`) to CI or local tooling.

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
