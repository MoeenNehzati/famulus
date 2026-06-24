#!/usr/bin/env bash
set -euo pipefail

op="${1:-}"
relpath="${2:-}"
remote="${CLOUD_FILES_REMOTE:-GDrive}"
timeout_seconds="${CLOUD_FILES_TIMEOUT_SECONDS:-45}"

validate_remote() {
  case "$remote" in
    ""|*:*|*/*|*\\*)
      echo "invalid CLOUD_FILES_REMOTE: $remote" >&2
      exit 2
      ;;
  esac
}

validate_relpath() {
  local path="$1"
  local allow_empty="${2:-false}"

  if [ -z "$path" ]; then
    if [ "$allow_empty" = "true" ]; then
      return 0
    fi
    echo "path required" >&2
    exit 2
  fi

  case "$path" in
    /*|*'..'*|*\\*)
      echo "invalid assistant-relative path: $path" >&2
      exit 2
      ;;
  esac
}

assistant_path() {
  local path="$1"
  if [ -z "$path" ]; then
    printf '%s:assistant/' "$remote"
  else
    printf '%s:assistant/%s' "$remote" "$path"
  fi
}

run_remote() {
  timeout "${timeout_seconds}s" rclone "$@"
}

validate_remote

case "$op" in
  list)
    validate_relpath "$relpath" true
    run_remote lsf "$(assistant_path "$relpath")"
    ;;
  read)
    validate_relpath "$relpath"
    run_remote cat "$(assistant_path "$relpath")"
    ;;
  write)
    validate_relpath "$relpath"
    run_remote rcat "$(assistant_path "$relpath")"
    ;;
  delete)
    validate_relpath "$relpath"
    run_remote deletefile "$(assistant_path "$relpath")"
    ;;
  *)
    echo "usage: cloud-files.sh {list [path]|read <path>|write <path>|delete <path>}" >&2
    exit 2
    ;;
esac
