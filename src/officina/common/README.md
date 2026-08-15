# `officina.common` utility guide

This guide is the first checkpoint for every developer before implementing cross-repository mechanics, validation, certification flows, or visualization features.

## How this was built

The entries below come from live repo searches (`rg`) in `src/officina` for concrete call sites.
Treat each row as:

1. what problem the utility solves,
2. where it is used now, and
3. where not to use it.

## Import surface

Prefer `officina.common` exports for most flows unless you need an advanced object graph:

1. `officina.common` exposes most parser/validator + blueprint helpers.
2. `officina.common.visualization` exposes graph rendering wrappers.
3. `officina.common.docstring` and `officina.common.visualization.from_docstring` are for deep docstring/graph extraction use.

If you add a new call site for one of these utilities, update this file in the same commit.

## Repository utility map (ordered by intent)

### 0) Configuration and JSON Schema validation

| Utility | Where it is used | Why this is the right tool | Don’t do this |
|---|---|---|---|
| `load_configuration`, `validate_configuration` (`configured_schema.py`) | docstring policy, certification hashing, recurring tasks, cloud-files | Validates compact YAML/JSON settings against the central natural-key configuration schema | Add JSON Schema syntax or a type discriminator to user configuration files |
| `configured_validator`, `ConfiguredSchemaBundle` (`configured_schema.py`) | schemas whose allowed values or required fields are supplied by configuration | Owns configuration-driven schema composition and its local-reference confinement | Route ordinary, non-configured domain schemas through this module |

### 1) Repository path safety

| Utility | Where it is used | Why this is the right tool | Don’t do this |
|---|---|---|---|
| `resolve_logical_module_path` (`repository_paths.py`) | `common/visualization/base_visualizer.py` (`resolve_graph_source`) | Accepts logical module identifiers (`pkg.module`) from CLI/config and resolves the actual source file | Hardcode package lookup rules or split module strings manually |
| `resolve_python_source_path` (`repository_paths.py`) | `common/visualization/base_visualizer.py` (`resolve_graph_source`) | Confirms only valid Python entrypoints (`.py` or package `__init__.py`) are accepted | Guess file suffixes with custom branching logic |
| `repository_relative_path` (`repository_paths.py`) | `common/certification_hashing.py`, `runtime/python_machine_interface_runner.py`, `common/blueprint_graph.py`, `common/certification_view.py` | Normalizes repository-bounded absolute path to repo-relative `Path` | Use `path.relative_to` directly and ignore symlink/drive ambiguities |
| `repository_relative_posix` (`repository_paths.py`) | `common/git_provenance.py`, `common/certification_view.py`, `runtime/python_machine_interface_runner.py` | Stores deterministic `/`-style paths in logs/JSON for cross-platform stability | Serialize raw OS-native separators into contracts or snapshots |
| `equivalent_root_relative_path` (`repository_paths.py`) | `dispatcher/core.py`, `common/blueprint_graph.py`, `common/certification_hashing.py` | Handles symlink/alias root equivalence before building stable relative locations | assume `relative_to` is enough in symlink-heavy repos |

### 2) Docstring parsing and validation

| Utility | Where it is used | Why this is the right tool | Don’t do this |
|---|---|---|---|
| `load_docstring_schema` / `resolve_docstring_schema_path` (`docstring/docstring_schema.py`) | `validators/docstring_validator.py`, `docstring/docstring_parser.py`, `docstring/docstring_validation.py` | Loads the active standard format once, then reuses everywhere | Hardcode schema constants in call sites |
| `parse_graph_block`, `parse_function_graphs`, `parse_pipeline` (`docstring/docstring_parser.py`) | `docstring/docstring_json_extractor.py`, `validators/docstring_validator.py` | Convert docstring blocks into structured AST-aligned specs and function/pipeline metadata | Regex-only extraction with no schema coupling |
| `parse_pseudocode_dependency_ref`, `parse_ownership_reference`, `parse_ownable_registry` (`docstring/docstring_parser.py`) | `validators/docstring_validator.py`, `docstring/docstring_validation.py`, `docstring/docstring_json_extractor.py` | Pull machine-readable references used by validators and graph extraction | Free-form parsing embedded in feature modules |
| `validate_edge_expression`, `validate_pipeline_docstring` (`docstring/docstring_validation.py`) | `docstring/docstring_validation.py`, `validators/docstring_validator.py` (via exported entry points) | Shared behavioral constraints for edge-like expressions and module pipeline sections | Duplicating same checks in each consumer |
| `check`, `check_graph_docstring`, `check_pipeline_docstring` (`docstring/docstring_validation.py`) | `validators/docstring_validator.py` | Single enforcement point used by certifier and scripts | Rebuilding linting rules per module |
| `check` re-export via `officina.common` | all CLI/validator entry points that import `officina.common` | Keeps import surface stable in feature modules | Importing private parser internals directly unless special behavior is needed |

### 3) Visualization core and orchestration

| Utility | Where it is used | Why this is the right tool | Don’t do this |
|---|---|---|---|
| `BaseRenderer` (`visualization/base_renderer.py`), `render_graph_html`, `write_graph_html`, `start_graph_server`, `reduce_graph_json_transitive_edges` | `visualization/from_docstring/visualizer.py` (through the orchestrator), `visualization/base_renderer.py` | Domain-neutral pipeline for JSON -> rendered HTML + optional server + optional simplification | Copying HTML/JS bootstrap code in each domain |
| `GraphSource`, `resolve_graph_source`, `BaseVisualizer` (`visualization/base_visualizer.py`) | `visualization/from_docstring/visualizer.py` (via `DocstringVisualizer`), `base_visualizer.py` | Standardizes source resolution, extractor contract, payload validation, and render output path decisions | Rebuilding source-extraction/render glue repeatedly |
| `BaseJsonExtractor` (`visualization/base_extractor.py`) | `visualization/from_docstring/json_extractor.py` | Defines a strict extractor contract (`extract(GraphSource) -> dict`) | Passing arbitrary dicts that bypass extractor semantics |
| `DocstringJsonExtractor` (`visualization/from_docstring/json_extractor.py`) | `visualization/from_docstring/visualizer.py` | Bridges docstring JSON extraction into the common `BaseVisualizer` flow | Duplicating docstring extraction outside the extractor contract |
| `parse_docstring_module`, `collect_defined_callables`, `infer_call_edges`, `to_dependency_json`, `to_docstring_dependency_json`, `extract_docstring_dependency_json` (`visualization/from_docstring/json_extractor.py`) | `visualization/from_docstring/visualizer.py` | Keeps docstring-to-graph transformation deterministic and reusable | Rebuilding module graph logic by hand in each visualization |
| `gather_modules`, `gather_modules_in_directory`, `build_docstring_graph`, `render_module_artifacts` (`visualization/from_docstring/visualizer.py`) | `visualization/from_docstring/visualizer.py` CLI entrypoint | Public orchestration entry for directory/module graph generation | Writing custom wrappers instead of delegating to these functions |

### 4) Certification, trust, and blueprints

| Utility | Where it is used | Why this is the right tool | Don’t do this |
|---|---|---|---|
| `collect_blueprints`, `iter_blueprints` (`blueprint_inventory.py`) | `dispatcher/core.py`, `runtime/python_machine_interface.py`, `blueprint_search.py` | Single discovery path for repository blueprint documents | Walking filesystem with local filters per caller |
| `load_repository_blueprint_graph` (`blueprint_graph.py`) | `dispatcher/core.py`, `blueprint_search.py`, `runtime/python_machine_interface.py` | Canonical graph build for blueprint traversal and interface resolution | Recomputing blueprint dependencies from file reads |
| `resolve_interface_authorization` (`blueprint_authorization.py`) | `dispatcher/core.py`, `common/interface_projection.py`, `blueprint_graph.py` | Resolves authorization policy consistently for interface calls | Copying authorization logic to every resolver |
| `compute_certification_basis_hash`, `compute_node_hash_states`, `_compute_node_hash_states`, `certification_target_postorder` (`certification_hashing.py`) | `certification_view.py`, `certification_hashing.py` | Canonical hash chain ordering and node dependency hash model | Writing ad-hoc hashing rules in new flows |
| `parse_certificate_log`, `CertificateRecordView` (`certificate_records.py`) | `certification_view.py` | Parses and normalizes certificate history before policy checks | Reading certificate logs without normalization |
| `check_export`, `check_authorization` and view wrappers (`certification_view.py`) | `dispatcher/core.py`, `interface_projection.py` | Central policy check interface that combines export and authorization semantics | Implementing one-off access decisions in call paths |
| `check_export` on resolver view subclasses (`common/certification_view.py`) | interface export checks in runtime/dispatcher context | Encapsulates per-interface export compatibility | Scattering export policies across consumers |

### 5) Repository-safe IO, provenance, and secret handling

| Utility | Where it is used | Why this is the right tool | Don’t do this |
|---|---|---|---|
| `read_regular_file_bytes`, `atomic_create_bytes`, `atomic_replace_bytes`, `AtomicWriteError` (`atomic_files.py`) | `certification_hashing.py`, `git_provenance.py`, `certificate_records.py`, `blueprint_inventory.py`, `oauth_json.py` | Safe file read/write with confinement and crash-safe write patterns | Using raw `open(..., "wb")` or `Path.write_bytes` where atomicity/confidence matters |
| `capture_git_snapshot`, `check_commit_readiness`, `run_git` (`git_provenance.py`) | `certification_hashing.py`, `certification_view.py` | Reproducible provenance blocks and commit readiness checks | Repeated inline git command wrappers in each module |
| `discover_repository_test_dirs`, `is_test_module` (`discover_tests.py`) | `validators/docstring_validator.py`, module-level validation flows | Shared test-root discovery and test-file classification | Duplicating test-dir logic in validators and tooling |
| `secret_store` (`secret_store.py`) | `certificate_records.py` (require/store/clear), blueprint package metadata references | Standard secret backend abstraction and namespace/key scoping | Accessing environment-only secrets directly in certification flow |
| `interface_projection` (`interface_projection.py`) | `common/interface_projection.py` + resolver-facing checks | Shared interface normalization and projection logic | Re-implementing interface-specific extraction rules |
| `toml_io` (`toml_io.py`) | Mostly defined in the common toolkit; no direct multi-module consumers today | Canonical TOML reader/writer helper for config persistence | Adding new TOML parsing behavior without using this helper |

## Practical reading order

1. Start with "Repository path safety" when touching any path inputs.
2. Use docstring utilities if you touch callable metadata, ownership, pseudocode, or validation.
3. Route graph work through `BaseVisualizer + BaseRenderer` first, then add a domain extractor.
4. Use certification/blueprint helpers whenever interface auth, export checks, or trust hashes are involved.
5. Use atomic I/O + provenance utilities for any operation that writes or inspects repo-trace state.
