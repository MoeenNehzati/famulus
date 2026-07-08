Implement Famulus per-skill health records, a mechanical `skill-health` wrapper, and a semantic `skill-doctor` certifier.

## Goal

Add a local health-record system for Famulus skills.

Each skill gets a local hidden health record:

```text
skills/<skill-name>/.health.json
```

This record tracks whether the current version of the skill has been certified as healthy. The record becomes stale if the skill’s meaningful files change, if the skill-system policy changes, or if the health record itself is tampered with.

There are two new skills:

```text
skill-health
skill-doctor
```

Their responsibilities are distinct:

```text
skill-health = mechanical health-record display and state operations
skill-doctor = semantic review, repair, and certification authority
```

The rule is:

```text
health.py computes.
skill-health displays.
skill-doctor judges and certifies.
```

`skill-health` must be a very thin wrapper around a Python script. It should not compute health, interpret statuses, aggregate summaries, or decide what is wrong. The Python script generates the full report.

`skill-doctor` runs validators, tests, guideline review, blueprint semantic review, dependency review, and then recertifies the target skill only if all checks pass.

## Important design choice

`.health.json` must be local state, not git-tracked.

Add this to `.gitignore`:

```gitignore
skills/*/.health.json
```

Reason: the health record checks its own filesystem `mtime`. Git does not preserve committed mtimes across clone/checkout, so committed health files would become stale everywhere.

The migration should create local initial `.health.json` files for all existing skills, but these files must not be committed.

---

# Files to add or change

Create:

```text
skills/skill-health/
  SKILL.md
  blueprint.yaml
  scripts/health.py
  tests/test_health.py

skills/skill-doctor/
  SKILL.md
  blueprint.yaml

llmhooks/mark_skill_health_stale.py
```

Modify:

```text
.gitignore
llmhooks/registry.py
hooks/hooks.json or any checked-in host hook config if needed
README/docs only if necessary
```

Also update generated blueprint artifacts after adding/changing blueprints:

```bash
python3 skills/skill-maker/scripts/sync_skill_blueprints.py
```

---

# Part 1: `skill-health`

## Purpose

`skill-health` is mechanical bookkeeping only.

It provides access to the health record system:

```text
status        read derived health status
invalidate    mark a skill’s current certification invalid/unhealthy
certify       write a well certification, only when called by skill-doctor
migrate       initialize local health records
```

It does not judge whether a skill deserves certification.

## `skill-health/SKILL.md`

Keep the body tiny.

Suggested content:

```markdown
---
name: skill-health
description: Use when reading, invalidating, migrating, or recording the local health state of Famulus skills.
---

<!-- generated blueprint blocks -->

Use the exported script interfaces.

For health reports, call the status interface. If the user does not name a skill, call status without a skill name so the script reports all observed skills.

Display the script's report directly. Do not recompute, reinterpret, summarize, aggregate, or override health status in the skill body.

This skill is mechanical bookkeeping only; it does not judge whether a skill deserves certification.
```

The skill should not contain health logic.

## `skills/skill-health/scripts/health.py`

Implement in pure Python stdlib.

The script owns all computation and report generation.

Supported commands:

```bash
python3 skills/skill-health/scripts/health.py status [skill-name] [--json]
python3 skills/skill-health/scripts/health.py invalidate <skill-name> --reason "..."
python3 skills/skill-health/scripts/health.py certify <skill-name> --checker "skill-doctor@1"
python3 skills/skill-health/scripts/health.py migrate-unhealthy [--all] --reason "..."
```

`status` without a skill name means report all observed skills.

Observed skills are:

```text
all directories matching skills/*/ that contain SKILL.md
```

So these are equivalent:

```bash
python3 skills/skill-health/scripts/health.py status
python3 skills/skill-health/scripts/health.py status --all
```

`--all` may be supported as an alias, but it should not be required.

If a named skill does not exist or lacks `SKILL.md`, return nonzero with a clear error.

## Health record schema

Write `.health.json` like:

```json
{
  "schema_version": 1,
  "skill": "proof-audit",
  "status": "unhealthy",
  "reason": "initial migration: wellness has not been run yet",
  "checked_at": "2026-07-07T21:00:00-04:00",
  "health_mtime_ns": 1783472400000000000,
  "skill_content_hash": "sha256:...",
  "policy_hash": "sha256:...",
  "checker": "skill-health@1",
  "passed": []
}
```

Allowed stored statuses:

```text
well
unhealthy
```

Derived statuses reported by `status`:

```text
well
unhealthy
missing-record
corrupt-record
skill-mismatch
record-mtime-mismatch
stale-content
stale-policy
uncertified
```

Use this precedence order:

```text
missing-record
corrupt-record
skill-mismatch
record-mtime-mismatch
stale-policy
stale-content
unhealthy
well
```

Meanings:

```text
missing-record
  .health.json does not exist.

corrupt-record
  .health.json is invalid JSON or lacks required fields.

skill-mismatch
  record.skill does not match the directory name.

record-mtime-mismatch
  filesystem mtime does not equal health_mtime_ns inside the record.

stale-policy
  current policy_hash differs from recorded policy_hash.

stale-content
  current skill_content_hash differs from recorded skill_content_hash.

unhealthy
  stored status is unhealthy.

well
  stored status is well and all hashes/metadata match.
```

## Health record self-mtime check

When writing `.health.json`, set the recorded mtime and actual filesystem mtime together.

Use second-aligned nanoseconds for portability:

```python
target_mtime_ns = int(time.time()) * 1_000_000_000
```

Then:

```python
payload["health_mtime_ns"] = target_mtime_ns
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
os.utime(path, ns=(target_mtime_ns, target_mtime_ns))
```

When reading:

```python
actual_mtime_ns = path.stat().st_mtime_ns
recorded_mtime_ns = health["health_mtime_ns"]

if actual_mtime_ns != recorded_mtime_ns:
    derived_status = "record-mtime-mismatch"
```

`status` must be read-only. It must never rewrite `.health.json`.

## Skill content hash

`skill_content_hash` is a deterministic hash over meaningful files under the target skill.

Include these if present:

```text
SKILL.md
blueprint.yaml
depends_on_skills
permissions.json
scripts/**
tests/**
agents/**
references/**
validators/**
```

Exclude:

```text
.health.json
__pycache__/**
.pytest_cache/**
.DS_Store
*.pyc
```

Hash both relative paths and file bytes in sorted order.

Format:

```text
sha256:<hex>
```

## Policy hash

`policy_hash` is a deterministic hash over files that define the health/certification standard.

Include these if present:

```text
references/skill-guidelines.md
references/blueprint/schema.json
references/blueprint/template.yaml
references/blueprint/guide.md
skills/skill-maker/validators/**/*.py
skills/skill-maker/scripts/sync_skill_blueprints.py
validators/**/*.py
.githooks/pre-commit
.githooks/skill/**
```

If any of these change, prior certifications become `stale-policy`.

## Human-readable reports

The Python script must generate the full human-readable report.

For all skills:

```text
Skill Health Report
===================

Summary
-------
Observed skills: 24
Well: 3
Unhealthy: 15
Stale content: 4
Stale policy: 1
Missing record: 1

Skills
------
well
  - proof-audit
  - bib-audit

unhealthy
  - skill-maker
    reason: initial migration: wellness has not been run yet

stale-content
  - daily-plan
    reason: skill_content_hash does not match current files

stale-policy
  - email-triage
    reason: policy_hash does not match current skill-system policy

missing-record
  - loose-mode
    reason: skills/loose-mode/.health.json does not exist
```

For one skill:

```text
Skill Health: proof-audit
=========================

Derived status: stale-content
Stored status: well
Record: skills/proof-audit/.health.json

Reason:
  skill_content_hash does not match current skill files.

Stored certification:
  checker: skill-doctor@1
  checked_at: 2026-07-07T21:00:00-04:00

Hashes:
  recorded skill_content_hash: sha256:...
  current  skill_content_hash: sha256:...
  recorded policy_hash:        sha256:...
  current  policy_hash:        sha256:...

Next action:
  Run skill-doctor on proof-audit before considering it healthy.
```

The exact formatting can differ, but the script must produce a complete user-readable report. `skill-health` should simply display this report.

## JSON output

For `--json`, emit structured JSON.

For all skills:

```json
{
  "schema_version": 1,
  "observed_skill_count": 24,
  "summary": {
    "well": 3,
    "unhealthy": 15,
    "stale-content": 4,
    "stale-policy": 1,
    "missing-record": 1
  },
  "skills": [
    {
      "skill": "proof-audit",
      "derived_status": "stale-content",
      "stored_status": "well",
      "record_path": "skills/proof-audit/.health.json",
      "reason": "skill_content_hash does not match current files",
      "checked_at": "2026-07-07T21:00:00-04:00",
      "checker": "skill-doctor@1",
      "recorded_skill_content_hash": "sha256:...",
      "current_skill_content_hash": "sha256:...",
      "recorded_policy_hash": "sha256:...",
      "current_policy_hash": "sha256:..."
    }
  ]
}
```

For one skill, prefer the same shape with a single item in `skills`.

## `invalidate`

Command:

```bash
python3 skills/skill-health/scripts/health.py invalidate <skill-name> --reason "..."
```

Writes `.health.json` with:

```json
{
  "status": "unhealthy",
  "reason": "<provided reason>",
  "checker": "skill-health@1",
  "passed": []
}
```

Also records current `skill_content_hash` and current `policy_hash`.

This means the record is internally valid but not certified.

## `certify`

Command:

```bash
python3 skills/skill-health/scripts/health.py certify <skill-name> --checker "skill-doctor@1"
```

Guard:

```text
Reject unless --checker starts with "skill-doctor@".
```

This is a convention guard, not a hard security boundary.

On success, write:

```json
{
  "status": "well",
  "reason": null,
  "checker": "skill-doctor@1",
  "passed": [
    "validators",
    "blueprint_sync",
    "tests",
    "guideline_review",
    "blueprint_semantics",
    "dependency_review",
    "state_contract_review"
  ]
}
```

Also record current hashes and mtime.

`certify` should not itself run semantic review. That is `skill-doctor`’s job.

## `migrate-unhealthy`

Command:

```bash
python3 skills/skill-health/scripts/health.py migrate-unhealthy --all \
  --reason "initial migration: wellness has not been run yet"
```

Iterate over all observed skills and create or replace `.health.json` with stored status `unhealthy`.

Do not stage these files. They are gitignored local state.

## `skill-health/blueprint.yaml`

Expose separate interfaces. Do not expose one broad “do anything” interface.

Suggested shape, adjusted to the repo’s current blueprint schema:

```yaml
category: skill-making-development-assistant
interface_version: 1

depends_on: {}

suggested_permissions:
  bash:
    - command: ["python3", "skills/skill-health/scripts/health.py"]
      reason: "Mechanical health-record operations for Famulus skills."
  network: []

skill_interface:
  inputs:
    - User request to read, invalidate, migrate, or record skill health state.
    - Optional target skill name.
  outputs:
    - Human-readable or JSON health report generated by the health script.
  side_effects:
    - May create or update local skills/<skill>/.health.json records for invalidate, certify, and migrate operations.

script_interfaces:
  health-status:
    id: health-status
    description: "Read derived health status for one skill or all observed skills."
    usage: "status [skill-name] [--json]"
    cwd: repo_root
    command: ["python3", "skills/skill-health/scripts/health.py"]
    default:
      patterns:
        - min_positionals: 1
          max_positionals: 2
          allow_stdin: false
          positional_patterns:
            0: "^status$"
          notes: "Run status with no skill name to report all observed skills. Observed skills are skills/* directories containing SKILL.md. Add --json for machine-readable output."
      allow_all_skills: true
      allowed_callers: []

  health-invalidate:
    id: health-invalidate
    description: "Invalidate a skill's current health certification."
    usage: "invalidate <skill-name> --reason <reason>"
    cwd: repo_root
    command: ["python3", "skills/skill-health/scripts/health.py"]
    default:
      patterns:
        - min_positionals: 2
          allow_stdin: false
          positional_patterns:
            0: "^invalidate$"
            1: "^[a-z0-9]+(?:-[a-z0-9]+)+$"
          required_flags: ["--reason"]
          notes: "Use after a meaningful skill file changes or a check finds stale/invalid health. The second positional is the target skill name."
      allow_all_skills: false
      allowed_callers:
        - skill-doctor
        - skill-maker

  health-certify:
    id: health-certify
    description: "Write a well health record after skill-doctor certifies a skill."
    usage: "certify <skill-name> --checker skill-doctor@1"
    cwd: repo_root
    command: ["python3", "skills/skill-health/scripts/health.py"]
    default:
      patterns:
        - min_positionals: 2
          allow_stdin: false
          positional_patterns:
            0: "^certify$"
            1: "^[a-z0-9]+(?:-[a-z0-9]+)+$"
          required_flags: ["--checker"]
          flag_patterns:
            --checker: "^skill-doctor@[0-9]+$"
          notes: "Only skill-doctor should call this after completing semantic review, validators, blueprint sync, and tests."
      allow_all_skills: false
      allowed_callers:
        - skill-doctor

  health-migrate:
    id: health-migrate
    description: "Initialize local unhealthy health records for observed skills."
    usage: "migrate-unhealthy [--all] --reason <reason>"
    cwd: repo_root
    command: ["python3", "skills/skill-health/scripts/health.py"]
    default:
      patterns:
        - min_positionals: 1
          allow_stdin: false
          positional_patterns:
            0: "^migrate-unhealthy$"
          required_flags: ["--reason"]
          allowed_flags: ["--all", "--reason"]
          notes: "Initialize local .health.json records with status unhealthy. The records are gitignored and should not be committed."
      allow_all_skills: false
      allowed_callers:
        - skill-maker
        - skill-doctor
```

If the current schema/validators require slightly different pattern fields, follow the repo’s existing blueprint template and validators.

---

# Part 2: write/edit invalidation hook

Add:

```text
llmhooks/mark_skill_health_stale.py
```

Purpose:

```text
After an assistant writes or edits a meaningful file under skills/<skill-name>/,
invalidate that skill's health record.
```

Use the existing cross-host hook scaffold. Keep host-specific parsing in adapter logic.

Behavior:

1. Read hook payload from stdin.
2. Extract tool name and written/edited path(s), defensively handling host differences.
3. If no path is found, exit successfully.
4. If a path is under `skills/<skill-name>/` and the skill directory contains `SKILL.md`, run:

```bash
python3 skills/skill-health/scripts/health.py invalidate <skill-name> \
  --reason "modified by assistant write/edit hook"
```

5. Ignore:

```text
skills/<skill-name>/.health.json
__pycache__/**
.pytest_cache/**
*.pyc
.DS_Store
```

6. Ignore files outside `skills/*/`.

Register this hook in:

```text
llmhooks/registry.py
```

Use host-specific post-tool-use bindings where available.

For Claude Code, bind to write/edit tools such as:

```text
Write
Edit
MultiEdit
```

For Codex, use the equivalent write/edit lifecycle only if the repo’s hook scaffold already supports it clearly. Do not guess unsupported Codex lifecycle behavior; document partial support if necessary.

Also update checked-in plugin hook config if this repo has separate static hook config.

---

# Part 3: `skill-doctor`

## Purpose

`skill-doctor` is the semantic review and certification authority.

It can:

```text
run validators
run blueprint sync checks
run tests
compare the skill against skill guidelines
verify blueprint semantic correctness
verify dependencies and interface versions
repair inconsistencies when appropriate
certify the skill as well by calling skill-health
```

Create:

```text
skills/skill-doctor/SKILL.md
skills/skill-doctor/blueprint.yaml
```

## Description

Use a trigger-only description:

```yaml
description: Use when checking, repairing, or certifying a Famulus skill after creation, edits, stale health state, failed validators, or suspected divergence from skill guidelines.
```

## Dependencies

`skill-doctor` should depend on:

```text
skill-health
skill-maker
```

It must have access to:

```text
skill-health.health-status
skill-health.health-invalidate
skill-health.health-certify
```

and any relevant skill-maker interface if one exists. If no useful exported skill-maker interface exists, the workflow may call repo scripts directly if the blueprint permits it and permissions are declared.

## `skill-doctor/SKILL.md` workflow

When invoked on a target skill:

### 1. Identify target

Determine the target skill name. If none is explicitly named and context clearly contains exactly one recently edited skill, use that. Otherwise ask for the target.

### 2. Read current health status

Use JSON, not the human report, for decision-making:

```bash
python3 skills/skill-health/scripts/health.py status <target> --json
```

### 3. Read target files

Inspect:

```text
skills/<target>/SKILL.md
skills/<target>/blueprint.yaml
skills/<target>/permissions.json
skills/<target>/depends_on_skills
skills/<target>/scripts/**
skills/<target>/tests/**
skills/<target>/references/**
skills/<target>/agents/**
skills/<target>/validators/**
```

Only inspect paths that exist.

Also read the shared standards:

```text
references/skill-guidelines.md
references/blueprint/schema.json
references/blueprint/template.yaml
references/blueprint/guide.md
```

### 4. Run mechanical checks

Run:

```bash
python3 skills/skill-maker/scripts/sync_skill_blueprints.py --check
python3 validators/runner.py
```

If either fails, fix the cause where appropriate, run blueprint sync if needed, and rerun.

### 5. Run tests

At minimum, if present:

```bash
pytest skills/<target>/tests
```

Also run targeted tests related to touched shared machinery:

```text
tests/validate_<name>.py
skills/skill-maker/tests/test_blueprint_tools.py
hooks/tests/**
skills/skill-health/tests/**
```

If practical before certification, run:

```bash
pytest
```

Do not certify if relevant tests fail.

### 6. Semantic guideline review

Check for divergence from `references/skill-guidelines.md` not already covered by validators.

Required review points:

```text
- Skill name and directory name follow repo naming conventions.
- SKILL.md frontmatter name matches directory.
- Description is trigger-only, not a summary of workflow.
- SKILL.md body is terse and output-focused.
- SKILL.md does not contain implementation logic that belongs in scripts.
- Blueprint owns script interface definitions.
- SKILL.md body does not restate generated dispatcher invocations.
- Generated interface block is sufficient for first-attempt invocation.
- Blueprint skill_interface accurately describes actual inputs, outputs, and side effects.
- suggested_permissions match actual script/tool needs and include safety reasons.
- permissions.json is generated and consistent with blueprint.yaml.
- depends_on only lists real dependencies.
- Every invoked skill is declared as a dependency.
- Dependency major versions match dependency interface versions.
- exports list only interfaces actually used.
- Restricted exported interfaces are allowed for this caller.
- No cross-skill script paths or imports are used directly.
- Persistent state lives under the owning skill directory.
- Credentials/secrets are documented under ~/.config/<skill-name>/ and not stored in repo.
- Tests exist for nontrivial scripts, validators, hook behavior, and migrations.
- Script interfaces have clear usage, patterns, and notes.
- Blueprint category is appropriate for the skill's actual role.
- The skill has a single coherent responsibility.
- The skill does not duplicate an existing skill unless it is intentionally extending it.
```

### 7. Blueprint semantic review

Check that `blueprint.yaml` is not merely schema-valid but actually correct.

Required review points:

```text
- category matches the skill's purpose.
- interface_version is unchanged unless the public contract changed.
- depends_on reflects actual skill-level or dispatcher-level use.
- exports list matches interfaces the skill actually calls.
- skill_interface inputs/outputs/side_effects match the body and scripts.
- script_interfaces describe actual script commands and argument contracts.
- usage fields are complete and not placeholders.
- pattern notes are enough for a dependent skill to call correctly.
- allow_all_skills / allowed_callers choices are appropriate.
- no internal-only interface is exported to dependents.
- suggested_permissions are not broader than the actual script surface.
```

### 8. Dependency/version review

Check:

```text
- If target depends on a blueprint skill, major_version is declared.
- major_version equals dependency interface_version.
- exports names exist on the dependency.
- restricted exports list target in allowed_callers.
- target does not depend on itself.
- target does not import or call another skill’s scripts directly.
```

Use existing validators where possible, but still semantically review whether the dependency declarations are meaningful and minimal.

### 9. Repair loop

If issues are found:

```text
- make the smallest coherent fix;
- rerun blueprint sync if blueprint changed;
- rerun validators;
- rerun relevant tests;
- reread changed files;
- repeat semantic review.
```

Stop and report instead of certifying if remaining issues require user judgment.

### 10. Certification

Only certify if all are true:

```text
- blueprint sync passes;
- validators pass;
- relevant tests pass;
- semantic guideline review passes;
- blueprint semantic review passes;
- dependency/version review passes;
- no unresolved TODOs/placeholders remain in the skill contract;
- no user judgment is required;
- the current skill files are the final reviewed files.
```

Then run:

```bash
python3 skills/skill-health/scripts/health.py certify <target> \
  --checker "skill-doctor@1"
```

After certification, run:

```bash
python3 skills/skill-health/scripts/health.py status <target>
```

Display the resulting report.

### 11. Final response format

Report:

```text
- initial health status;
- files inspected;
- checks run;
- tests run;
- issues found;
- repairs made;
- final health status;
- whether certification was written.
```

---

# Part 4: migration

After implementation, run locally:

```bash
python3 skills/skill-health/scripts/health.py migrate-unhealthy --all \
  --reason "initial migration: wellness has not been run yet"
```

Then verify:

```bash
python3 skills/skill-health/scripts/health.py status
python3 skills/skill-health/scripts/health.py status --json
```

Expected:

```text
Every observed skill has a local .health.json.
Every existing skill starts with stored status "unhealthy".
No .health.json files are tracked by git.
```

---

# Part 5: tests

Add tests in:

```text
skills/skill-health/tests/test_health.py
```

Test cases:

```text
1. status with no skill reports all observed skills.
2. status with no skill and --json returns all observed skills.
3. status <skill> reports only that skill.
4. status ignores directories under skills/ that lack SKILL.md.
5. status reports missing-record when .health.json is absent.
6. status reports corrupt-record when JSON is invalid.
7. status reports skill-mismatch when recorded skill differs from directory.
8. status reports record-mtime-mismatch after manual touch/edit of .health.json.
9. status reports stale-content after SKILL.md changes.
10. status reports stale-policy after policy files change.
11. .health.json is excluded from skill_content_hash.
12. invalidate writes status unhealthy and records reason.
13. migrate-unhealthy creates .health.json for every observed skill.
14. migrated records store status unhealthy.
15. certify refuses checker values not starting with skill-doctor@.
16. certify writes status well when checker is skill-doctor@1.
17. human all-skill report includes summary counts and grouped skills.
18. JSON all-skill report includes summary and skills array.
```

Add hook tests if the repo already has hook-test patterns:

```text
1. write/edit under skills/<skill>/ invalidates that skill.
2. write/edit to .health.json does not recurse.
3. write/edit outside skills/ is ignored.
4. directories under skills/ without SKILL.md are ignored.
```

Add or update tests ensuring:

```text
- skill-health SKILL.md contains no health computation logic.
- skill-doctor uses health.py status <target> --json for decision-making.
- skill-doctor is the only declared caller of health-certify.
```

---

# Part 6: acceptance criteria

The implementation is complete when:

```text
- .gitignore ignores skills/*/.health.json.
- skill-health exists and is a thin wrapper around scripts/health.py.
- health.py supports status, invalidate, certify, and migrate-unhealthy.
- health.py status with no skill reports all observed skills.
- health.py generates the full human-readable health report.
- health.py --json produces structured machine-readable output.
- .health.json records and checks its own mtime using health_mtime_ns.
- skill_content_hash detects meaningful skill file changes.
- policy_hash detects skill-system policy changes.
- existing skills can be locally migrated to initial unhealthy state.
- skill-doctor exists and owns semantic review, repair, and certification.
- skill-doctor only certifies after validators, blueprint sync, tests, guideline review, blueprint semantic review, and dependency review pass.
- health.py certify refuses non-skill-doctor checker values.
- write/edit hooks invalidate changed skills where host support exists.
- tests cover health-record mechanics and reporting.
- blueprint sync, validators, and relevant pytest tests pass.
```

## Final commands to run

After code changes:

```bash
python3 skills/skill-maker/scripts/sync_skill_blueprints.py
python3 validators/runner.py
pytest skills/skill-health/tests
pytest
python3 skills/skill-health/scripts/health.py migrate-unhealthy --all \
  --reason "initial migration: wellness has not been run yet"
python3 skills/skill-health/scripts/health.py status
git status --short
```

Confirm that `.health.json` files are not staged/tracked.
