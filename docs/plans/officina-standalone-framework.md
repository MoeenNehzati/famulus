# Officina: refactor into a standalone framework

Status: design draft as of 2026-07-26. Describes splitting `officina` —
currently the dispatcher/blueprint/certification machinery living inside
this repo (`src/officina/`) — into its own standalone, separately
pip-installable package. Famulus (this repo's product) becomes a consumer
of officina rather than its container. No implementation detail — the goal
is to fix what moves, how it ships, and what the resulting interface does,
so gaps can be found before building anything.

There is currently no `pyproject.toml` at the repo root and nothing is
packaged for distribution today (`script_dispatcher/` is the one exception
— see below). This is a from-scratch split, not a repackaging of an
existing release.

## 1. Repo separation — what moves, what stays

Facts below come from a grep of `officina` imports across the tree and a
pass over `references/`, `skills/`, and top-level tooling directories.
Items marked **flagged** are genuinely ambiguous and need a decision, not
an inference.

### Moves to officina

- **`src/officina/`** in full: `dispatcher/` (cli, core, platforms),
  `runtime/` (Python machine-interface execution), `common/` (blueprint
  graph/inventory/template, certificate records/hashing/view, git
  provenance, interface projection/migration, process-binding compiler,
  atomic files, repository paths, secret store, TOML/OAuth I/O), and
  `blueprint_search.py`.
- **`references/blueprint/`, `references/certification/`,
  `references/skill-standards/`, `references/standards/`** — these define
  the blueprint schema, node-hash/certification policy, skill-authoring
  guidelines, and the versioned standard schema. All are officina
  conventions, not Famulus product content.
- **The subset of `validators/` that imports officina**:
  `cross_platform.py`, `platform_neutral.py`, `portable_dates.py`,
  `toml_io_boundary.py`. These back `officina check`.
- **The validator-runner engine itself**, i.e. `validators/runner.py`'s
  discovery-and-execution machinery (not the Famulus-specific validator
  files it currently discovers). Read directly: it already discovers
  validators from multiple registered "packages" —
  `_VALIDATOR_PACKAGES = [("repo", validators/), ("skill-maker",
  skills/skill-maker/validators/)]` — dynamically loads each `*.py`'s
  `validate(root) -> list[str]`, and merges the findings. That
  package-discovery-and-merge mechanism is exactly "run officina's
  built-in validators plus whatever validator package(s) the consuming
  project registers," it just isn't labeled officina-vs-consumer today.
  This engine is what `officina check` should be built on (§3): officina
  ships its own built-in validator package (the four files above, plus
  `skills/skill-maker/validators/*`, which also imports officina), and
  exposes a registration point for a consuming project's own validator
  package — Famulus's own `validators/*.py` files (below) become that
  project package, not a hardcoded second entry.
- **The git pre-commit wiring for validators.** `.githooks/pre-commit`
  calls `python3 validators/runner.py` as one of its steps, and
  `.githooks/skill/check-blueprints` / `check-dependencies` /
  `check-names` / `check-runtime-files` are thin wrappers that call the
  same runner with specific validator IDs. Only this validator-invocation
  piece is officina's — the rest of `.githooks/pre-commit` (PROFILES.md
  regeneration, doc-artifact regeneration, README preview, gitleaks scan,
  running the Famulus test suite) is Famulus-specific and stays. The
  actionable point: `officina check` should be directly callable from any
  repo's own pre-commit hook (this repo's included), so a project gets
  "officina's validators + my validators, enforced pre-commit" the same
  way whether it's this repo or an external officina consumer — mirroring
  the `SessionStart` hook wiring above but for the git-commit lifecycle
  instead of the host-session lifecycle.
- **`script_dispatcher/`** — already has its own `pyproject.toml` and
  exists solely to re-export `officina.dispatcher.core`. It should almost
  certainly be absorbed into officina directly (its entire purpose is
  officina's dispatcher) rather than kept as a second package that
  shadows it. **Flagged**: confirm nothing outside officina depends on
  `script_dispatcher` specifically as a separate import path before
  retiring it.
- **Tests exercising the above**: the ~24 `tests/test_officina_*.py` and
  related files (`test_blueprint_inventory.py`, `test_blueprint_search.py`,
  `test_dispatcher_route_smoke.py`, `test_interface_injection_migration.py`,
  `test_interface_projection.py`, `test_node_certification_hashing.py`,
  `test_process_binding_compiler.py`, the `validate_*.py` scripts, and
  `test_support/git_repository.py`).
- **Framework-authoring skills**: `skill-maker`, `skill-certifier`,
  `regenerate-blueprints` — per the interface decisions in §3, these are
  the two (three, counting regenerate-blueprints) skills officina ships as
  its own plugin bundle. `skill-drift` retires outright; its function
  becomes `officina drift`.
- **`hooks/` and `llmhooks/` in full.** Verified by reading the code (an
  earlier pass over this document wrongly cleared these as Famulus-side —
  a plain grep for the literal string `officina` missed the coupling,
  which runs through `script_dispatcher` instead): `llmhooks/
  inject_dispatcher_context.py` exists purely to tell the host session at
  `SessionStart` how to invoke the dispatcher and to warn when it's
  missing; `llmhooks/registry.py` and `llmhooks/lib/cross_host.py` are
  generic per-host hook-installation scaffolding with no Famulus-specific
  content; `hooks/inject_dispatcher_context.py` + `hooks/hooks.json` are a
  plugin-mode compatibility shim that does nothing but call into
  `llmhooks/`. None of this teaches a session anything about Famulus's own
  skills — it's the mechanism by which *any* officina-based package makes
  its dispatcher visible to a host session, so it belongs to officina.
  `officina skills install --host HOST` should wire up this
  dispatcher-context hook as part of registering with a host, not just
  drop skill files — the framework should set this up even for a package
  with no other custom hooks, since without it a host session has no way
  to learn the dispatcher exists.

### Stays in Famulus

- All personal-assistant / research skills: `bib-audit`, `cloud-files`,
  `connect-google`, `daily-plan`, `email-client`, `email-triage`,
  `find-handoff-candidates`, `fix-bisync`, `formal-prose-review`,
  `g-calendar`, `get-weather`, `latex-workshop`, `list-manager`,
  `loose-mode`, `make-tex-docstring`, `math-dependency-graph`,
  `notation-review`, `pdf-to-markdown`, `prepare-handoff`, `proof-audit`,
  `recurring-tasks`, `technical-flow-review`, `tight-mode`,
  `tool-applicability`, `wrap-up`.
- `workers/` (assistant, coauthor, collab personas).
- **The rest of `validators/`** — `contributor_docs_contract.py`,
  `generated_skill_docs.py`, `personal_info.py`, `readme_user_contract.py`,
  `skill_md_body.py`, `skill_runtime_doc_references.py`,
  `skill_runtime_files.py`, `skip_hygiene.py`, `standard_documents.py`,
  `user_docs_cover_blueprints.py`. None import officina; these check
  Famulus-specific contracts (README claims, personal-info hygiene, doc
  coverage). This becomes Famulus's own validator package, registered with
  officina's validator-runner engine per §1's note above, rather than
  living inside officina.
- `.githooks/pre-commit`'s non-validator steps (doc/README regeneration,
  gitleaks scan, running Famulus's test suite) and `.githooks/skill/*` as
  repo-specific wrapper scripts — these call into officina's validator
  engine but the orchestration script itself is Famulus's.
- `references/document-standards/` — prose/document authoring conventions
  for the writing skills; not an officina schema.
- `graphs/`, `docs_tooling/` — confirmed zero officina imports and no
  dispatcher-related content; these are Famulus-side tooling (diagram
  rendering, doc catalog generation).
- Product docs describing Famulus features (installation, launchers, skill
  catalog) rather than the officina/blueprint architecture itself.

### Second pass: infrastructure missed the first time

Found by hunting specifically for the `hooks/`/`llmhooks/` failure mode —
things referenced by path/filename elsewhere without an `officina` import
to grep for.

- **`scripts/search_blueprints.py`** — directly imports
  `officina.blueprint_search` and wasn't named anywhere above despite the
  module it calls being covered. It's the CLI front-end for
  `blueprint_search.py`, i.e. an early version of `officina query` (§3), so
  it moves with officina.
- **`scripts/migrate-blueprints-v4.py`** is *not* an officina-architecture
  item, despite importing `officina.common.interface_injection_migration`
  — correcting an inconsistency: §3 already puts that migration module
  out of scope for the shipped interface as transitory, one-time tooling,
  so its driver script doesn't belong under "moves to officina" either.
  It only needs to not be deleted out from under the migration module
  before the one-time conversion runs; neither script is part of
  officina's long-term shape.
- **`.codex-plugin/plugin.json`** declares `"hooks": "./hooks/hooks.json"`
  — the Famulus Codex plugin manifest wires in the dispatcher-context hook
  directly by path. Moving `hooks/` to officina means this manifest must
  be updated to point at wherever officina's plugin bundle puts it (or
  Famulus's own `officina skills install` registration produces this
  wiring automatically, per §3 — needs a decision on which).
- **`docs/scaffolding/README.md`** — pure officina documentation
  (blueprint.yaml ownership, `dispatcher --caller-skill` invocation,
  `skill-certifier`/`skill-drift` semantics), not listed in the original
  docs sweep. Moves with officina.
- **`scripts/run-python-tests.py`** and **`.github/workflows/python-tests.yml`**
  — both hardcode paths into officina's territory: the test runner lists
  `hooks/tests` and `tests/test_officina_*` explicitly in its test-suite
  definitions, and CI directly invokes `validators/runner.py` (§1's
  validator engine, moving to officina) and names
  `tests/test_officina_secret_store.py` by path. Neither is itself
  officina code, but both silently break if the split happens without
  updating them — CI is not "generic Famulus CI plus separately-owned
  officina CI," it's currently one undifferentiated pipeline.
- **`lessons/`** — a shared, dated engineering-lessons journal. Some
  entries happen to discuss dispatcher/blueprint-syncer behavior, but a
  journal entry isn't infrastructure for developing a skill library — it's
  a record about developing one. Not officina's concern; stays with
  Famulus regardless of what it discusses. (This is the scope test for
  the rest of this section too: officina is the infrastructure a skill
  library needs to exist and function — design/scaffolding, hooks, tests,
  validators, dispatch, certification — not anything that merely mentions
  that infrastructure.)

### Flagged — needs an explicit decision, not assumed either way

- `git-workflow`, `install-assistant-tools`, `initialize-tdd`,
  `hook-maker`, `refactor-skills`, `update-standards` — these are
  general dev-workflow or skill-framework-adjacent skills that aren't
  personal-assistant content but also aren't required by officina's core
  interface (§3 only names skill-maker/skill-certifier/
  regenerate-blueprints as officina's shipped skills). Each needs a
  case-by-case call: does it belong to officina (useful to any
  officina-based project) or to Famulus (specific to how this repo does
  development)?
- `docs/architectural-principles.md`, `docs/skills.md`,
  `docs/installation.md` — plausibly describe either the officina
  architecture or the Famulus product depending on framing; not
  determined from filename alone.

## 2. Packaging and distribution

- **Officina ships as its own PyPI-installable package** with a
  `pyproject.toml` declaring a console entry point (`officina`) bound to
  the CLI described in §3, plus package data covering the bundled
  skill/plugin assets (`skill-maker`, `skill-certifier`,
  `regenerate-blueprints`) so `officina skills install` can find them
  inside its own installed distribution without any repo checkout.
- **Officina-based libraries (e.g. Famulus) depend on officina via a
  normal Python dependency**, not a vendored copy. Famulus gets its own
  `pyproject.toml` declaring `officina` as a dependency and bundling its
  own skills as package data, following the exact same
  `pip install X && officina skills install X --host HOST` pattern
  officina uses on itself. This is what makes the two-line install in §3's
  `officina skills` group work identically for officina and any consumer.
- **Version alignment** is officina's job, not pip's: `officina version`
  reports the installed package version against what's currently synced to
  each host, so a `pip install --upgrade` that isn't followed by
  `officina skills sync` is visible rather than silently stale.
- **Nothing here changes who owns building or publishing wheels** — that
  stays with standard Python build tooling and CI, per §3.

Open question: does Famulus's split-out require a *new* git repository for
officina, or can it stay in this repo as a separately-packaged
subdirectory (a monorepo with two publishable packages)? The `pyproject.toml`
mechanics above work either way, but it changes how `references/` and
`skills/*` physically move versus merely getting separate package
manifests. Needs a decision before the repo-separation work in §1 is
executed.

## 3. The officina interface

Every verb falls into exactly one of two buckets:

- **Deterministic repository operations** — mechanical, requires no model
  judgment, safe to run unattended. These become `officina` CLI verbs.
- **Judgment-requiring workflows** — requires an LLM to reason about
  content, intent, or correctness. These stay as skills invoked through a
  host agent (Claude, Codex, etc.), never as CLI verbs.

Anything that doesn't cleanly fit one bucket is a sign the interface needs
more thought, not a reason to force it into the CLI.

### `officina skills` — plugin delivery to a host

Registers or removes the skill/plugin assets bundled inside an *already
pip-installed* Python distribution, for a specific agent host. This
subgroup does not touch Python packages themselves — `pip`/`uv` remain
solely responsible for installing, upgrading, or removing the
distribution. `officina skills` only bridges "a Python package is
installed" to "a host knows about its skills."

- **`officina skills install [PACKAGE] --host HOST`** — finds the
  skill/plugin assets bundled in the named installed distribution
  (default: `officina` itself) and registers them with the given host for
  the first time. For officina itself, this also wires up the
  dispatcher-context `SessionStart` hook (§1: what was `hooks/`/
  `llmhooks/`) so the host session learns the dispatcher exists — every
  officina-based package gets this regardless of whether it ships any
  other custom hooks.
- **`officina skills sync [PACKAGE] --host HOST`** — replaces whatever the
  host currently has registered for `PACKAGE` with the assets bundled in
  the currently installed version. This is the update path: run after
  `pip install --upgrade PACKAGE` to bring the host's copy in line.
- **`officina skills uninstall [PACKAGE] --host HOST`** — removes
  `PACKAGE`'s plugin registration from the given host. Does not uninstall
  the Python package.
- **`officina skills list`** — shows every skill package currently
  registered, which version's assets are registered, and with which
  host(s). Read-only, cross-host — the one subcommand in this group that
  isn't scoped to a single `--host`.

**Open question:** `install` vs `sync` currently overlap when the plugin
is already registered. Needs a decision on whether `install` is idempotent
(and `sync` collapses into it), or whether the two stay distinct with
`install` refusing to act on an existing registration.

### `officina check [NODE...]`

Runs the deterministic validators applicable to the given node(s) (or the
whole repository if none given): blueprint schema validity, naming
convention checks, structural consistency between a skill's declared
interfaces and its actual behavior sources, and any other rule evaluable
purely from repository state without judgment calls. It does not touch
certificates or signing.

Composes two sources, per §1's validator-runner note: officina's own
built-in validator package, and any validator package(s) the consuming
project registers (e.g. Famulus registering its own `validators/*.py`
checks). A project's pre-commit hook calls `officina check` directly, so
external officina consumers get the same "officina's validators + my
validators, enforced pre-commit" behavior this repo has today via
`validators/runner.py`.

### `officina drift [NODE...]`

Reports whether each node's certificate is still current against the
node's present hashed inputs. This replaces the `skill-drift` skill:
determining whether a hash matches a recorded hash is mechanical. It only
*reports* drift; it never signs or re-certifies — that stays exclusively
inside `skill-certifier`.

### `officina graph`

Constructs and displays the architectural graph of the repository from its
blueprints: nodes, their declared interfaces, and the dependency/ownership
edges between them. This is the structural model `check`, `drift`, and
`query` all read from.

### `officina query [FILTER...]`

Runs a structured filter/select query over the same blueprint data `graph`
assembles — e.g. "which interfaces declare a given capability," "which
nodes depend on node X," "which skills export interface Y." Where `graph`
answers "what does the whole architecture look like," `query` answers
"give me the subset matching this predicate." Standalone verb, not a
`graph` subcommand, for brevity.

### `officina dispatch <INTERFACE> [ARGS...]`

Resolves a declared machine interface (by ID/version) to its bound
behavior and executes it with the given arguments. The mechanical "run
this thing" step, with no judgment about *which* thing to run or *whether*
it's correct to run.

### `officina doctor`

Self-diagnostic: confirms the Python installation is sound, bundled
resources (blueprints, schemas, templates) are present and readable, the
dispatcher can resolve interfaces, and host plugin registrations aren't
stale or broken. The "why isn't officina working" first step, distinct
from `check` (which diagnoses the *repository*, not the officina
installation).

### `officina version`

Reports the installed officina package version and, for each registered
host, the version of the plugin assets currently synced to it.

### Workflows that remain skills (not CLI verbs)

- **`skill-maker`** — creates libraries, nodes, and blueprints. Requires
  judgment about what a new skill should contain and how it should be
  described.
- **`skill-certifier`** — performs certification: verifies a node
  semantically (not just structurally) and signs its certificate.
  Certificate-signing-key provisioning and rotation live here too, as an
  internal part of the signing process — deliberately not a separate CLI
  verb or separate skill action, since signing authority should have
  exactly one entry point.
- **`regenerate-blueprints`** — regenerates a skill's `blueprint.yaml` from
  its current state. Requires LLM assistance to produce a blueprint that
  reads correctly.

`skill-drift` is retired outright: its one function is now `officina
drift`.

### Explicitly out of scope for this interface

- Python package install/upgrade/removal — `pip`, `uv`, etc.
- Building wheels — standard Python build tooling.
- Publishing wheels and host plugins — registry/marketplace tooling.
- Source control and releases — Git and CI.
- Legacy blueprint-format migration (`interface_injection_migration.py`
  and its standalone script today) — transitory tooling for a one-time
  conversion; not part of the shipped interface.

Removed relative to earlier CLI sketches: `new`, `develop`, `certify`,
`inspect`, `build`, `publish` — each either folded into a skill above or
pushed to one of the tools listed here.

## 4. Open design questions

Consolidated from inline flags above, plus questions raised by this
document but not yet answered anywhere in it. Ranked roughly by how much
else depends on the answer.

### Load-bearing — nearly every verb in §3 depends on these

- **Where does officina's state live once it's pip-installed?** Today,
  certificates/secrets/blueprint state sit under
  `src/officina/common/.certificates`, `.../blueprints`, `.../secrets` —
  repo-relative, resolved via `repository_paths.py`, from *inside* the
  project it manages. Once officina is a separately-installed package, it
  no longer lives inside the project. Does it auto-discover the enclosing
  git repo as "the project," or does a consuming project need to declare
  its root explicitly (config file, env var)? Affects `check`, `drift`,
  `graph`, `query`, `dispatch`, and `skills install` identically.
- **Certificate trust scope.** `skill-certifier` provisions/rotates a
  signing key. Is that key per-project (Famulus and any other officina
  consumer each mint their own; certificates aren't portable across
  projects), or is there a shared/central trust root? Determines whether
  `officina drift` can ever mean anything across projects or is strictly
  local to one.

### Composition and compatibility

- **Cross-package composition.** If a host has officina's own skills,
  Famulus, and a third officina-based package all registered at once, do
  `graph`/`query`/`dispatch` operate on one combined namespace or
  per-package? Interface-ID collisions across independently-developed
  packages aren't addressed.
- **Version compatibility contract.** A skill built against officina's
  blueprint schema version N — what happens when the host has officina
  N+1 installed? Is there a declared minimum-officina-version per skill,
  enforced by `doctor` or `check`, or is this undefined until it breaks?

### Repo and release mechanics

- **Repo topology** (§2) — new git repo for officina, or stays in this
  repo as a second publishable package.
- **`script_dispatcher/` retirement** (§1) — unconfirmed whether anything
  outside officina imports it as a separate path from
  `officina.dispatcher.core`.
- **`.codex-plugin/plugin.json` hook wiring** (§1, second pass) — manual
  update to point at officina's relocated `hooks.json`, or does
  `officina skills install` produce this wiring automatically.
- **Cutover path for this repo specifically.** Existing Famulus installs
  already have `hooks.json`, certificates, and dispatcher wiring in place
  pre-split. Is there a migration step for existing users, or does the
  split assume a clean reinstall?
- **Distribution channel.** Public PyPI vs. a private index — interacts
  with the secret-store/OAuth code confirmed part of officina (§1).

### Scope calls still open

- **`install` vs `sync` overlap** (§3) — whether `install` should be
  idempotent and `sync` collapse into it.
- **Six ambiguous skills** (§1) — `git-workflow`,
  `install-assistant-tools`, `initialize-tdd`, `hook-maker`,
  `refactor-skills`, `update-standards`: officina or Famulus,
  case-by-case.
- **Three ambiguous docs** (§1) — `architectural-principles.md`,
  `skills.md`, `installation.md`: describe the officina architecture or
  the Famulus product, not determined from filename alone.
