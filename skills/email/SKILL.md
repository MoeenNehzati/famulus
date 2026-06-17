---
name: email
description: Use when reading, listing, searching, or sending email for the user. Covers both nyu (sn3379@nyu.edu) and personal (smnehzati@gmail.com) Gmail accounts.
---

# Email

Category: automation

Two Gmail accounts: **nyu** (`sn3379@nyu.edu`) and **personal** (`smnehzati@gmail.com`, default).

## Reading — himalaya (IMAP only)

himalaya v1.2.0 IMAP works correctly. **SMTP/send is broken** — see Known Bugs.

```bash
# List recent envelopes (default account = personal)
himalaya envelope list
himalaya envelope list -a nyu
himalaya envelope list -a nyu --folder "[Gmail]/Sent Mail"

# Read a message by ID
himalaya message read -a nyu 42

# Search
himalaya envelope list --query "subject:\"meeting\""

# List folders
himalaya folder list -a nyu
```

Gmail folder names: `INBOX`, `[Gmail]/Sent Mail`, `[Gmail]/Drafts`, `[Gmail]/Trash`, `[Gmail]/All Mail`.

## Sending — email-send.sh

**Never use `himalaya message send` or `himalaya template send` — both are broken** (v1.2.0 bugs; see below).

Use `~/.claude/skills/email/scripts/email-send.sh`. Body comes from stdin.

```bash
# Plain text
echo "Hi there." | ~/.claude/skills/email/scripts/email-send.sh \
  --from nyu \
  --to recipient@example.com \
  --subject "Hello"

# Multiple recipients + attachments (path:DisplayName — DisplayName optional)
cat <<'EOF' | ~/.claude/skills/email/scripts/email-send.sh \
  --from nyu \
  --to alice@example.com \
  --to bob@example.com \
  --subject "Papers" \
  --attach /path/to/file.pdf:"paper.pdf" \
  --attach /path/to/slides.pdf
Body text here.
EOF
```

**Accounts:** `--from nyu` or `--from personal`.

Passwords come from GNOME keyring via `secret-tool`. Keys:
- `secret-tool lookup account nyu service himalaya-smtp`
- `secret-tool lookup account personal service himalaya-smtp`
- `secret-tool lookup account nyu service himalaya-imap`
- `secret-tool lookup account personal service himalaya-imap`

## Known Bugs — himalaya v1.2.0

1. **SMTP backend hangs** after Gmail's `CertificateRequest` in TLS 1.3 (rustls bug). msmtp/curl handle it fine.
2. **Sendmail backend hangs** at "building new sendmail context" — never calls the external command. Affects even trivially configured commands like `cat`. No workaround; use email-send.sh directly.

These bugs apply regardless of how himalaya is configured. Do not attempt to fix by reconfiguring himalaya.

## Config Files

- himalaya: `~/.config/himalaya/config.toml`
- msmtp: `~/.config/msmtp/config` (accounts: `nyu`, `personal`)
- App Passwords stored in GNOME keyring via `secret-tool`
