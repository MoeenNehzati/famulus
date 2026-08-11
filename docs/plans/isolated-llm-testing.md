# Isolated LM Testing Plan

Status: proposed as of 2026-08-11.

## Goal

Build a reusable Linux test environment in which a fresh LM session can install,
configure, explore, and evaluate Famulus as an external user would, while
deterministic checks independently verify observable outcomes.

This is a general testing capability. It is not part of the public-release
workflow, although release work may choose to use its results.

## Testing model

The system has three distinct roles:

1. A mechanical harness creates and resets the isolated environment, identifies
   test artifacts, collects evidence, and performs deterministic checks.
2. An LM follows a textual scenario and interacts with Famulus through the
   supported assistant host. It evaluates discoverability, usability, recovery,
   and whether the documented workflows can be completed naturally.
3. A human supplies authentication or confirmation where external services or
   consequential actions require it. The human does not guide ordinary Famulus
   usage unless the scenario explicitly tests escalation.

The LM's report is qualitative evidence, not proof of success. Assertions about
files, messages, calendar events, installed state, and cleanup must come from
independent probes wherever practical.

Each run receives two versioned inputs: an immutable Famulus candidate and the
public documentation the LM is allowed to use. The candidate may be a release,
repository revision, or locally built distributable artifact, but its identity
and digest must be recorded and it must reach the VM without exposing the
maintainer checkout.

Each scenario defines its initial state, permitted human interventions,
observable outcomes, verification points, cleanup obligations, and the rule for
returning `pass`, `fail`, or `inconclusive`. A run passes only when every required
scenario outcome is independently verified and cleanup succeeds. A required
outcome or cleanup failure fails the run; an environment or verifier failure
that prevents a judgment makes the run inconclusive rather than successful.

## Isolation boundary

Use a disposable Linux virtual machine based on a reusable snapshot.

The baseline contains:

- a clean Linux installation;
- declared generic operating-system and assistant-host prerequisites;
- a supported Claude or Codex host installed and authenticated; and
- the mechanism needed to inject a scenario and extract a sanitized report.

The baseline must not contain:

- Famulus source or an installed Famulus release;
- a maintainer checkout or machine-specific development path;
- Famulus configuration, registry entries, caches, or generated state;
- Famulus-specific dependencies that the supported installation path is
  responsible for installing;
- connected email, cloud-file, or calendar accounts; or
- assistant-host plugins, skills, memories, instructions, or conversation state
  that could give the LM private Famulus knowledge; or
- test artifacts left by an earlier run.

Assistant-host authentication is harness state. It may exist in the baseline so
that the LM can run, but it must be kept separate from the Famulus state under
test. Each run starts from a copy-on-write clone or restored snapshot and the
clone is discarded after evidence and cleanup status are recorded.

## Workstream 1: Reproducible VM baseline

**Outcome:** A single command or short documented procedure creates a disposable
test VM from a known baseline.

- [ ] Choose and document one Linux virtualization path, preferring KVM/QEMU
  with a copy-on-write disk on hosts that support it.
- [ ] Define the baseline Linux distribution, version, system packages, resource
  allocation, network behavior, and supported assistant-host versions.
- [ ] Separate generic VM and assistant-host prerequisites from Famulus-owned
  dependencies. Install the latter through the documented Famulus path or test
  them as explicit public preconditions rather than hiding them in the image.
- [ ] Automate or document baseline creation sufficiently that it can be rebuilt
  without relying on the original maintainer's VM.
- [ ] Authenticate the assistant host, seal the Famulus-free baseline, and
  verify that a clone can start an interactive LM session.
- [ ] Add a preflight check that rejects a clone containing Famulus state,
  repository paths, connected integrations, or artifacts from a previous run.
- [ ] Provide bounded mechanisms for scenario injection and sanitized report
  extraction without mounting the maintainer checkout into the VM.
- [ ] Accept an immutable Famulus candidate and a versioned public-documentation
  bundle as run inputs, transfer them without exposing repository internals, and
  record their identities and digests in the run manifest.

## Workstream 2: Scenario protocol

**Outcome:** Text files give the LM realistic user missions without exposing
implementation details or coaching it around defects.

- [ ] Define a Markdown scenario format containing initial state, prerequisites,
  permitted actions and human interventions, safety limits, user missions,
  observable outcomes, verification points, optional exploration prompts,
  stopping conditions, cleanup duties, verdict rules, and a report schema.
- [ ] Require the LM to use only installed behavior and public user material. It
  must not inspect the source repository, test implementation, expected probe
  data, or maintainer notes.
- [ ] Require the LM to record each attempted mission as succeeded, failed, or
  uncertain; identify human interventions; and describe confusing or misleading
  behavior separately from mechanical failures.
- [ ] Treat unplanned human guidance as contamination of the usability result:
  record it and mark the affected scenario inconclusive unless that intervention
  is itself part of the scenario.
- [ ] Let scenarios ask the LM to vary meaningful user-facing options and try
  recovery paths, while avoiding an exhaustive combinatorial test matrix.
- [ ] Version scenarios so results state exactly which instructions were used.

## Workstream 3: Test fixtures and scenarios

**Outcome:** A small suite covers the workflows that matter to an external user.

### Integration fixture bootstrap and reset

- [ ] Provide a repeatable maintainer procedure for preparing dedicated test
  email and Google accounts, the OAuth client and required permissions, an
  isolated Drive root, a dedicated calendar, and seeded test messages.
- [ ] Define a fixture health check that confirms the accounts, credentials,
  permissions, seed data, and independent verifier access are ready before the
  LM begins.
- [ ] Define a reset procedure for both VM state and external services. It must
  remove abandoned run-identified artifacts, revoke or retain grants according
  to the scenario, restore seed data, and reject a dirty starting state.

### Minimum from-scratch scenario

- [ ] Install the exact immutable Famulus candidate through its supported user
  path without a source checkout, then confirm that a new LM session can
  discover and invoke it.
- [ ] Complete a useful credential-free workflow using only public guidance.
- [ ] Use dedicated test accounts rather than the maintainer's personal
  accounts, and assign every run a unique identifier used in subjects, event
  names, directories, files, and other created artifacts.
- [ ] Configure a new email account, verify authentication and reading, and send
  an explicitly authorized message to the test account itself.
- [ ] Configure cloud files with an isolated root; create, read, update, and
  delete bounded data; and confirm that an attempted out-of-root operation is
  rejected.
- [ ] Configure a dedicated test calendar; create, read, update, and delete an
  event without attendees or invitations.

### Expanded coverage

- [ ] Exercise a malformed request, a cancelled action, and one recoverable
  setup error; assess whether the LM explains the next step accurately.
- [ ] Complete a cross-skill workflow: turn a test email into triage state,
  preserve its source identity, and use the resulting state with calendar or
  list data in daily planning.
- [ ] Restart the assistant and verify that the installed Famulus release,
  registrations, and integrations retain their documented behavior.
- [ ] Exercise update or reinstall without losing user-owned state.
- [ ] Exercise retained-data uninstall and explicit purge as separate paths.
- [ ] Verify that cleanup removes all run-identified external artifacts and that
  the disposable VM can be discarded without losing required evidence.

## Workstream 4: Independent verification

**Outcome:** Test conclusions do not depend solely on the acting LM's account of
what happened.

- [ ] Define deterministic probes for installed version and provenance,
  installed payload, configuration ownership, persistence, and removal.
- [ ] Run verification from harness-controlled state that the acting LM cannot
  modify, and verify transient create/update behavior at scenario checkpoints
  before cleanup removes the resulting artifacts.
- [ ] Verify live integration outcomes externally: message receipt, bounded
  cloud-file state, calendar state, cross-skill source identifiers, and final
  cleanup.
- [ ] Keep probe instructions and expected values hidden from the acting LM
  where revealing them would turn the scenario into a scripted demonstration.
- [ ] Distinguish verified success, verified failure, LM-reported usability
  findings, and outcomes that could not be independently observed.
- [ ] Make failed cleanup a test failure even when the user-facing mission
  otherwise succeeded.

## Workstream 5: Security and evidence handling

**Outcome:** Interactive testing does not leak credentials, personal data, or
unnecessarily expand the LM's authority.

- [ ] Keep assistant-host, email, and Google credentials outside the repository
  and exclude secrets, OAuth client contents, tokens, and message bodies from
  reports and captured logs.
- [ ] Give test accounts the minimum data and permissions needed for the
  scenarios, and never use personal inboxes, calendars, or Drive roots.
- [ ] Require explicit human confirmation for sends and other consequential
  external actions; design ordinary test artifacts so cleanup is safe and
  deterministic.
- [ ] Sanitize reports before extraction and retain only the identifiers and
  evidence needed to reproduce or diagnose a result.
- [ ] Document credential rotation, VM disposal, and recovery when a run exits
  before cleanup.

## Workstream 6: Result format and usability

**Outcome:** Runs are comparable and failures lead to actionable work.

- [ ] Produce one report per run containing the VM-baseline version, Famulus
  candidate identity and digest, public-documentation version, assistant host
  and model, scenario and verifier versions, timestamps, and the unique run
  identifier.
- [ ] Record each mission's LM assessment beside independent probe results and
  any required human intervention.
- [ ] Separate product defects, documentation defects, environment failures,
  model-specific behavior, and inconclusive observations.
- [ ] Preserve sanitized logs sufficient to reproduce failures without turning
  the report store into a credential or personal-data store.
- [ ] Provide a concise summary suitable for comparing repeated runs across
  Famulus versions or supported hosts.
- [ ] Produce an overall `pass`, `fail`, or `inconclusive` verdict from the
  required scenario verdicts, while keeping qualitative usability findings
  visible beside the mechanical result.

## Implementation order

1. Establish and manually validate the Famulus-free Linux VM baseline.
2. Define immutable candidate and documentation inputs, then define the scenario
   acceptance contract and report format.
3. Prepare dedicated integration fixtures and validate their health and reset
   procedures.
4. Run installation, credential-free, email, cloud-file, and calendar scenarios
   manually through a fresh LM.
5. Add independent probes and produce the first overall Famulus verdict.
6. Add recovery, cross-skill, persistence, reinstall, uninstall, and purge
   coverage.
7. Automate clone creation, preflight, evidence collection, and disposal only
   after the manual workflow has stabilized.

## Completion criteria

### Minimum usable system

The core goal is reached when:

- [ ] a maintainer can create a fresh run from the documented baseline without
  manually cleaning prior Famulus state;
- [ ] the run consumes and records an immutable Famulus candidate and the exact
  public documentation available to the LM;
- [ ] a fresh LM can install that candidate and complete discovery and a useful
  credential-free workflow without repository access or private guidance;
- [ ] the LM can set up email, cloud files, and calendar from a validated clean
  fixture state and complete at least one bounded operation with each;
- [ ] independent, harness-controlled probes verify the required installation
  and integration outcomes at the defined checkpoints;
- [ ] the external fixtures can be reset to a validated clean state, including
  after an interrupted or failed run;
- [ ] secrets and personal data are absent from extracted evidence;
- [ ] the disposable VM can be discarded after each run while retaining a
  sanitized, reproducible result; and
- [ ] the result states an overall `pass`, `fail`, or `inconclusive` verdict and
  keeps the LM's usability findings separate from independently verified facts.

### Expanded coverage

After the minimum system works:

- [ ] malformed, cancelled, and recoverable-error scenarios are covered;
- [ ] at least one cross-skill workflow is independently verified;
- [ ] restart and persistence behavior are covered;
- [ ] update or reinstall without user-data loss is covered;
- [ ] retained-data uninstall and explicit purge are covered; and
- [ ] results can be compared across selected Famulus versions or supported
  assistant hosts.

## Non-goals

- Replacing deterministic unit, integration, or CI tests.
- Fully automating natural-language interaction or OAuth consent.
- Certifying every model, Linux distribution, external provider, or option
  combination.
- Treating the acting LM's self-reported success as authoritative.
- Rebuilding the operating system or reauthenticating the assistant host for
  every run.
