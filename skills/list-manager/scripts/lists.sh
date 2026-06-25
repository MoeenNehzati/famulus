#!/usr/bin/env bash
# Operations against assistant/lists/ through the cloud-files skill.
#
# Subcommands:
#   read [name]                    — no name: list all lists; name: print full contents
#   write <name>                   — write stdin as new full content; empty stdin deletes
#   unchecked <name>               — print only [ ] task lines (reads fresh from cloud)
#   grep <name> <text>             — fixed-string, case-insensitive search with line numbers
#   toggle <name> <id> check|uncheck — toggle checkbox by 4-char hex id (reads+writes atomically)
#   append <name>                  — append stdin item; auto-injects <!-- #id --> (reads+writes atomically)
#   migrate <name>                 — add <!-- #id --> to every task line that lacks one
set -euo pipefail

op="${1:-}"
name="${2:-}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cloud_files="${script_dir}/../../cloud-files/scripts/cloud-files.sh"

list_path() { printf 'lists/%s.md' "$1"; }

require_name() {
  if [ -z "$name" ]; then
    echo "usage: lists.sh $op <name>" >&2; exit 1
  fi
}

# Generate a 4-char lowercase hex ID from /dev/urandom (no subshell pipes that risk SIGPIPE).
gen_id() {
  local n
  n=$(od -vAn -N2 -tu2 < /dev/urandom | tr -dc '0-9')
  printf '%04x' "$n"
}

# Return an ID not already present as <!-- #XXXX --> anywhere in $1.
unique_id() {
  local content="$1" id
  while true; do
    id="$(gen_id)"
    case "$content" in
      *"<!-- #${id} -->"*) ;;   # collision — retry
      *) break ;;
    esac
  done
  printf '%s' "$id"
}

# Return 0 if $1 is a task line (any checkbox state + date prefix) with no existing ID.
# Uses bash built-in [[ =~ ]] to avoid spawning grep for every line in migrate.
TASK_RE='^[[:space:]]*- \[.\] \([0-9]{2}/[0-9]{2}/[0-9]{2}\) '
is_task_without_id() {
  local line="$1"
  [[ "$line" =~ $TASK_RE ]] || return 1
  case "$line" in *'<!-- #'*) return 1 ;; esac
}

case "$op" in

  # ── List / raw read ─────────────────────────────────────────────────────────

  read)
    if [ -z "$name" ]; then
      "$cloud_files" list lists | while IFS= read -r entry; do
        case "$entry" in *.md) printf '%s\n' "$entry" ;; esac
      done
    else
      "$cloud_files" read "$(list_path "$name")"
    fi
    ;;

  # ── Raw write (full content via stdin) ──────────────────────────────────────

  write)
    require_name
    content="$(cat)"
    if [ -z "$content" ]; then
      "$cloud_files" delete "$(list_path "$name")"
    else
      printf '%s\n' "$content" | "$cloud_files" write "$(list_path "$name")"
    fi
    ;;

  # ── Filtered reads (fresh from cloud each call) ─────────────────────────────

  unchecked)
    # Print only unchecked [ ] task lines, including their <!-- #id --> comments.
    # Callers should strip <!-- #id --> when displaying to the user.
    require_name
    "$cloud_files" read "$(list_path "$name")" \
      | grep -E '^[[:space:]]*- \[ \]' \
      || echo "(no unchecked items)"
    ;;

  grep)
    # Fixed-string, case-insensitive search. Safe when titles/descriptions contain brackets.
    # Usage: lists.sh grep <name> <text>
    require_name
    pattern="${3:-}"
    [ -z "$pattern" ] && { echo "usage: lists.sh grep <name> <text>" >&2; exit 1; }
    "$cloud_files" read "$(list_path "$name")" \
      | grep -niF "$pattern" \
      || echo "(no matches)"
    ;;

  # ── Atomic read-modify-write ops ────────────────────────────────────────────

  toggle)
    # Usage: lists.sh toggle <name> <id> check|uncheck
    # <id> is the 4-char hex value without the # prefix.
    require_name
    id="${3:-}"; action="${4:-}"
    [ -z "$id" ] || [ -z "$action" ] && {
      echo "usage: lists.sh toggle <name> <id> check|uncheck" >&2; exit 1
    }
    content="$("$cloud_files" read "$(list_path "$name")")"
    # Find the line by its ID comment using fixed-string search — safe against
    # brackets or other regex metacharacters in title/description text.
    linenum=$(printf '%s\n' "$content" | grep -nF "<!-- #${id} -->" | head -1 | cut -d: -f1)
    if [ -z "$linenum" ]; then
      echo "error: id '${id}' not found in list '${name}'" >&2; exit 1
    fi
    # Toggle only the checkbox on the matched line. sed targets the specific line
    # number, so brackets elsewhere in the file are never touched. The checkbox
    # [ ] or [x] always appears before any title text, so the substitution hits
    # the right token even if the title contains [ ] or [x].
    case "$action" in
      check)   new="$(printf '%s\n' "$content" | sed "${linenum}s/\[ \]/[x]/")" ;;
      uncheck) new="$(printf '%s\n' "$content" | sed "${linenum}s/\[x\]/[ ]/")" ;;
      *) echo "error: action must be 'check' or 'uncheck'" >&2; exit 1 ;;
    esac
    printf '%s\n' "$new" | "$cloud_files" write "$(list_path "$name")"
    printf '%s\n' "$new" | sed -n "${linenum}p"   # print toggled line for confirmation
    ;;

  append)
    # Append a new task from stdin; auto-injects <!-- #id --> at end of the checkbox line.
    # Pass only the item content (title + optional continuation lines) — do not include an id.
    require_name
    new_item="$(cat)"
    [ -z "$new_item" ] && { echo "error: nothing to append" >&2; exit 1; }
    existing="$("$cloud_files" read "$(list_path "$name")")"
    id="$(unique_id "$existing")"
    # Inject the id comment at the end of the first (checkbox) line only.
    first_line="$(printf '%s\n' "$new_item" | head -1) <!-- #${id} -->"
    rest="$(printf '%s\n' "$new_item" | tail -n +2)"
    if [ -n "$rest" ]; then
      new_item="${first_line}
${rest}"
    else
      new_item="$first_line"
    fi
    if [ -z "$existing" ]; then
      combined="$new_item"
    else
      combined="${existing}
${new_item}"
    fi
    printf '%s\n' "$combined" | "$cloud_files" write "$(list_path "$name")"
    printf 'appended with id #%s\n' "$id" >&2
    ;;

  gen-id)
    # Print a single collision-free 4-char hex ID for use in structural writes
    # (nested add, structured add) where the caller must embed the id in a heredoc.
    # Usage: id=$(scripts/lists.sh gen-id <name>)
    require_name
    content="$("$cloud_files" read "$(list_path "$name")")"
    unique_id "$content"
    printf '\n'
    ;;

  migrate)
    # Add <!-- #id --> to every task line that doesn't already have one.
    require_name
    content="$("$cloud_files" read "$(list_path "$name")")"
    if [ -z "$content" ]; then
      echo "list '${name}' is empty or does not exist" >&2; exit 1
    fi
    migrated=""
    count=0
    while IFS= read -r line; do
      if is_task_without_id "$line"; then
        # Pass combined (already-migrated lines + original content) to unique_id
        # so it avoids collisions with both new and pre-existing IDs.
        id="$(unique_id "${migrated}${content}")"
        line="${line} <!-- #${id} -->"
        count=$((count + 1))
      fi
      if [ -z "$migrated" ]; then
        migrated="$line"
      else
        migrated="${migrated}
${line}"
      fi
    done < <(printf '%s\n' "$content")
    printf '%s\n' "$migrated" | "$cloud_files" write "$(list_path "$name")"
    echo "migration complete: added IDs to ${count} items"
    ;;

  *)
    echo "usage: lists.sh {read|write|unchecked|grep|toggle|append|gen-id|migrate} [name] [args]" >&2
    exit 1
    ;;
esac
