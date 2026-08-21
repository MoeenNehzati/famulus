# Skill Setup Lifecycle Design

## Goal

Let skills declare paired setup and teardown instructions. Local receipts make the normal check fast; a durable Rutter handles dependency order, interruption, and recovery only when lifecycle work is needed.

## Blueprint contract

Lifecycle roles annotate ordinary exported Markdown instruction interfaces:

```yaml
interfaces:
  list-manager.source.setup.interface.default:
    version: 1
    lifecycle: setup
    description: Set up this skill for use.

  list-manager.source.teardown.interface.default:
    version: 1
    lifecycle: teardown
    description: Tear down this skill's setup.
```

`lifecycle` accepts only `setup` or `teardown`. Declaring either requires exactly one exported interface of each role, owned by the same skill. The descriptions above are fixed; the ordinary interface version is the lifecycle version. Teardown behavior and resource provenance remain the skill author's responsibility.

## Graph semantics

Lifecycle projects the validated blueprint graph to top-level skills. Cross-skill interface uses and declared source dependencies create edges; internal edges collapse and self-edges disappear. Traversal passes through skills without lifecycle roles but emits actions only for lifecycle-bearing skills.

Generic deterministic directed-graph traversal belongs in `src/officina/common`; blueprint-to-skill projection remains in `src/officina/lifecycle`. The common helper provides dependency-first order, reverse traversal, and explicit cycle diagnostics without knowing about blueprints or lifecycle.

Setup of `X` runs every unset lifecycle-bearing node in `X`'s dependency closure, dependencies first, then `X` when applicable.

Teardown has deliberately different semantics: teardown of `X` first tears down every currently set-up lifecycle-bearing skill that transitively depends on `X`, then `X`. It does not tear down `X`'s dependencies. Thus tearing down a root leaves shared dependencies available; explicitly tearing down a dependency cascades through all set-up consumers before removing it. A cascade affecting another skill requires confirmation.

Cycles fail closed. Ordering is deterministic.

## Setup state

Each lifecycle-bearing skill stores only its own receipt:

```text
<famulus-state-root>/<skill-id>/setup.json
```

The strict payload is `{"schema_version":1,"set_up":true|false}`. Absence means not set up. Malformed, unsafe, or unreadable state is an error. Writes are confined, atomic, and user-only.

`has-been-set-up` is a fast local interface. For a root skill it returns true exactly when every lifecycle-bearing node in the root's dependency closure has a valid true receipt. It performs no remote health checks and caches no aggregate state.

Receipts describe current environment state. Reckonings describe progress through one voyage. Skill-owned provenance describes resources safe to remove. These remain separate.

## Lifecycle coordination

Lifecycle actions can be externally non-idempotent, so receipt locking alone is insufficient. A single durable lifecycle coordinator serializes setup and teardown voyages across the installation:

- no active voyage: derive work, create a Reckoning, and publish its locator;
- caller holding the active claim ID: reopen and resume it;
- any request without that claim ID while active: report busy rather than start competing work;
- complete voyage: clear the active locator, retaining the immutable Reckoning as history;
- faulted or uncertain voyage: retain the locator for explicit recovery.

The coordinator creates a random claim ID with the voyage and requires it on every later step. It is a concurrency claim, not a security credential. Before returning an instruction, the gateway atomically records its voyage, revision, and instruction nonce as awaiting evidence. It never reissues unresolved instruction text; retries return `awaiting-evidence`. Invalid evidence retains the same nonce and returns validation issues. Lost-claim recovery must reconcile an outstanding nonce or mark it uncertain before rotating the claim; there is no timeout-based takeover.

The coordinator lock covers claim validation, each complete Python Rutter step, and locator reconciliation, but not the time in which the LLM performs returned Markdown. On entry, the coordinator compares an active locator with its Reckoning: terminal locators are cleared, faulted or uncertain locators remain, and missing or malformed targets fail closed. A handle-bearing retry can still reopen a historical terminal Reckoning and replay its terminal response.

## Rutter and Compass boundary

One `SkillLifecycleRutter`, parameterized by Charter data rather than constructor state, handles both operations. It has the ordinary Rutter identity fields and derives its states from an immutable Charter containing:

- root skill, operation, and blueprint fingerprint;
- ordered action records;
- exact instruction text, interface ID, version, and content digest for each action; and
- the receipt observations used to create the voyage.

Setup action states expose one setup instruction at a time, followed by a repeat-safe receipt effect. Teardown begins with cascade confirmation when needed, then exposes one teardown instruction at a time. Failures leave the current receipt unchanged. Rutter effect recovery reconciles a completed receipt write without rerunning its instruction.

The live dispatcher is a process boundary, so Markdown cannot carry a Python `BaseRutter` or `UnboundRutter`. A lifecycle-owned Compass session gateway therefore owns registry binding and reopens the Rutter for each call. Its JSON binding contains an opaque confined voyage ID and claim ID. `start`, handle-bearing `step`, and confirmed `recover` are distinct operations. A step accepts optional evidence tied to the displayed revision and instruction nonce, and returns `instruction`, `awaiting-evidence`, `invalid`, `complete`, `faulted`, `uncertain`, or `busy`. Python performs settling, validation, advancement, and callable effects; the LLM only performs newly issued string instructions and supplies finite evidence.

Before deriving states or receipt effects, the lifecycle Rutter validates every persisted Charter action against the current blueprint graph, interface content digest, target order, and confined receipt path. Stale or malformed Charter authority fails closed.

`using-compass` is updated to operate this serializable session binding. It does not receive registry objects, paths, or Charter construction authority.

The root named by generated instructions is validated against dispatcher caller policy. This prevents accidental cross-skill mutation but is not described as a security boundary: the current CLI caller identity is user-supplied. Destructive teardown still requires explicit user intent and cascade confirmation.

## Flows and generated header

Canonical flow sources have a small registered `src/officina/lifecycle_flow` owner that may depend on both lifecycle and `using-compass` without creating a dependency cycle. Generation projects them to repository-level `instructions/setup-flow.md` and `instructions/teardown-flow.md`, preserving the requested installed paths.

The generated gate is intentionally short:

> Lifecycle: for teardown, follow `../../instructions/teardown-flow.md` for `<skill-id>`. Otherwise call `lifecycle.interface.has-been-set-up`; if false or setup was explicitly requested, follow `../../instructions/setup-flow.md` for `<skill-id>`. Continue only after the applicable flow completes.

The header appears when the skill or its dependency closure contains a lifecycle-bearing skill. Each flow binds the appropriate serialized lifecycle session as the invoking root, hands that bound handle to Compass, and stops unless it completes.

## Isolated acceptance fixture

Development uses a fresh nested Codex process with temporary `HOME`, `CODEX_HOME`, platform config/state roots, and a working directory outside repository ancestry. Its local inventory contains only four fixture skills, apart from bundled system skills:

```text
fixture-setup-root -> fixture-plain-middle -> fixture-setup-leaf
fixture-plain-standalone
```

The root and leaf have trivial paired instructions that print their skill-specific setup or teardown message. The middle has no lifecycle role; the standalone has neither a role nor lifecycle dependency.

The harness copies, rather than symlinks, the required fixture repository. A temporary dispatcher launcher always supplies that copy's exact `officina.toml`; prompt inspection verifies every production skill is absent, and a dispatcher dry run verifies fixture routing. Deterministic tests use the fixtures throughout development. One final `codex exec --json` smoke verifies actual dispatcher calls, Rutter transitions, receipts, and messages; printed messages alone are not proof.

## Canary and adoption

`list-manager` is the first production binding. Setup checks `todo` and `triage`, creates only lists proven absent, records which it created, and verifies both. Teardown deletes only recorded resources and clears provenance after verification.

After the framework and canary pass, audit the repository for other durable prerequisites. Names such as `init`, `connect`, or `install` are only search clues. Migrate verified dependencies before consumers, in small batches, and require a safe paired teardown for every setup role.

## Non-goals

- Remote health checks on every invocation.
- Aggregate setup-state caching.
- A global multi-skill receipt ledger.
- Automatic receipt invalidation when instructions change.
- Framework-inferred teardown or resource provenance.
- Concurrent lifecycle voyages.
- Refactoring unrelated existing graph traversals.
