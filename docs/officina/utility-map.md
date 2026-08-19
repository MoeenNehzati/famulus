# Officina utility map

Use this guide to locate shared repository mechanics. Package `__init__.py`
docstrings are the authoritative file inventories; this page explains which
package to inspect for a task.

Import the concrete owning module. The package initializers are documentation,
not compatibility facades.

| Task | Owning modules | Relevance |
|---|---|---|
| Atomic and repository-bounded file handling | `officina.common.atomic_files`, `officina.common.repository_paths` | Confined reads, atomic writes, logical-module resolution, and stable repository-relative paths. |
| TOML and Codex configuration | `officina.common.toml_io`, `officina.common.codex_toml` | Shared TOML serialization and structure-preserving Codex configuration updates. |
| Repository configuration and configured schemas | `officina.configuration.repository`, `officina.configuration.configured_schema` | Loads `officina.toml` and applies configuration-derived schema constraints. |
| Structured docstrings | `officina.docstring.parser`, `officina.docstring.policy`, `officina.docstring.validation` | Parses the repository format, resolves its policy, and enforces semantic constraints. |
| Blueprint discovery and authorization | `officina.blueprints.inventory`, `officina.blueprints.graph`, `officina.blueprints.authorization` | Discovers registered nodes, constructs the canonical graph, and resolves export access. |
| Blueprint projections and process bindings | `officina.blueprints.projection`, `officina.blueprints.process_binding` | Produces consumer-facing interface records and compiled process invocations. |
| Blueprint search and templates | `officina.blueprints.search`, `officina.blueprints.template` | Searches repository metadata and validates template expansion. |
| Certification | `officina.certification.hashing`, `officina.certification.records`, `officina.certification.view` | Computes node hashes, manages certificate records, and evaluates currentness and authorization. |
| Rutter execution | `officina.rutter.model`, `officina.rutter.engine`, `officina.rutter.storage`, `officina.rutter.runtime` | Defines immutable Charter/Fix/Reckoning values, binds direct state mappings, persists strict authority, and creates or opens named voyages. |
| Credentials and secrets | `officina.credentials.google`, `officina.credentials.oauth`, `officina.credentials.secret_store` | Resolves credential files, manages OAuth JSON, and accesses namespaced secrets. |
| Git provenance | `officina.git.provenance` | Captures commit readiness, provenance, and isolated repository snapshots. |
| Standards | `officina.standards.extractor`, `officina.standards.query` | Resolves pinned standard closures and answers deterministic policy queries. |
| Repository checks | `officina.repository.checks.discovery`, `officina.repository.checks.runner`, `officina.repository.checks.remote` | Discovers tests and coordinates local, snapshot, and remote checks. |
| Graph visualization | `officina.visualization.graph`, `officina.visualization.base_visualizer`, `officina.visualization.base_renderer` | Defines the graph model and the shared extraction-to-rendering pipeline. |
| Blueprint visualization | `officina.visualization.from_blueprint` | Converts blueprint graphs into scoped, inspectable renderer payloads. |
| Docstring visualization | `officina.visualization.from_docstring` | Converts structured Python docstrings into dependency graphs. |

For path inputs, start with `officina.common.repository_paths`. For repository
policy, query `officina.standards.query`. For interface or trust decisions, use
the blueprint and certification owners instead of reproducing their logic in a
caller.

For the vocabulary, ownership boundaries, lifecycle, persistence model, and
LLM-facing operating loop, see [Compass and Rutter](compass-rutter.md).
