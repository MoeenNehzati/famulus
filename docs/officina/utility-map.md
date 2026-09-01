# Current implementation ownership map

Use this guide after you know which mechanism you need and want to find its
current Python owner. It is a navigation map, not a second authority: package
`__init__.py` docstrings remain the authoritative file inventories, while
blueprints are the canonical machine-readable descriptions of logical boundaries
and relationships. Import the concrete owning module; package initializers are
documentation, not compatibility facades.

The grouping below describes the current implementation. Officina still lives
inside Famulus, and its eventual standalone package boundary is unsettled. The
groups therefore help readers find code; they do not declare a future package
layout or move authority between modules.

For conceptual background, use the [Overview](README.md) or [Getting
Started](getting-started.md). Use [Scaffolding](scaffolding/README.md) when
creating a node. The specialist links below explain the subsystems whose tables
only identify current owners.

## Officina core

These modules implement Officina's graph, contracts, authorization, validation,
execution, and projections.

| Concern | Owning modules | Relevance |
|---|---|---|
| Repository configuration and configuration-derived schemas | `officina.configuration.repository`, `officina.configuration.configured_schema` | Loads `officina.toml` and applies configuration-derived schema constraints. |
| Structured docstrings | `officina.docstring.parser`, `officina.docstring.policy`, `officina.docstring.validation` | Parses the repository format, resolves its policy, and enforces semantic constraints. See the [Docstring Contract](docstring.md). |
| Blueprint discovery and authorization | `officina.blueprints.inventory`, `officina.blueprints.graph`, `officina.blueprints.authorization` | Discovers registered nodes, constructs the canonical graph, and resolves export access. |
| Blueprint projections and process bindings | `officina.blueprints.projection`, `officina.blueprints.process_binding` | Produces consumer-facing interface records and compiled process invocations. |
| Blueprint search and templates | `officina.blueprints.search`, `officina.blueprints.template` | Searches repository metadata and validates template expansion. |
| Certification | `officina.certification.hashing`, `officina.certification.records`, `officina.certification.view` | Computes node hashes, manages certificate records, and evaluates currentness and authorization. |
| Standards | `officina.standards.extractor`, `officina.standards.query` | Resolves pinned standard closures and answers deterministic policy queries. |
| Interface routing and authorization | `officina.dispatcher.core`, `officina.dispatcher.direct_authorization`, `officina.dispatcher.direct_blueprints` | Resolves one declared interface, checks the crossed access policies, and compiles its process binding. |
| Machine-interface execution | `officina.runtime.python_machine_interface`, `officina.runtime.python_machine_interface_runner` | Runs a Python gateway in its own process under the confined importer. |
| Framework validators | `officina.validators.docstring_validator`, `officina.validators.snapshot` | Enforces the docstring contract and pins a repository view for validation. |
| Graph visualization | `officina.visualization.graph`, `officina.visualization.base_visualizer`, `officina.visualization.base_renderer` | Defines the graph model and the shared extraction-to-rendering pipeline. See [Visualization](visualization.md). |
| Blueprint visualization | `officina.visualization.from_blueprint` | Converts blueprint graphs into scoped, inspectable renderer payloads. |
| Docstring visualization | `officina.visualization.from_docstring` | Converts structured Python docstrings into dependency graphs. |

## Shared infrastructure

These modules provide narrow repository mechanics used across concerns. Their
presence in `officina` does not make their callers owners of the policy they
apply.

| Concern | Owning modules | Relevance |
|---|---|---|
| Atomic and repository-bounded file handling | `officina.common.atomic_files`, `officina.common.repository_paths` | Confined reads, atomic writes, logical-module resolution, and stable repository-relative paths. |
| TOML and Codex configuration | `officina.common.toml_io`, `officina.common.codex_toml` | Shared TOML serialization and structure-preserving Codex configuration updates. |
| Credentials and secrets | `officina.credentials.google`, `officina.credentials.oauth`, `officina.credentials.secret_store` | Resolves credential files, manages OAuth JSON, and accesses namespaced secrets. |
| Git provenance | `officina.git.provenance` | Captures commit readiness, provenance, and isolated repository snapshots. |
| Repository checks | `officina.repository.checks.discovery`, `officina.repository.checks.runner`, `officina.repository.checks.remote` | Discovers tests and coordinates local, snapshot, and remote checks. |

## Famulus-adjacent subsystems

These subsystems support Famulus operation around the Officina core. This is a
reader-facing distinction, not a settled extraction boundary.

| Concern | Owning modules | Relevance |
|---|---|---|
| Rutter execution | `officina.rutter.model`, `officina.rutter.engine`, `officina.rutter.storage`, `officina.rutter.runtime`, `officina.rutter.dispenser` | Defines immutable Charter/Fix/Reckoning values, persists strict authority, creates or opens named Voyages, and exposes mode-aware, run-scoped Voyage collections through a process-safe interface. See [Compass and Rutter](compass-rutter.md). |
| Agent launch | `officina.launchers.agent` | Resolves durable backend selection and builds the command a launcher or scheduled job executes. |
| Recurring jobs | `officina.recurring.control`, `officina.recurring.executor`, `officina.recurring.native`, `officina.recurring.healthcheck` | Registers and runs managed jobs, renders the native scheduler unit, and reports whether the schedule is live. |
| Host-session lifecycle | `officina.wakeup.policies`, `officina.wakeup.deadlines`, `officina.wakeup.store` | Decides whether a session is woken at a usage reset, when, and on what durable record. |

For path inputs, start with `officina.common.repository_paths`. For repository
policy, query `officina.standards.query`. For interface or trust decisions, use
the blueprint and certification owners instead of reproducing their logic in a
caller.

For schema roles and authority, see [Schemas](schema.md). For the durable
algorithm vocabulary, persistence model, and LLM-facing operating loop, see
[Compass and Rutter](compass-rutter.md).
