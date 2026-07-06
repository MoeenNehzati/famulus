# Profiles & Settings

Agent configurations for Claude and Codex, auto-generated from source config files.

## Overview

| Profile | Purpose | Codex Model | Codex Effort | Claude Model | Claude Effort | Claude Thinking |
|---------|---------|-------------|--------------|--------------|---------------|-----------------|
| **assistant** | Secretary: fetch info, write, implement easy logic | gpt-5.4-mini | low | claude-haiku-4-5-20251001 | low | 2000 |
| **collab** | Serious coding, focused on documentation/learning | gpt-5.4 | medium | claude-sonnet-4-6 | medium | 8000 |
| **coauthor** | Math/research mode, deep thinking and rigor | gpt-5.4 | high | claude-opus-4-8 | high | 16000 |

## Details

Generated from:
- Codex: `profiles/*.config.toml`
- Claude: `profiles/*_claude_setting.json`

**To update:** Run `bash scripts/generate-settings-table.sh` after modifying profile configs.

_Last generated: 2026-07-06 18:14:03 UTC_
