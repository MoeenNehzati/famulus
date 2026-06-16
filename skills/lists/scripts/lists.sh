#!/usr/bin/env bash
# Two operations against GDrive:assistant/lists/:
#   lists.sh read [name]   - no name: list all lists; name: print that list's contents
#   lists.sh write <name>  - write stdin as the new full content of <name>;
#                             empty stdin deletes the list file
set -euo pipefail

op="${1:-}"
name="${2:-}"

case "$op" in
  read)
    if [ -z "$name" ]; then
      rclone lsf "GDrive:assistant/lists/" --include "*.md" 2>/dev/null || true
    else
      rclone cat "GDrive:assistant/lists/${name}.md" 2>/dev/null || true
    fi
    ;;
  write)
    if [ -z "$name" ]; then
      echo "usage: lists.sh write <name>" >&2
      exit 1
    fi
    content="$(cat)"
    if [ -z "$content" ]; then
      rclone deletefile "GDrive:assistant/lists/${name}.md" 2>/dev/null || true
    else
      printf '%s\n' "$content" | rclone rcat "GDrive:assistant/lists/${name}.md"
    fi
    ;;
  *)
    echo "usage: lists.sh {read|write} [name]" >&2
    exit 1
    ;;
esac
