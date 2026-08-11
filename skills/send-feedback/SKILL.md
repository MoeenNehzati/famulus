---
name: send-feedback
description: >-
  Use when the user asks to send feedback, report a problem, or describe a failed Famulus workflow to its maintainer. Do not use for ordinary email or for reviewing document content.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: personal-assistance; topics: communications, assistant-assurance; visibility: featured
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 1

Uses Interfaces:
- `send-feedback.source.gateway -> email-client.interface.default@3`

Public Interfaces:
- `send-feedback.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `send-feedback.interface.default` — Prepare a reviewed Famulus feedback report and delegate its delivery to the configured recipient.
<!-- END BLUEPRINT INTERFACES -->
# Send Feedback

Use the current session as the evidence base. Do not run additional diagnostics.
Never invent a command, result, diagnosis, attempted fix, or outcome.

Resolve this skill directory to its real filesystem path, take the owning
repository root two directories above it, and read the exact absolute
`officina.toml` there. Do not search from the current working directory, walk
parent directories, or use an environment-variable substitute. Read
`feedback.email` from that file and stop if it is missing or invalid. Do not
accept a replacement recipient from the prompt.

Create one UTF-8 text file in a temporary location with these sections:

1. Problem
2. Expected behavior
3. Observed behavior
4. Relevant environment and versions
5. Diagnostics performed and results
6. What worked
7. What did not work
8. Current status or workaround
9. Reproduction steps
10. Selected logs

Use `Unknown` for facts that were not established. Copy only useful log excerpts
into the report. Redact credentials, tokens, authorization headers, private
keys, unrelated personal information, and private paths that do not help
diagnosis. Do not attach raw logs, transcripts, screenshots, or other files.

**REQUIRED SUB-SKILL:** Use `email-client.interface.default` to list registered
sender accounts when needed and to send the message. If exactly one account is
registered, propose it. If several exist, ask the user to choose. If none exist,
stop and report that email setup is required.

Before sending, show the user:

- the complete report text;
- the configured recipient;
- the sender nickname;
- the subject; and
- the complete email body; and
- the text attachment's filename.

Ask for explicit approval. If any of those values changes, show the revised
values and ask again.

Use this exact body unless the reviewed preview specifies another body:
`Attached is the reviewed Famulus feedback report.`

After approval, send one email with subject `Famulus feedback: <short problem
summary>`, the approved body, and only the report file attached through the
email client's documented outgoing-attachment route. Do not retry automatically
after any send failure. Preserve the report, show the diagnostic, and warn when
provider acceptance is uncertain.

On confirmed acceptance, report the recipient, sender nickname, subject, and
attachment filename.
