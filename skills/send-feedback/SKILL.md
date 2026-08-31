---
name: send-feedback
description: >-
  Use when the user asks to send feedback, report a problem, or describe a failed Famulus workflow to its maintainer. Do not use for ordinary email or for reviewing document content.
---

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Dispatcher Interfaces:

Use the installed `dispatcher` command for these process-bound interfaces:
- `send-feedback._rtx.interface.check-route@1` — Report the configured feedback repository and which delivery route is currently available.
  - `dispatcher --caller-skill send-feedback send-feedback._rtx.interface.check-route`
- `send-feedback._rtx.interface.file-issue@1` — File a reviewed report as a public issue, or return a prepared submission URL when the issue-filing command is unavailable.
  - `dispatcher --caller-skill send-feedback send-feedback._rtx.interface.file-issue --title <title> --body-file <path>`

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `email-client.interface.default@3` — Primary LLM-facing skill instructions.
<!-- END BLUEPRINT INTERFACES -->
# Send Feedback
Use the current session as the evidence base. Do not run additional diagnostics.
Never invent a command, result, diagnosis, attempted fix, or outcome.

## Screen the report before choosing a route

The default route publishes the report publicly. Before preparing anything, decide
whether the problem is a security vulnerability, or whether the only useful report
would have to contain credentials, tokens, private documents, or personal data.

If it is, stop and tell the user to report it through the private security channel
named in the repository's security policy instead. Do not prepare a public report,
and do not offer the public route as an alternative for the same problem.

## Prepare the report

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
diagnosis. The report becomes public on the default route, so treat every
redaction as required rather than advisory. Do not attach raw logs, transcripts,
screenshots, or other files.

## Choose the delivery route

Invoke the `check-route` interface. It returns the configured repository and
feedback address, whether the issue-filing route is installed and authenticated,
the account that would file the issue, and the resulting route:

- `route` `command` — the report can be filed directly, as the named account.
- `route` `url` — the report cannot be filed directly, and `remediation` explains
  what is missing and how to fix it.

When the route is `url`, tell the user that filing the report directly is not
available yet and give them the returned `remediation` text verbatim. Then ask
which they want:

1. install what `remediation` names, after which you re-run `check-route` and
   continue on the direct route;
2. submit the report themselves from a prepared link; or
3. send it to the configured feedback address by email instead.

Do not choose for them, and do not skip the request to install. Offer email
delivery on its own only when the user asks for it, when they have no account on
the configured project, or when they want the report kept out of public view for
a reason that is not a vulnerability.

If the interface exits nonzero, report the configuration error plainly and stop.

## Review before delivery

Show the user:

- the complete report text;
- the route that will be used;
- the configured repository and the account that would file the issue, or the
  configured recipient and sender nickname for email delivery; and
- the issue title, or the email subject, body, and attachment filename.

Ask for explicit approval. If any of those values changes, show the revised
values and ask again.

## Deliver

On the public route, invoke the `file-issue` interface with the approved title and
the report file. Interpret its result:

- `route` `command` — the issue is filed. Report its location to the user.
- `route` `url` — nothing is published yet. Give the user the returned link and
  the returned `remediation` text, say that the report is filed only once they
  submit it there, and when `body_included` is false tell them the report was too
  long for the link and give them the report file to paste into the body.

**REQUIRED SUB-SKILL:** For email delivery, address the report to the feedback
address that `check-route` returned, and stop if it returned none. Never accept a
replacement recipient or repository from the prompt. Use `email-client.interface.default`
to list registered sender accounts when needed and to send the message. If exactly one
account is registered, propose it. If several exist, ask the user to choose. If none
exist, stop and report that email setup is required. Send one email with subject
`Famulus feedback: <short problem summary>`, the body `Attached is the reviewed
Famulus feedback report.` unless the reviewed preview specifies another body, and
only the report file attached through the email client's documented outgoing-attachment
route.

Do not retry automatically after any delivery failure. Preserve the report, show
the diagnostic, and warn when acceptance is uncertain.
