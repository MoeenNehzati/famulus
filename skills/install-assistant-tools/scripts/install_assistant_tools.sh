#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  install_assistant_tools.sh [options]

Installs or updates:
  - assistant script (symlinked from skill bin/)
  - collab script (symlinked from skill bin/)
  - tw/tmux-workspace script (symlinked from skill bin/)
  - Profile symlinks (profiles/*.config.toml -> Codex and Claude homes)
  - Git hook path for this repository (.githooks)
  - PATH entry and ASSISTANT_DEFAULT in the user (and optionally system) shell rc
  - PATH entry in the login shell profile (default: $HOME/.profile), so that
    login shells (e.g. bash -lc used by systemd jobs) can find the assistant
    command

Options:
  --home DIR             Home directory to install into (default: $HOME)
  --bin-dir DIR          Directory for installed symlinks
                         (default: $HOME/Documents/scripts/bin)
  --shell-rc FILE        Shell rc file to update (default: $HOME/.bashrc)
  --login-shell-rc FILE  Login shell profile to update (default: $HOME/.profile)
  --system-shell-rc FILE System bash rc file to update when writable
                         (default: /etc/bash.bashrc)
  --codex-home DIR       Codex state/config directory for profile symlinks
                         (default: $CODEX_HOME or $HOME/.codex)
  --claude-home DIR      Claude state/config directory for profile symlinks
                         (default: $CLAUDE_HOME or $HOME/.claude)
  --default-llm claude|codex
                         Default backend for assistant (prompted if omitted)
  --no-login-shell-rc    Do not update the login shell profile
  --no-system-shell-rc   Do not update the system bash rc file
  --dry-run              Print planned actions without writing files
  -h, --help             Show this help
EOF
}

home_dir="${HOME:-}"
bin_dir=""
shell_rc=""
login_shell_rc=""
system_shell_rc="/etc/bash.bashrc"
codex_home=""
claude_home=""
default_llm=""
update_login_shell_rc=0
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
    --login-shell-rc)
      login_shell_rc="${2:?--login-shell-rc requires a file}"
      shift 2
      ;;
    --no-login-shell-rc)
      update_login_shell_rc=0
      shift
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
    --claude-home)
      claude_home="${2:?--claude-home requires a directory}"
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
login_shell_rc="${login_shell_rc:-$home_dir/.profile}"
codex_home="${codex_home:-${CODEX_HOME:-$home_dir/.codex}}"
claude_home="${claude_home:-${CLAUDE_HOME:-$home_dir/.claude}}"
profiles_dir="$repo_root/profiles"
hooks_dir="$repo_root/.githooks"

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

  for script in assistant collab tmux-workspace; do
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

remove_legacy_coder_links() {
  local legacy_bin="$bin_dir/coder"
  local legacy_codex_profile="$codex_home/coder.config.toml"
  local legacy_claude_profile="$claude_home/coder.config.toml"

  for legacy in "$legacy_bin" "$legacy_codex_profile" "$legacy_claude_profile"; do
    if [ ! -L "$legacy" ]; then
      continue
    fi
    case "$(readlink "$legacy")" in
      "$source_bin_dir/coder"|"$profiles_dir/coder.config.toml")
        if (( dry_run )); then
          log "Would remove legacy link $legacy"
        else
          rm -f "$legacy"
        fi
        ;;
    esac
  done
}

install_profile_links() {
  local profile
  local linked_any=0

  if [ ! -d "$profiles_dir" ]; then
    log "Warning: profiles directory is missing: $profiles_dir"
    return 0
  fi

  mkdir -p "$codex_home" "$claude_home"

  for profile in "$profiles_dir"/*.config.toml; do
    [ -e "$profile" ] || continue
    linked_any=1
    if (( dry_run )); then
      log "Would link $codex_home/$(basename "$profile") -> $profile"
      log "Would link $claude_home/$(basename "$profile") -> $profile"
    else
      ln -sfn "$profile" "$codex_home/$(basename "$profile")"
      ln -sfn "$profile" "$claude_home/$(basename "$profile")"
    fi
  done

  if (( linked_any == 0 )); then
    log "Warning: no profile files found in $profiles_dir"
  fi
}

install_git_hooks() {
  if [ ! -d "$hooks_dir" ]; then
    echo "Missing git hooks directory: $hooks_dir" >&2
    exit 1
  fi

  while IFS= read -r -d '' hook_path; do
    if (( dry_run )); then
      log "Would chmod +x $hook_path"
    else
      chmod +x "$hook_path"
    fi
  done < <(find "$hooks_dir" -type f -print0)

  if (( dry_run )); then
    log "Would set git -C $repo_root config core.hooksPath .githooks"
  else
    git -C "$repo_root" config core.hooksPath .githooks
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

maybe_ensure_login_rc_block() {
  local rc_file="$1"

  if (( update_login_shell_rc == 0 )); then
    return 0
  fi

  if (( dry_run )); then
    log "Would update login shell profile: $rc_file"
    return 0
  fi

  ensure_rc_block "$rc_file" "login profile"
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

  for cmd in assistant collab tw; do
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

install_fzf() {
  if command -v fzf >/dev/null 2>&1; then
    log "fzf already installed: $(command -v fzf)"
    return 0
  fi

  log "fzf not found — installing to $bin_dir..."

  if (( dry_run )); then
    log "(dry-run) Would download and install fzf to $bin_dir"
    return 0
  fi

  local arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64)  arch="amd64" ;;
    aarch64) arch="arm64" ;;
    armv7l)  arch="armv7" ;;
    *)
      log "Warning: unsupported arch '$arch' for fzf auto-install; install manually."
      return 0
      ;;
  esac

  local version
  version="$(curl -fsSL https://api.github.com/repos/junegunn/fzf/releases/latest \
              | grep '"tag_name"' | head -1 | sed 's/.*"v\([^"]*\)".*/\1/')"

  if [ -z "$version" ]; then
    log "Warning: could not determine latest fzf version; install manually."
    return 0
  fi

  local url="https://github.com/junegunn/fzf/releases/download/v${version}/fzf-${version}-linux_${arch}.tar.gz"
  log "Downloading fzf v${version} (${arch})..."

  mkdir -p "$bin_dir"
  if curl -fsSL "$url" | tar -xz -C "$bin_dir" fzf; then
    log "  OK: fzf installed to $bin_dir/fzf"
  else
    log "Warning: fzf download failed; install manually."
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

write_runner_env() {
  # Write a repo-local env.sh into the recurring-tasks skill's scripts dir.
  # invoke-agent.sh sources this to get PATH without touching system profiles.
  local runner_scripts_dir="$repo_root/skills/recurring-tasks/scripts"
  local runner_env_file="$runner_scripts_dir/env.sh"

  if (( dry_run )); then
    log "Would write $runner_env_file"
    return 0
  fi

  if [ ! -d "$runner_scripts_dir" ]; then
    log "Warning: recurring-tasks scripts dir not found: $runner_scripts_dir — skipping env.sh"
    return 0
  fi

  cat > "$runner_env_file" <<EOF
# Generated by install_assistant_tools.sh — do not edit manually.
# Sourced by invoke-agent.sh to ensure assistant is on PATH in login-shell
# and systemd service contexts without requiring changes to system profiles.
export PATH="$bin_dir:\$PATH"
EOF
  log "Wrote runner env: $runner_env_file"
}

resolve_default_llm
install_fzf
install_bin_scripts
install_profile_links
install_git_hooks
remove_legacy_coder_links
install_ai_agent_env
write_runner_env
ensure_rc_block "$shell_rc" "user"
maybe_ensure_login_rc_block "$login_shell_rc"
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
log "  Claude home:    $claude_home"
log "  Git hooks:      $hooks_dir"
log "  Default LLM:    $default_llm"
log "  User shell rc:  $shell_rc"
if (( update_login_shell_rc )); then
  log "  Login profile:  $login_shell_rc"
fi
if (( update_system_shell_rc )); then
  log "  System rc:      $system_shell_rc"
fi
log ""
log "Run 'source \"$shell_rc\"' or open a new shell to apply PATH changes."
