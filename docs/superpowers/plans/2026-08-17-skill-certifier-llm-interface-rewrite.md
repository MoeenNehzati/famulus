# Skill Certifier LLM Interface Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the monolithic certifier instruction surface with three internal, responsibility-specific LLM audit interfaces and a thin drift-selected discovery gateway.

**Architecture:** `SKILL.md` owns orchestration only. It consumes drift's exact stale worklist and routes affected facets through three Markdown behavioral sources for interface, behavioral-source, and module semantic review; the existing Python runtime remains the sole mechanical validator and certificate issuer.

**Tech Stack:** Markdown instruction sources, Officina schema-v6 YAML blueprints, repository validators, pytest.

**Spec:** `docs/superpowers/specs/2026-08-17-skill-certifier-llm-interface-design.md`

## Global constraints

- Do not add or export `.interface.default`.
- Do not create file or remainder LLM interfaces.
- Keep signing and certificate writes in the existing runtime.
- Reuse a facet outside the exact stale worklist only when its claim is
  authenticated by the latest valid signed certificate and matches canonical
  state.
- Audit stale leaves before affected source and module ancestors; widen semantic
  context only on `needs-context`.
- Generate `SKILL.md` contract/interface blocks from blueprints.

### Task 1: Structural contract

**Files:**
- Create: `tests/test_skill_certifier_instruction_interfaces.py`
- Create: `skills/skill-certifier/instructions/audit-interface.md`
- Create: `skills/skill-certifier/instructions/audit-behavioral-source.md`
- Create: `skills/skill-certifier/instructions/audit-module.md`
- Create: `skills/skill-certifier/blueprints/instructions-audit-interface.yaml`
- Create: `skills/skill-certifier/blueprints/instructions-audit-behavioral-source.yaml`
- Create: `skills/skill-certifier/blueprints/instructions-audit-module.yaml`
- Modify: `skills/skill-certifier/blueprint.yaml`
- Modify: `skills/skill-certifier/blueprints/gateway.yaml`
- Modify: `skills/skill-certifier/SKILL.md`

**Produces:** Three private intrinsic instruction interfaces and a gateway that declares and sequences their use.

- [x] Add a focused test asserting the exact source/interface inventory, absence of a default export, narrow file/remainder treatment, and orchestration wording.
- [x] Run the focused test and confirm failure because the decomposition is absent.
- [x] Add the three instruction sources and their v6 blueprints.
- [x] Reduce `SKILL.md` to the dependency-first orchestration and mechanical handoff rules.
- [x] Synchronize generated skill blocks from the authored blueprints.
- [x] Run the focused test and current blueprint validators.

### Task 2: Regression verification and review

**Files:**
- Verify: `skills/skill-certifier/**`
- Verify: `tests/test_skill_certifier_instruction_interfaces.py`

**Produces:** Evidence that instruction decomposition does not change runtime signing behavior or invalidate repository structure.

- [x] Run skill-certifier runtime tests, relevant graph/schema tests, blueprint synchronization checks, and documentation validators.
- [x] Inspect the exact diff for unrelated or generated churn.
- [x] Ask an independent subagent to audit spec concordance, naming, exposure, dependency declarations, and side effects.
- [x] Correct material findings and rerun affected checks.

### Task 3: Drift-selected orchestration

**Files:**
- Modify: `skills/skill-certifier/SKILL.md`
- Modify: `skills/skill-certifier/blueprint.yaml`
- Modify: `skills/skill-certifier/blueprints/gateway.yaml`
- Modify: `skills/skill-drift/SKILL.md`
- Modify: `skills/skill-drift/blueprint.yaml`
- Modify: `skills/skill-drift/blueprints/gateway.yaml`
- Modify: `skills/skill-drift/_rtx/_check_drift_state.py`
- Modify: `skills/skill-drift/_rtx/blueprint.yaml`
- Modify: `skills/skill-drift/_rtx/blueprints/rtx-check-drift-state.yaml`
- Modify: `skills/skill-drift/_rtx/tests/test_drift_check.py`
- Modify: `skills/skill-certifier/_rtx/_node_certifier.py`
- Modify: `skills/skill-certifier/_rtx/blueprint.yaml`
- Modify: `skills/skill-certifier/_rtx/blueprints/rtx-certifier.yaml`
- Modify: `skills/skill-certifier/_rtx/tests/test_certifier.py`
- Modify: `src/officina/certification/view.py`
- Modify: `docs/officina/certification_and_drift.md`
- Modify: `tests/test_officina_certification_view.py`
- Modify: `tests/test_skill_certifier_instruction_interfaces.py`

**Produces:** Exact drift-cause selection, bottom-up semantic audits, and a
mechanical handoff that skips current nodes.

- [x] Declare the namespace-exported `skill-drift._rtx.interface.drift-status`
  use without introducing a certification dependency cycle.
- [x] Route stale interface facets through interface, source, and module audits
  dependency-first; route remainder causes directly to source review.
- [x] Permit wider semantic evidence only when an audit returns `needs-context`.
- [x] Emit exact structured drift causes and a dependency-first stale worklist;
  bump and propagate the changed drift-status contract version.
- [x] Recompute currentness before issuance, skip current nodes, and route-smoke
  only the stale issuance worklist.
- [x] Synchronize generated skill blocks and exact consumer version pins.
- [x] Run focused instruction, graph, synchronization, and documentation checks.
