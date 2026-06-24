#!/usr/bin/env bash
# Two operations against GDrive:assistant/lists/:
#   lists.sh read [name]   - no name: list all lists; name: print that list's contents
#   lists.sh write <name>  - write stdin as the new full content of <name>;
#                             empty stdin deletes the list file
set -euo pipefail

op="${1:-}"
name="${2:-}"
remote_root="${LISTS_REMOTE_ROOT:-GDrive:assistant/lists}"
timeout_seconds="${LISTS_RCLONE_TIMEOUT_SECONDS:-45}"

run_rclone() {
  timeout "${timeout_seconds}s" rclone "$@"
}

remote_path() {
  local list_name="$1"
  printf '%s/%s.md' "$remote_root" "$list_name"
}

case "$op" in
  read)
    if [ -z "$name" ]; then
      run_rclone lsf "${remote_root}/" --include "*.md"
    else
      run_rclone cat "$(remote_path "$name")"
    fi
    ;;
  write)
    if [ -z "$name" ]; then
      echo "usage: lists.sh write <name>" >&2
      exit 1
    fi
    content="$(cat)"
    if [ -z "$content" ]; then
      run_rclone deletefile "$(remote_path "$name")"
    else
      printf '%s\n' "$content" | run_rclone rcat "$(remote_path "$name")"
    fi
    ;;
  *)
    echo "usage: lists.sh {read|write} [name]" >&2
    exit 1
    ;;
esac
