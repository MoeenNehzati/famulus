#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  install_assistant_tools.sh [options]

Installs or updates:
  - assistant shell function
  - tw/tmux-workspace tmux helper
  - PATH entry for the helper bin directory

Options:
  --home DIR             Home directory to install into (default: $HOME)
  --assistant-dir DIR    Directory used by assistant before launching tools
                         (default: $HOME/Documents/assistant)
  --bin-dir DIR          Directory for tmux-workspace and tw
                         (default: $HOME/Documents/scripts/bin)
  --shell-rc FILE        Shell rc file to update (default: $HOME/.bashrc)
  --system-shell-rc FILE System bash rc file to update when writable
                         (default: /etc/bash.bashrc)
  --codex-home DIR       Codex state/config directory for profile symlinks
                         (default: $CODEX_HOME or $HOME/.codex)
  --no-system-shell-rc   Do not update the system bash rc file
  --dry-run              Print planned actions without writing files
  -h, --help             Show this help
EOF
}

home_dir="${HOME:-}"
assistant_dir=""
bin_dir=""
shell_rc=""
system_shell_rc="/etc/bash.bashrc"
codex_home=""
update_system_shell_rc=1
dry_run=0
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../../.." && pwd)"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --home)
      home_dir="${2:?--home requires a directory}"
      shift 2
      ;;
    --assistant-dir)
      assistant_dir="${2:?--assistant-dir requires a directory}"
      shift 2
      ;;
    --bin-dir)
      bin_dir="${2:?--bin-dir requires a directory}"
      shift 2
      ;;
    --shell-rc)
      shell_rc="${2:?--shell-rc requires a file}"
      shift 2
      ;;
    --system-shell-rc)
      system_shell_rc="${2:?--system-shell-rc requires a file}"
      update_system_shell_rc=1
      shift 2
      ;;
    --codex-home)
      codex_home="${2:?--codex-home requires a directory}"
      shift 2
      ;;
    --no-system-shell-rc)
      update_system_shell_rc=0
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "$home_dir" ]; then
  echo "HOME is not set; pass --home DIR." >&2
  exit 2
fi

assistant_dir="${assistant_dir:-$home_dir/Documents/assistant}"
bin_dir="${bin_dir:-$home_dir/Documents/scripts/bin}"
shell_rc="${shell_rc:-$home_dir/.bashrc}"
codex_home="${codex_home:-${CODEX_HOME:-$home_dir/.codex}}"
workspace_script="$bin_dir/tmux-workspace"
tw_link="$bin_dir/tw"
profiles_dir="$repo_root/profiles"

assistant_block_begin="# >>> assistant-tools >>>"
assistant_block_end="# <<< assistant-tools <<<"

log() {
  printf '%s\n' "$*"
}

write_file() {
  local path="$1"
  local mode="$2"
  local tmp

  if (( dry_run )); then
    log "Would write $path"
    return 0
  fi

  mkdir -p "$(dirname "$path")"
  tmp="$(mktemp "${path}.tmp.XXXXXX")"
  cat > "$tmp"
  chmod "$mode" "$tmp"
  mv "$tmp" "$path"
}

ensure_rc_block() {
  local rc_file="$1"
  local label="$2"
  local tmp

  if (( dry_run )); then
    log "Would update $label rc: $rc_file"
    return 0
  fi

  mkdir -p "$(dirname "$rc_file")"
  touch "$rc_file"
  tmp="$(mktemp "${rc_file}.tmp.XXXXXX")"

  awk -v begin="$assistant_block_begin" -v end="$assistant_block_end" '
    $0 == begin { skip = 1; next }
    $0 == end { skip = 0; next }
    skip != 1 { print }
  ' "$rc_file" > "$tmp"

  cat >> "$tmp" <<EOF

$assistant_block_begin
export PATH="$bin_dir:\$PATH"

assistant() {
  local use_codex=0

  if [[ "\${1:-}" == "-c" || "\${1:-}" == "--codex" ]]; then
    use_codex=1
    shift
  fi

  cd "$assistant_dir" || return

  if (( use_codex )); then
    codex --profile assistant "\$@"
  else
    claude --agent assistant "\$@"
  fi
}
$assistant_block_end
EOF

  mv "$tmp" "$rc_file"
}

maybe_ensure_system_rc_block() {
  local rc_file="$1"

  if (( update_system_shell_rc == 0 )); then
    return 0
  fi

  if (( dry_run )); then
    log "Would update system rc: $rc_file"
    return 0
  fi

  if [ -e "$rc_file" ] && [ ! -w "$rc_file" ]; then
    log "Warning: system rc is not writable: $rc_file"
    log "Re-run with sudo, or pass --system-shell-rc FILE for another global rc path."
    return 0
  fi

  if [ ! -e "$rc_file" ] && [ ! -w "$(dirname "$rc_file")" ]; then
    log "Warning: cannot create system rc: $rc_file"
    log "Re-run with sudo, or pass --system-shell-rc FILE for another global rc path."
    return 0
  fi

  ensure_rc_block "$rc_file" "system"
}

install_tmux_workspace() {
  write_file "$workspace_script" 0755 <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
prog="$(basename "$0")"

usage() {
  cat <<EOUSAGE
Usage:
  $prog [-c|--codex] [template] [session-name] [dir] [-- tmux-global-args...]

Default template:
  llm

Templates:
  llm       assistant on left, two terminals on right
  shell     simple 2x2 shell workspace
  raw       pass directly to tmux

Examples:
  $prog -c
  $prog -c paper
  $prog paper
  $prog paper ~/projects/paper
  $prog -- -L codex
  $prog paper -- -L codex

  $prog llm paper
  $prog shell scratch
  $prog raw -- list-sessions
  $prog raw -- attach -t paper
EOUSAGE
}

known_templates=("llm" "shell" "raw")

is_template() {
  local x="${1:-}"
  for t in "${known_templates[@]}"; do
    if [ "$x" = "$t" ]; then
      return 0
    fi
  done
  return 1
}

positional=()
tmux_args=()
assistant_command=(assistant)
session_suffix=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    -c|--codex)
      assistant_command=(assistant -c)
      session_suffix="-codex"
      shift
      ;;
    --)
      shift
      tmux_args=("$@")
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      positional+=("$1")
      shift
      ;;
  esac
done

tmux_do() {
  command tmux "${tmux_args[@]}" "$@"
}

if [ "${#positional[@]}" -gt 0 ] && is_template "${positional[0]}"; then
  template="${positional[0]}"
  positional=("${positional[@]:1}")
else
  template="llm"
fi

if [ "$template" = "raw" ]; then
  if [ "${#positional[@]}" -eq 0 ]; then
    tmux_do
  else
    tmux_do "${positional[@]}"
  fi
  exit 0
fi

if [ "$template" != "llm" ]; then
  session_suffix=""
fi

name="${positional[0]:-$(basename "$PWD")}"
dir="${positional[1]:-$PWD}"

dir="$(realpath "$dir")"

safe_name="$(printf '%s' "$name" | tr -cs 'A-Za-z0-9_.-' '-')"
safe_name="${safe_name%-}"
safe_name="${safe_name:-session}"
session="${safe_name}${session_suffix}"

attach_or_switch() {
  if [ -n "${TMUX:-}" ]; then
    tmux_do switch-client -t "$session"
  else
    tmux_do attach-session -t "$session"
  fi
}

if tmux_do has-session -t "$session" 2>/dev/null; then
  attach_or_switch
  exit 0
fi

case "$template" in
  llm)
    p_assistant="$(tmux_do new-session -d -P -F "#{pane_id}" -s "$session" -n assistant -c "$dir")"
    p_term1="$(tmux_do split-window -h -p 45 -P -F "#{pane_id}" -t "$p_assistant" -c "$dir")"
    p_term2="$(tmux_do split-window -v -p 50 -P -F "#{pane_id}" -t "$p_term1" -c "$dir")"

    tmux_do send-keys -t "$p_assistant" "${assistant_command[*]}" C-m
    tmux_do send-keys -t "$p_term1" "pwd" C-m
    tmux_do send-keys -t "$p_term2" "git status" C-m

    tmux_do select-pane -t "$p_assistant" -T "assistant"
    tmux_do select-pane -t "$p_term1" -T "terminal-1"
    tmux_do select-pane -t "$p_term2" -T "terminal-2"

    tmux_do new-window -t "$session:" -n scratch -c "$dir"
    tmux_do send-keys -t "$session:scratch" "pwd" C-m

    tmux_do new-window -t "$session:" -n logs -c "$dir"
    tmux_do send-keys -t "$session:logs" "echo 'Use this window for logs, tests, servers, watchers, etc.'" C-m

    tmux_do select-window -t "$session:assistant"
    tmux_do select-pane -t "$p_assistant"
    ;;

  shell)
    p1="$(tmux_do new-session -d -P -F "#{pane_id}" -s "$session" -n main -c "$dir")"
    p2="$(tmux_do split-window -h -p 50 -P -F "#{pane_id}" -t "$p1" -c "$dir")"
    p3="$(tmux_do split-window -v -p 50 -P -F "#{pane_id}" -t "$p1" -c "$dir")"
    p4="$(tmux_do split-window -v -p 50 -P -F "#{pane_id}" -t "$p2" -c "$dir")"

    tmux_do select-pane -t "$p1" -T "main"
    tmux_do select-pane -t "$p2" -T "shell"
    tmux_do select-pane -t "$p3" -T "scratch"
    tmux_do select-pane -t "$p4" -T "logs"
    tmux_do select-pane -t "$p1"
    ;;

  *)
    echo "Unknown template: $template"
    echo
    usage
    exit 1
    ;;
esac

attach_or_switch
EOF
}

install_tw_link() {
  if (( dry_run )); then
    log "Would link $tw_link -> $workspace_script"
    return 0
  fi

  mkdir -p "$bin_dir"
  ln -sfn "$workspace_script" "$tw_link"
}

install_profile_links() {
  local profile
  local linked_any=0

  if [ ! -d "$profiles_dir" ]; then
    log "Warning: profiles directory is missing: $profiles_dir"
    return 0
  fi

  mkdir -p "$codex_home"

  for profile in "$profiles_dir"/*.config.toml; do
    [ -e "$profile" ] || continue
    linked_any=1

    if (( dry_run )); then
      log "Would link $codex_home/$(basename "$profile") -> $profile"
    else
      ln -sfn "$profile" "$codex_home/$(basename "$profile")"
    fi
  done

  if (( linked_any == 0 )); then
    log "Warning: no profile files found in $profiles_dir"
  fi
}

warn_missing_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    log "Warning: '$name' is not currently on PATH."
  fi
}

install_tmux_workspace
install_tw_link
install_profile_links
ensure_rc_block "$shell_rc" "user"
maybe_ensure_system_rc_block "$system_shell_rc"

warn_missing_command tmux
warn_missing_command codex
warn_missing_command claude

log "Installed assistant tools."
log "User shell rc: $shell_rc"
if (( update_system_shell_rc )); then
  log "System shell rc: $system_shell_rc"
fi
log "Bin dir: $bin_dir"
log "Assistant dir: $assistant_dir"
log "Codex home: $codex_home"
log "Run 'source \"$shell_rc\"' or open a new shell before using assistant/tw."
