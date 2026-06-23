#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  install_assistant_tools.sh [options]

Installs or updates:
  - assistant script (symlinked from skill bin/)
  - tw/tmux-workspace script (symlinked from skill bin/)
  - Codex profile symlinks (profiles/*.config.toml -> codex home)
  - PATH entry and ASSISTANT_DEFAULT in the user (and optionally system) shell rc

Options:
  --home DIR             Home directory to install into (default: $HOME)
  --bin-dir DIR          Directory for installed symlinks
                         (default: $HOME/Documents/scripts/bin)
  --shell-rc FILE        Shell rc file to update (default: $HOME/.bashrc)
  --system-shell-rc FILE System bash rc file to update when writable
                         (default: /etc/bash.bashrc)
  --codex-home DIR       Codex state/config directory for profile symlinks
                         (default: $CODEX_HOME or $HOME/.codex)
  --default-llm claude|codex
                         Default backend for assistant (prompted if omitted)
  --no-system-shell-rc   Do not update the system bash rc file
  --dry-run              Print planned actions without writing files
  -h, --help             Show this help
EOF
}

home_dir="${HOME:-}"
bin_dir=""
shell_rc=""
system_shell_rc="/etc/bash.bashrc"
codex_home=""
default_llm=""
update_system_shell_rc=1
dry_run=0

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skill_dir="$(cd "$script_dir/.." && pwd)"
source_bin_dir="$skill_dir/bin"
repo_root="$(cd "$script_dir/../../.." && pwd)"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --home)
      home_dir="${2:?--home requires a directory}"
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
    --default-llm)
      default_llm="${2:?--default-llm requires claude or codex}"
      case "$default_llm" in
        claude|codex) ;;
        *) echo "--default-llm must be 'claude' or 'codex'" >&2; exit 2 ;;
      esac
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

bin_dir="${bin_dir:-$home_dir/Documents/scripts/bin}"
shell_rc="${shell_rc:-$home_dir/.bashrc}"
codex_home="${codex_home:-${CODEX_HOME:-$home_dir/.codex}}"
profiles_dir="$repo_root/profiles"

assistant_block_begin="# >>> assistant-tools >>>"
assistant_block_end="# <<< assistant-tools <<<"

log() {
  printf '%s\n' "$*"
}

resolve_default_llm() {
  if [ -n "$default_llm" ]; then
    return 0
  fi
  if (( dry_run )); then
    default_llm="claude"
    log "(dry-run) Would prompt for default LLM; using 'claude' as placeholder"
    return 0
  fi
  if [ -t 0 ]; then
    printf 'Default assistant backend [claude/codex] (default: claude): '
    local reply
    read -r reply
    case "${reply:-claude}" in
      claude|codex) default_llm="${reply:-claude}" ;;
      *) echo "Invalid choice '$reply'; defaulting to claude." >&2; default_llm="claude" ;;
    esac
  else
    default_llm="claude"
    log "Non-interactive mode: defaulting to 'claude'. Use --default-llm to override."
  fi
}

install_bin_scripts() {
  mkdir -p "$bin_dir"

  for script in assistant tmux-workspace; do
    local src="$source_bin_dir/$script"
    local dst="$bin_dir/$script"
    if [ ! -f "$src" ]; then
      log "Warning: source script not found: $src"
      continue
    fi
    if (( dry_run )); then
      log "Would link $dst -> $src"
    else
      ln -sfn "$src" "$dst"
    fi
  done

  # tw is an alias for tmux-workspace
  local tw_link="$bin_dir/tw"
  local tw_target="$source_bin_dir/tmux-workspace"
  if (( dry_run )); then
    log "Would link $tw_link -> $tw_target"
  else
    ln -sfn "$tw_target" "$tw_link"
  fi
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
    $0 == end   { skip = 0; next }
    skip != 1   { print }
  ' "$rc_file" > "$tmp"

  cat >> "$tmp" <<EOF

$assistant_block_begin
export PATH="$bin_dir:\$PATH"
export ASSISTANT_DEFAULT=$default_llm
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

verify_install() {
  if (( dry_run )); then
    return 0
  fi

  log ""
  log "Verifying installation..."
  local ok=1

  for cmd in assistant tw; do
    local dst="$bin_dir/$cmd"
    if [ ! -x "$dst" ]; then
      log "  FAIL: $dst is not executable"
      ok=0
      continue
    fi
    if "$dst" --help >/dev/null 2>&1; then
      log "  OK:   $dst --help"
    else
      log "  FAIL: $dst --help exited non-zero"
      ok=0
    fi
  done

  if (( ok == 0 )); then
    log "Warning: one or more verification checks failed."
    return 1
  fi
}

warn_missing_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    log "Warning: '$name' is not currently on PATH."
  fi
}

install_ai_agent_env() {
  local env_dir="$home_dir/.config/environment.d"
  local env_file="$env_dir/20-ai-agent.conf"
  local invoke_script="$home_dir/.claude/skills/recurring-tasks/scripts/invoke-agent.sh"

  if (( dry_run )); then
    log "Would write $env_file"
    return 0
  fi

  mkdir -p "$env_dir"
  printf 'AI_AGENT_COMMAND_TEMPLATE=%s {skill}\n' "$invoke_script" > "$env_file"

  # Also apply to the current systemd user session if systemctl is available
  if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active default.target >/dev/null 2>&1; then
    systemctl --user set-environment "AI_AGENT_COMMAND_TEMPLATE=$invoke_script {skill}" 2>/dev/null || true
  fi
}

resolve_default_llm
install_bin_scripts
install_profile_links
install_ai_agent_env
ensure_rc_block "$shell_rc" "user"
maybe_ensure_system_rc_block "$system_shell_rc"
verify_install

warn_missing_command tmux
warn_missing_command codex
warn_missing_command claude

log ""
log "Installed assistant tools."
log "  Bin dir:        $bin_dir"
log "  Source bin:     $source_bin_dir"
log "  Codex home:     $codex_home"
log "  Default LLM:    $default_llm"
log "  User shell rc:  $shell_rc"
if (( update_system_shell_rc )); then
  log "  System rc:      $system_shell_rc"
fi
log ""
log "Run 'source \"$shell_rc\"' or open a new shell to apply PATH changes."
