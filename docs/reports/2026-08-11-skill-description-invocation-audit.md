# Skill Description Invocation Audit

Date: 2026-08-11

Implementation note: all 37 recommended triggers were applied to the live
skill frontmatter on 2026-08-11. “Current description” and the description
lengths below record the pre-remediation values audited that day.

## Scope and criterion

This report audits the live `description:` frontmatter of all 37 repository
skills under `skills/*/SKILL.md`. It evaluates only discovery and invocation:
whether the description causes the skill to load for the right requests and
stay unloaded for neighboring requests. The skill body and declared public
interfaces are treated as the evidence for the skill's actual invocation
boundary. This is not an audit of implementation correctness.

The current instruction-module standard requires a description to:

- begin with a standalone, sentence-terminated `Use when...` trigger summary;
- add only trigger detail when needed; and
- omit workflow steps and outputs.

The recommendations below apply a stricter drafting test: state the user intent
or situation that warrants loading the skill, plus only the exclusions needed
to distinguish neighboring skills. Do not inventory features, inputs, data
sources, internal composition, workflow, or outputs.

Scores:

- **5 — precise:** clear positive trigger and sufficient boundary against nearby skills.
- **4 — good:** materially correct, with a small omission or overlap risk.
- **3 — mixed:** identifies the domain but misses an important trigger or exclusion.
- **2 — weak:** regularly risks false-positive loading or conflates adjacent workflows.
- **1 — poor:** materially misstates the invocation boundary or violates the trigger-header contract.

## Summary

Length is the number of Unicode characters in the YAML-parsed current
`description` value, using the same `len(description)` convention as the
repository metadata validator. It excludes YAML syntax and indentation but
includes whitespace preserved by YAML block scalars.

| Skill | Quality score | Description length (characters) |
|---|---:|---:|
| `bib-audit` | 4/5 | 230 |
| `cloud-files` | 1/5 | 238 |
| `connect-google` | 4/5 | 131 |
| `daily-plan` | 2/5 | 223 |
| `email-client` | 3/5 | 123 |
| `email-triage` | 4/5 | 94 |
| `find-handoff-candidates` | 5/5 | 222 |
| `fix-bisync` | 5/5 | 208 |
| `formal-prose-review` | 5/5 | 258 |
| `g-calendar` | 3/5 | 113 |
| `get-weather` | 5/5 | 122 |
| `git-workflow` | 2/5 | 186 |
| `hook-maker` | 5/5 | 231 |
| `initialize-tdd` | 5/5 | 124 |
| `install-assistant-tools` | 4/5 | 197 |
| `latex-workshop` | 4/5 | 182 |
| `list-manager` | 4/5 | 409 |
| `llm-wakeup` | 5/5 | 158 |
| `loose-mode` | 2/5 | 187 |
| `make-tex-docstring` | 1/5 | 933 |
| `math-dependency-graph` | 4/5 | 337 |
| `notation-review` | 5/5 | 607 |
| `pdf-to-markdown` | 4/5 | 96 |
| `prepare-handoff` | 5/5 | 400 |
| `proof-audit` | 5/5 | 620 |
| `recurring-tasks` | 1/5 | 212 |
| `refactor-node` | 1/5 | 135 |
| `regenerate-blueprints` | 3/5 | 74 |
| `semantic-integration` | 4/5 | 289 |
| `skill-certifier` | 4/5 | 124 |
| `skill-drift` | 2/5 | 93 |
| `skill-maker` | 2/5 | 76 |
| `technical-flow-review` | 5/5 | 604 |
| `tight-mode` | 4/5 | 163 |
| `tool-applicability` | 4/5 | 598 |
| `update-standards` | 5/5 | 181 |
| `wrap-up` | 2/5 | 195 |

Score distribution: 12 precise (5), 12 good (4), 3 mixed (3), 6 weak
(2), and 4 poor (1).

The highest-value corrections are the ten skills scoring 1 or 2. The recurrent
failure modes are:

1. **Generic language activates persistent or heavyweight workflows.** The
   largest cases are `tight-mode`, `loose-mode`, `git-workflow`, and
   `refactor-node`.
2. **A neighboring task is included as a trigger.** `daily-plan` claims ordinary
   schedule checks, while `wrap-up` claims generic session endings.
3. **The header describes operations or output instead of invocation.** This is
   most visible in `cloud-files`, `make-tex-docstring`, `recurring-tasks`, and
   `wrap-up`.
4. **Internal-only or special-purpose boundaries are missing.** Several service
   skills do not clearly separate setup, ordinary operations, triage, and
   orchestration.

## Skill-by-skill audit

### `bib-audit` — 4/5, good

**Current description**

> Use when auditing a .bib bibliography file for syntactic validity, style consistency, external metadata verification, or duplicate/version conflicts; or when applying approved corrections to a .bib file or LaTeX project citations.

**Assessment:** The audit triggers match the body well and “approved
corrections” preserves the report-before-transformation boundary. The last
phrase can still load the skill for general citation edits unrelated to a
bibliography audit. It also does not distinguish bibliography corrections from
ordinary LaTeX citation prose or formatting.

**Recommended trigger**

> Use when the user asks to audit a bibliography or apply corrections identified by a bibliography audit. Do not use for general LaTeX citation editing.

### `cloud-files` — 1/5, poor

**Current description**

> Read, write, and delete plain files under a configured Google Drive LLM root through skill-owned Python scripts. Use when another skill needs bounded cloud-file storage or a separately prompted broader read from the configured Drive root.

**Assessment:** It does not start with `Use when`, leads with implementation and
capabilities, and makes direct invocation asymmetric: another skill may request
bounded storage, while a direct user request is described only as a broader
read. The intended boundary is any read from or write to the configured LLM root
of a remote, whether requested directly by the user or delegated by another
skill. The trigger should not narrow that boundary to list/plan storage or to a
particular remote provider. Although the current implementation is Google-backed,
the invocation boundary is deliberately provider-neutral because additional
remote sources are expected.

**Recommended trigger**

> Use when the user or another skill needs to read from or write to the configured LLM root of a remote. Do not use for local files or remote paths outside that LLM root.

### `connect-google` — 4/5, good

**Current description**

> Use when a Google service needs a shared OAuth client prepared, or when the user asks to prepare Google authentication for Famulus.

**Assessment:** It correctly identifies shared OAuth preparation. “A Google
service needs” is slightly too implicit, and the header does not state the
important boundary between initial setup/reauthorization and normal Calendar,
Drive, or Gmail operations.

**Recommended trigger**

> Use when the user needs to set up or restore Google authentication for Famulus. Do not use for ordinary Google-service operations.

### `daily-plan` — 2/5, weak

**Current description**

> Use when the user asks to plan their day, see what to work on today, check their schedule, or review today's actions. Triggers on "plan my day", "what should I do today", "what should I work on", "show my plan", or similar.

**Assessment:** Planning or showing a stored daily plan is accurate. “Check their
schedule” belongs to `g-calendar` when the user only wants calendar data, and
“review today's actions” can mean `list-manager` or the end-of-day `wrap-up`
workflow. The header therefore loads a multi-source orchestration skill for
several narrower reads.

**Recommended trigger**

> Use when the user asks to plan their day, decide what to work on today, or review an existing daily plan. Do not use for a standalone calendar or list request, or for an end-of-day wrap-up.

### `email-client` — 3/5, mixed

**Current description**

> Use when reading, listing, searching, or sending email for the user across any nickname registered in the account registry.

**Assessment:** The core message operations are accurate, but the description
omits attachment retrieval, account-registry management, replies, and explicit
provider checks. It also fails to exclude inbox triage, so “process my inbox”
can load both this skill and `email-triage` even though triage should own the
user-facing workflow and call the client as a dependency.

**Recommended trigger**

> Use when the user asks to access or manage email or a registered email account. Do not use when the primary request is inbox triage or shared Google authentication setup.

### `email-triage` — 4/5, good

**Current description**

> Use when asked to triage email, process the inbox, or surface action items from recent emails.

**Assessment:** The primary intent is captured well. The missing boundary is
that this is a since-last-run triage and action-routing workflow, not a synonym
for reading, searching, or summarizing one message.

**Recommended trigger**

> Use when the user asks for inbox-level email triage or processing. Do not use for ordinary email access, sending, or analysis of a single message.

### `find-handoff-candidates` — 5/5, precise

**Current description**

> Use when you need a mechanical, non-interpretive scan of today's (or another day's) work sessions to find ones that had substantial activity but no completed handoff. Typically invoked by wrap-up, not directly by the user.

**Assessment:** No material shortfall. It states the mechanical/non-interpretive
boundary and correctly marks the skill as normally internal to `wrap-up`, which
substantially reduces accidental direct loading.

**Recommended trigger**

> Use when `wrap-up` needs to identify recent work sessions that may still require a handoff. Do not invoke directly for transcript review, interpretation, or summarization.

### `fix-bisync` — 5/5, precise

**Current description**

> Use when a live rclone bisync job fails, requests `--resync`, has corrupted state, needs its first real fault or culprit files identified, or repeatedly fails and needs a safe prevention or recovery decision.

**Assessment:** No material shortfall. It requires a live bisync failure and
names the diagnostic and recovery intents without attracting generic rclone or
one-off sync questions.

**Recommended trigger**

> Use when a live rclone bisync job has failed or repeatedly becomes unhealthy and the user needs diagnosis or recovery guidance. Do not use for generic rclone questions or one-off syncs.

### `formal-prose-review` — 5/5, precise

**Current description**

> Use when technical prose needs grammar, typos, punctuation, wording, clarity, concision, or formal-tone editing while preserving mathematical and substantive content; not for proof verification, notation review, document-level flow, or substantive rewriting.

**Assessment:** No material shortfall. The positive trigger and exclusions map
cleanly onto the neighboring research-review skills.

**Recommended trigger**

> Use when the user asks for sentence-level editing of technical prose while preserving its substance. Do not use for proof, notation, document-flow, or substantive review.

### `g-calendar` — 3/5, mixed

**Current description**

> Use when the user asks to read or change Google Calendar events, calendars, schedules, meetings, or availability.

**Assessment:** Direct Calendar reads and mutations are covered, but “schedules”
is broad enough to catch daily planning and non-Google scheduling questions.
The description needs to say that the requested object is data in the user's
Google Calendar and exclude multi-source day planning.

**Recommended trigger**

> Use when the user asks to view or change their Google Calendar. Do not use for daily planning.

### `get-weather` — 5/5, precise

**Current description**

> Use when the user asks about weather for the current location or a named location, including a specific day or date range.

**Assessment:** No material shortfall. It is intent-based, complete for the
supported date/location routes, and does not describe workflow.

**Recommended trigger**

> Use when the user asks about weather for the current location or a named location, including a specific day or date range.

### `git-workflow` — 2/5, weak

**Current description**

> Use when working in any git repo — committing, staging, checking branch state, or deciding whether to suggest a commit. Also use before editing files in any repo to verify branch safety.

**Assessment:** The mandatory pre-edit branch check and explicit Git operations
are accurate. The opening phrase “working in any git repo” is much broader than
the body and loads the skill for read-only inspection, explanation, or any task
whose current directory merely happens to be a repository.

**Recommended trigger**

> Use when a task will edit a Git repository or the user asks to inspect or change Git state. Do not use solely because a read-only task happens inside a repository.

### `hook-maker` — 5/5, precise

**Current description**

> Use when designing, creating, installing, or refactoring assistant hooks that must work across multiple hosts or future agent runtimes, especially when a shared hook purpose needs host-specific lifecycle bindings or output schemas.

**Assessment:** No material shortfall. It scopes the skill to assistant
lifecycle hooks with a cross-host/runtime requirement and identifies the exact
architectural condition that distinguishes it from ordinary automation.

**Recommended trigger**

> Use when the user asks to create or change an assistant lifecycle hook that must support multiple hosts or runtimes. Do not use for ordinary Git hooks or single-host automation.

### `initialize-tdd` — 5/5, precise

**Current description**

> Use when starting a brand-new project that should follow a staged, approval-gated TDD workflow, especially a Python project.

**Assessment:** No material shortfall. “Brand-new project” and
“approval-gated TDD workflow” prevent it from loading for adding tests to an
existing project or for ordinary feature TDD.

**Recommended trigger**

> Use when the user asks to initialize a brand-new TDD project. Do not use for adding TDD to an existing project.

### `install-assistant-tools` — 4/5, good

**Current description**

> Use when installing, repairing, updating, or propagating the assistant, collab, coauthor, or workspace helper commands on a machine, or when their launcher or shell integration is missing or stale.

**Assessment:** The supported command families and repair signals are clear.
“The assistant” and “workspace helper commands” remain mildly generic and can
attract unrelated assistant/software installation requests.

**Recommended trigger**

> Use when the user asks to install, update, propagate, or repair the `assistant`, `collab`, `coauthor`, or workspace helper commands, including missing or stale launchers and shell integration. Do not use for unrelated software or plugin installation.

### `latex-workshop` — 4/5, good

**Current description**

> Use when a user wants to compile, rebuild, or troubleshoot a TeX/LaTeX document and the build should match VS Code LaTeX Workshop settings, recipes, and output-directory conventions.

**Assessment:** The LaTeX Workshop dependency is clear, but the header does not
say that the document must be inside a VS Code project whose configuration
governs the build. Without that boundary, the skill can load for unrelated
LaTeX compilation elsewhere.

**Recommended trigger**

> Use when compiling or troubleshooting a LaTeX document inside a VS Code project whose build is governed by LaTeX Workshop. Do not use for LaTeX compilation outside a VS Code project.

### `list-manager` — 4/5, good

**Current description**

> Use whenever the user refers to any personal list they keep — a list of any name or topic (todo, shopping, reading, packing, gifts, projects, and any other) — or asks to see, add to, check off, complete, reorder, rename, set a deadline on, or remove items in one. Any phrasing like "my <X> list", "what's on my <X>", "add X to my list", "show my <X>", "mark X done" triggers this, whatever the list is called.

**Assessment:** The persistent personal-list boundary and mutations are unusually
explicit. The header is longer than needed and still lacks a negative boundary
against one-off generated lists, repository inventories, or prose checklists;
generic words such as “projects” can otherwise cause false positives.

**Recommended trigger**

> Use when the user asks to view or change a persistent personal list. Do not use for an ad hoc generated list, repository inventory, or prose checklist.

### `llm-wakeup` — 5/5, precise

**Current description**

> Use when the user wants to schedule a supported assistant session after a usage reset, infer a wakeup from a timeout, or manage per-session automatic wakeups.

**Assessment:** No material shortfall. It captures explicit scheduling,
timeout-derived inference, and automatic-wakeup policy without attracting
general scheduler tasks.

**Recommended trigger**

> Use when the user asks to schedule or manage an automatic assistant-session wakeup after a usage reset or timeout.

### `loose-mode` — 2/5, weak

**Current description**

> Use when the user invokes "loose mode" or asks for broad exploration, strategy, options, or a fast overview — when breadth and speed matter more than certainty. Contrasts with tight-mode.

**Assessment:** Explicit “loose mode” requests are correct. The alternative
trigger matches a large share of ordinary brainstorming, strategy, and overview
requests, yet the body makes this a persistent mode that should not be exited
without instruction. Inferring a persistent modifier from an ordinary task is a
high false-positive risk.

**Recommended trigger**

> Use when the user explicitly asks to enter or continue loose mode. Do not infer it from an ordinary request for ideas, options, strategy, or an overview.

### `make-tex-docstring` — 1/5, poor

**Current description**

> Create or propose a top-of-document TeX comment block that records the document profile and intended use.
>
> Use when:
> - a TeX document is missing a top-of-document profile comment
> - the user wants to add or standardize a document docstring/header comment
> - another skill needs document-profile information and the file does not already state it clearly
> - a skill marked `Category: document-oriented` is about to be applied to a `.tex` file — check for a profile comment before proceeding
>
> Do not use when:
> - the file already has a suitable top-of-document profile comment
> - the user wants substantive editing rather than document-profile metadata
>
> Success criteria:
> - identify or reliably infer the document profile
> - ask only for information that cannot be inferred safely
> - produce one canonical TeX comment block
> - keep the schema in one place and avoid ad hoc variations across skills
> - do not edit the file unless the user agrees

**Assessment:** The header begins with an output, embeds workflow and success
criteria, and refers to the stale `Category: document-oriented` mechanism. Most
importantly, it makes a missing comment sufficient to load the skill before any
document-oriented TeX task, even when the selected task can infer its profile or
does not need the metadata. That is a systematic unnecessary-load path.

**Recommended trigger**

> Use when the user asks to create or standardize a TeX document-profile comment, or when a selected TeX task requires profile information that the document does not state clearly. Do not use merely because the target is a `.tex` file.

### `math-dependency-graph` — 4/5, good

**Current description**

> Use when a LaTeX math document needs a direct dependency graph of its assumptions-to-results structure, covering standing assumptions, definitions, mathematical results, notation, and evidence, as canonical JSON or interactive HTML.
>
> Do not use when the main goal is proof validation, notation cleanup, prose review, or a literature map.

**Assessment:** The invocation intent and exclusions are clear, but the header
goes beyond the trigger by inventorying graph contents and naming output
formats. Those details belong in the skill body, not discovery metadata.

**Recommended trigger**

> Use when the user asks for a direct assumptions-to-results dependency graph of a LaTeX mathematical document. Do not use for proof, notation, prose, or literature review.

### `notation-review` — 5/5, precise

**Current description**

> Use when mathematical notation needs review for lightness, unification, reuse across scopes, or semantic transparency.
>
> Use when:
> - the user asks to review, simplify, unify, standardize, or clean up notation
> - related objects should share a notation family, or notation should be lighter, more reusable, or more self-explanatory
> - the user asks whether notation follows standard conventions or the paper's local conventions
>
> Do not use when:
> - the main issue is proof validity, prose editing, stylistic rewriting, or grammar
> - the user wants a proof plan or mathematical strategy rather than notation review

**Assessment:** No material shortfall. The positive trigger and exclusions
separate notation work from proof, prose, and strategy work.

**Recommended trigger**

> Use when the user asks to review, simplify, or standardize mathematical notation. Do not use for proof, prose, or document-flow review.

### `pdf-to-markdown` — 4/5, good

**Current description**

> Use when converting a research paper PDF to readable text for LLM analysis of technical content.

**Assessment:** The domain and preprocessing intent are accurate. The body
actually prefers retrieving LaTeX source and uses PDF conversion only as a
fallback, so the current wording can cause unnecessary conversion when usable
source/text already exists. It also undersells that the skill can start from a
paper identity rather than only a local PDF.

**Recommended trigger**

> Use when research-paper analysis requires readable source or text that is not already available. Do not use for generic non-research PDFs.

### `prepare-handoff` — 5/5, precise

**Current description**

> Use when the user explicitly invokes this skill to prepare a handoff or preserve project continuity before pausing, ending, or switching tracks after work that produced decisions, failed paths, interface contracts, environment quirks, or preferences worth preserving. Do not auto-use for general "remember this" requests, short clarifications, or incidental mentions of switching inside another task.

**Assessment:** No material shortfall. It combines explicit invocation,
continuity-value criteria, transition context, and concrete exclusions. This is
the strongest model in the repository for preventing incidental phrase matches.

**Recommended trigger**

> Use when the user explicitly asks for a handoff, or when pausing, ending, or switching away from substantial project work that produced continuity-relevant knowledge. Do not use for ordinary remember-this requests, short clarifications, incidental switching, or formal closure with no handoff need.

### `proof-audit` — 5/5, precise

**Current description**

> Use when the user asks to audit a mathematical proof, proof sketch, argument, lemma, proposition, or theorem statement for soundness, coherence, redundancy, hidden assumptions, invalid theorem use, quantifier or domain mistakes, corner cases, or missing hypotheses; asks to check, verify, validate, stress-test, or debug a proof or claimed mathematical implication; wants diagnosis before rewriting; or asks whether an argument actually proves its stated conclusion.
>
> Do not use when the main task is brainstorming, notation cleanup, prose polishing, writing a proof from scratch, computation, code, or LaTeX formatting.

**Assessment:** No material shortfall. It is long but every clause sharpens
invocation and the exclusions closely track adjacent skills.

**Recommended trigger**

> Use when the user asks to audit the soundness, coherence, or redundancy of a mathematical proof or claimed implication. Do not use to write a new proof or for notation, prose, computation, code, or LaTeX work.

### `recurring-tasks` — 1/5, poor

**Current description**

> Manage recurring AI job automation via the host's native per-user scheduler (systemd on Linux, launchd on macOS, Task Scheduler on Windows). Define jobs in jobs.yaml, enable/disable/test them, and monitor health.

**Assessment:** It never states `Use when`, and it describes architecture,
configuration, workflow, and supported platforms rather than a user intent.
There is no exclusion for one-off commands, generic scheduler questions, or
recurring non-AI jobs.

**Recommended trigger**

> Use when the user asks to set up or manage a recurring AI job. Do not use for one-off commands or generic scheduler questions.

### `refactor-node` — 1/5, poor

**Current description**

> Use when auditing or refactoring a whole registered skill-system node or an owned file, class, function, method, or instruction section

**Assessment:** The sentence is not terminated and incorrectly calls the
subject a “skill-system node” rather than an Officina node. More importantly,
“an owned file, class, function, method, or instruction section” makes almost
any code or instruction review match. The actual workflow is a heavyweight,
standards-driven, behavior-preserving refactor of a registered Officina node and
its owned sources; it is not the route for feature work, bug fixes, generic
review, or arbitrary repository files.

**Recommended trigger**

> Use when the user asks for a behavior-preserving audit or refactor of a registered Officina node or one of its owned sources. Do not use for feature work, bug fixes, generic code review, or files outside registered node ownership.

### `regenerate-blueprints` — 3/5, mixed

**Current description**

> Use when an existing skill's blueprint.yaml needs regeneration or refresh.

**Assessment:** It identifies the artifact but “refresh” implies updating the
source in place. The actual skill generates one schema-documented replacement
candidate in temporary storage and explicitly never modifies the existing
blueprint. It also needs a boundary against normal blueprint editing or
synchronization.

**Recommended trigger**

> Use when an existing skill blueprint needs regeneration, whether requested directly or required by another skill. Do not use for ordinary blueprint editing or synchronization.

### `semantic-integration` — 4/5, good

**Current description**

> Use when a complicated Git integration between substantially diverged branches requires reconstructing source intent against the target's current architecture, especially after failed merges, broad conflicts, or architectural drift; do not use for ordinary conflict-free merges or rebases.

**Assessment:** The trigger identifies the right general boundary, but
“substantially diverged” and “architectural drift” are labels rather than usable
tests. It mentions failed merges and broad conflicts without distinguishing
structural conflicts from ordinary localized ones. Its conflict-free exclusion
also misses a second failure mode: merge or rebase can apply mechanically while
placing source changes into structures that the target architecture has
replaced, thereby losing their intent.

**Recommended trigger**

> Use when integrating substantially diverged Git branches and merge or rebase is inadequate because it produces broad structural conflicts, or because mechanical application would place source changes into structures the target architecture has replaced and thereby lose their intent. Do not use when direct application and localized conflict resolution can preserve both branches' intended behavior.

### `skill-certifier` — 4/5, good

**Current description**

> Use when mechanical checks and semantic review should issue fresh node certificates for an exact committed repository state.

**Assessment:** It distinguishes certificate issuance from currentness reads,
but frames successful review and committed-state prerequisites as invocation
prerequisites. The skill must also load when certification is requested for an
unready state so it can report the failure. The header also omits the Officina
node boundary.

**Recommended trigger**

> Use when fresh certificates are requested for one or more Officina nodes. Do not use merely to check certificate currentness or canonical node hashes.

### `skill-drift` — 2/5, weak

**Current description**

> Use when reading signed certificate currentness or canonical node hashes for Famulus modules.

**Assessment:** It incorrectly calls the certified objects “Famulus modules”;
they are Officina nodes. It is also phrased in implementation jargon rather
than likely user intents such as “is this node certified/current or stale?” and
does not explicitly exclude certificate issuance, leaving an avoidable overlap
with `skill-certifier`.

**Recommended trigger**

> Use when the user asks whether Officina node certificates are current or stale, or asks for canonical node hashes. Do not use to issue certificates.

### `skill-maker` — 2/5, weak

**Current description**

> Use when creating or editing a personal skill in the shared skills directory

**Assessment:** The sentence is not terminated, and “editing” matches every
change to a personal skill. That overlaps behavior-preserving refactors,
blueprint-only regeneration, certificate work, and standards maintenance. The
body is the authoring route for creating a skill or changing its intended
behavior/interface under repository standards.

**Recommended trigger**

> Use when the user asks to create a personal skill or change an existing personal skill's intended behavior or public interface in the shared skills directory. Do not use for behavior-preserving refactoring, blueprint regeneration, certificate work, or standards maintenance.

### `technical-flow-review` — 5/5, precise

**Current description**

> Use when a technical document needs review for flow, structure, motivation, or readability.
>
> Especially when:
> - the user wants feedback on section-level or whole-document flow
> - the user wants to know whether the problem, goal, or contribution is obvious early enough
> - the user wants to assess whether the intended audience can follow the document without mastering all technical details
> - the user wants feedback on section ordering, motivation, signposting, or overall readability
>
> Do not use when:
> - the main task is proof verification, notation review, or sentence-level prose editing or copyediting

**Assessment:** No material shortfall. It gives concrete document-level triggers
and cleanly excludes the three neighboring review skills.

**Recommended trigger**

> Use when the user asks for document-level review of technical structure, motivation, or reader flow. Do not use for sentence editing, proof review, or notation review.

### `tight-mode` — 4/5, good

**Current description**

> Use when the user invokes "tight mode" or asks for rigorous, exact, verified output — when certainty matters more than breadth or speed. Contrasts with loose-mode.

**Assessment:** Explicit “tight mode” requests and emphatic requests for rigor
are both valid triggers. The current wording is close, but it does not
distinguish an emphasized requirement for rigor and careful detail from an
ordinary request that merely expects baseline accuracy.

**Recommended trigger**

> Use when the user explicitly asks for tight mode or emphasizes that rigor, verification, or careful attention to detail should take priority over speed or breadth. Do not use for ordinary requests that merely expect baseline accuracy.

### `tool-applicability` — 4/5, good

**Current description**

> Use when checking whether a theorem, framework, or mathematical tool can achieve a target objective in the current setting and, if not, what nearest valid result it still delivers, including when:
> - the user asks whether a theorem, method, machinery, or formalism applies
> - the user wants to know whether a tool proves a target under current assumptions, what added assumptions would make it work, or what weaker nearby result it gives
>
> Do not use when:
> - the main task is line-by-line proof auditing, broad proof strategy without a specific candidate tool, or notation or document-structure review

**Assessment:** The body is specifically about mathematical tools: theorems,
lemmas, formalisms, proof methods, and related machinery. It remains useful in
mathematically formal neighboring fields such as economics or econometrics,
but not as a generic software-tool or framework evaluator. The unqualified word
“framework” in the current description obscures that boundary.

**Recommended trigger**

> Use when the user asks whether a specific mathematical theorem, method, framework, or formalism applies to a target problem. Do not use for generic software tools, line-by-line proof review, or broad strategy without a candidate mathematical tool.

### `update-standards` — 5/5, precise

**Current description**

> Use when creating, changing, splitting, importing, or auditing a canonical standard document and its pinned dependents, generated views, declared evidence, or enforcement artifacts.

**Assessment:** No material shortfall. “Canonical standard document” prevents
generic policy or documentation work from matching, while the dependent
artifacts correctly keep standards-maintenance work within one invocation
boundary.

**Recommended trigger**

> Use when the user asks to create, change, or audit a canonical repository standard. Do not use for generic policy or documentation work.

### `wrap-up` — 2/5, weak

**Current description**

> Use when ending the work day or wrapping up a session. Reads today's plan, asks which incomplete actions were completed, prompts for calendar activity notes, and captures any new items for lists.

**Assessment:** The first sentence does not require language asking for formal
closure, so the skill can load merely because a task or session appears to be
ending. The second sentence describes workflow and outputs, which the header
standard excludes. The trigger should instead be sensitive to explicit closure
language such as “wrap up,” “close out this session,” or “finish the day.” Status
or completeness questions such as “anything else remaining?” or “are we done
here?” do not ask for formal closure and must not trigger the skill.

**Recommended trigger**

> Use when the user explicitly asks to wrap up or formally close the workday or current session. Do not use for status or completeness questions such as “anything else remaining?” or “are we done here?”, ordinary task completion, or a handoff-only request.

## Recommended edit order

1. Fix the four score-1 descriptions: `refactor-node`, `make-tex-docstring`,
   `cloud-files`, and `recurring-tasks`.
2. Fix the six score-2 descriptions: `git-workflow`, `daily-plan`, `wrap-up`,
   `loose-mode`, `skill-maker`, and `skill-drift`.
3. Tighten the four score-3 service/maintenance boundaries.
4. Apply the score-4 refinements only after the false-positive routes above are
   removed.
5. Treat the score-5 rewrites as optional concision edits; their current
   invocation boundaries are already sound.

The first edit pass should change frontmatter descriptions only. Any mismatch
that instead requires changing discovery metadata, public interfaces, or skill
behavior is a separate design change and should not be hidden inside description
cleanup.
