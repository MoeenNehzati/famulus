#!/usr/bin/env bash
# Two operations against assistant/lists/ through the cloud-files skill:
#   lists.sh read [name]   - no name: list all lists; name: print that list's contents
#   lists.sh write <name>  - write stdin as the new full content of <name>;
#                             empty stdin deletes the list file
set -euo pipefail

op="${1:-}"
name="${2:-}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cloud_files="${script_dir}/../../cloud-files/scripts/cloud-files.sh"

list_path() {
  local list_name="$1"
  printf 'lists/%s.md' "$list_name"
}

case "$op" in
  read)
    if [ -z "$name" ]; then
      "$cloud_files" list lists | while IFS= read -r entry; do
        case "$entry" in
          *.md) printf '%s\n' "$entry" ;;
        esac
      done
    else
      "$cloud_files" read "$(list_path "$name")"
    fi
    ;;
  write)
    if [ -z "$name" ]; then
      echo "usage: lists.sh write <name>" >&2
      exit 1
    fi
    content="$(cat)"
    if [ -z "$content" ]; then
      "$cloud_files" delete "$(list_path "$name")"
    else
      printf '%s\n' "$content" | "$cloud_files" write "$(list_path "$name")"
    fi
    ;;
  *)
    echo "usage: lists.sh {read|write} [name]" >&2
    exit 1
    ;;
esac
