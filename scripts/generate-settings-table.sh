#!/usr/bin/env bash
set -euo pipefail

# Generate a markdown table comparing Claude and Codex settings for all profiles.
# Reads from profiles/*.config.toml (Codex) and profiles/*_claude_setting.json (Claude)
# Outputs to PROFILES.md

profiles_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../profiles" && pwd)"
output_file="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/PROFILES.md"

if [ ! -d "$profiles_dir" ]; then
  echo "Error: profiles directory not found: $profiles_dir" >&2
  exit 1
fi

# Helper: extract value from TOML file (match exact key with = sign)
toml_get() {
  local file="$1"
  local key="$2"
  grep "^$key = " "$file" 2>/dev/null | sed 's/.*= *"//' | sed 's/".*//' | head -1
}

# Helper: extract value from JSON file
json_get() {
  local file="$1"
  local key="$2"
  if command -v jq >/dev/null 2>&1; then
    jq -r ".$key // empty" "$file" 2>/dev/null
  else
    grep "\"$key\"" "$file" | sed 's/.*: *"//' | sed 's/".*//' | head -1
  fi
}

# Collect profile information
declare -A names descriptions codex_models codex_efforts claude_models claude_efforts claude_thinking

for toml_file in "$profiles_dir"/*.config.toml; do
  [ -e "$toml_file" ] || continue

  profile_name=$(basename "$toml_file" .config.toml)

  # Add to array in order
  names["$profile_name"]="$profile_name"

  # Codex settings
  codex_models["$profile_name"]=$(toml_get "$toml_file" "model")
  codex_efforts["$profile_name"]=$(toml_get "$toml_file" "model_reasoning_effort")
done

for json_file in "$profiles_dir"/*_claude_setting.json; do
  [ -e "$json_file" ] || continue

  basename_json=$(basename "$json_file" _claude_setting.json)
  profile_name="$basename_json"

  # Claude settings
  claude_models["$profile_name"]=$(json_get "$json_file" "model")
  claude_efforts["$profile_name"]=$(json_get "$json_file" "effortLevel")

  # Extract thinking tokens from env
  if command -v jq >/dev/null 2>&1; then
    claude_thinking["$profile_name"]=$(jq -r '.env.MAX_THINKING_TOKENS // "—"' "$json_file")
  else
    claude_thinking["$profile_name"]=$(grep "MAX_THINKING_TOKENS" "$json_file" | sed 's/.*": *"//' | sed 's/".*//' || echo "—")
  fi
done

# Add descriptions
descriptions["assistant"]="Secretary: fetch info, write, implement easy logic"
descriptions["collab"]="Serious coding, focused on documentation/learning"
descriptions["coauthor"]="Math/research mode, deep thinking and rigor"

# Generate markdown table
{
  echo "# Profiles & Settings"
  echo ""
  echo "Agent configurations for Claude and Codex, auto-generated from source config files."
  echo ""
  echo "## Overview"
  echo ""
  echo "| Profile | Purpose | Codex Model | Codex Effort | Claude Model | Claude Effort | Claude Thinking |"
  echo "|---------|---------|-------------|--------------|--------------|---------------|-----------------|"

  for profile_name in assistant collab coauthor; do
    codex_model="${codex_models[$profile_name]:-—}"
    codex_effort="${codex_efforts[$profile_name]:-—}"
    claude_model="${claude_models[$profile_name]:-—}"
    claude_effort="${claude_efforts[$profile_name]:-—}"
    claude_think="${claude_thinking[$profile_name]:-—}"
    description="${descriptions[$profile_name]:-—}"

    echo "| **$profile_name** | $description | $codex_model | $codex_effort | $claude_model | $claude_effort | $claude_think |"
  done

  echo ""
  echo "## Details"
  echo ""
  echo "Generated from:"
  echo "- Codex: \`profiles/*.config.toml\`"
  echo "- Claude: \`profiles/*_claude_setting.json\`"
  echo ""
  echo "**To update:** Run \`bash scripts/generate-settings-table.sh\` after modifying profile configs."
  echo ""
  echo "_Last generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')_"
} > "$output_file"

echo "✓ Generated $output_file"
