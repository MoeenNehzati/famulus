#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$repo_root/tests/test_skill_metadata.py"
python3 "$repo_root/tests/test_platform_neutral_content.py"

plugin_name="$(
  python3 - "$repo_root/.codex-plugin/plugin.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["name"])
PY
)"
marketplace_name="${plugin_name}-local-test"

tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/${plugin_name}-codex-install.XXXXXX")"
cleanup() {
  rm -rf "$tmp_root"
}
trap cleanup EXIT

marketplace_root="$tmp_root/marketplace"
codex_home="$tmp_root/codex-home"
tmp_home="$tmp_root/home"
workdir="$tmp_root/work"

mkdir -p "$marketplace_root/.agents/plugins" "$marketplace_root/plugins" "$codex_home" "$tmp_home" "$workdir"
ln -s "$repo_root" "$marketplace_root/plugins/$plugin_name"

python3 - "$marketplace_root/.agents/plugins/marketplace.json" "$plugin_name" "$marketplace_name" <<'PY'
import json
import sys

marketplace_path, plugin_name, marketplace_name = sys.argv[1:]
payload = {
    "name": marketplace_name,
    "interface": {"displayName": marketplace_name},
    "plugins": [
        {
            "name": plugin_name,
            "source": {"source": "local", "path": f"./plugins/{plugin_name}"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity",
        }
    ],
}
with open(marketplace_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\n")
PY

expected_json="$tmp_root/expected-skills.json"
python3 - "$repo_root" "$expected_json" <<'PY'
import json
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
out_path = Path(sys.argv[2])

expected = sorted(
    skill_dir.name
    for skill_dir in (repo_root / "skills").iterdir()
    if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file()
)

out_path.write_text(json.dumps(expected), encoding="utf-8")
PY

baseline_prompt_json="$tmp_root/baseline-prompt-input.json"
(
  cd "$workdir"
  HOME="$tmp_home" CODEX_HOME="$codex_home" codex debug prompt-input "List available skills." >"$baseline_prompt_json"
)

python3 - "$expected_json" "$baseline_prompt_json" "$plugin_name" <<'PY'
import json
import sys
from pathlib import Path

expected = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
with Path(sys.argv[2]).open(encoding="utf-8") as handle:
    prompt = json.load(handle)
plugin_name = sys.argv[3]

visible_text = json.dumps(prompt)
leaked = [
    name
    for name in expected
    if f"{plugin_name}:{name}" in visible_text or f"- {name}:" in visible_text
]

if leaked:
    print("Repo skills are visible before installing the plugin:")
    for name in leaked:
        print(f"- {name}")
    raise SystemExit(1)
PY

HOME="$tmp_home" CODEX_HOME="$codex_home" codex plugin marketplace add "$marketplace_root" --json >/dev/null
install_json="$tmp_root/install.json"
HOME="$tmp_home" CODEX_HOME="$codex_home" codex plugin add "$plugin_name@$marketplace_name" --json >"$install_json"

installed_path="$(
  python3 - "$install_json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["installedPath"])
PY
)"

test -d "$installed_path/skills"
test -d "$installed_path/references"

python3 - "$expected_json" "$installed_path" <<'PY'
import json
import sys
from pathlib import Path

expected = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
installed_path = Path(sys.argv[2])

missing = [
    name
    for name in expected
    if not (installed_path / "skills" / name / "SKILL.md").is_file()
]

if missing:
    print("Missing installed skill files:")
    for name in missing:
        print(f"- skills/{name}/SKILL.md")
    raise SystemExit(1)
PY

while IFS= read -r skill_name; do
  prompt_json="$tmp_root/prompt-${skill_name}.json"
  (
    cd "$workdir"
    HOME="$tmp_home" CODEX_HOME="$codex_home" codex debug prompt-input "Use \$$plugin_name:$skill_name." >"$prompt_json"
  )
  python3 - "$prompt_json" "$plugin_name" "$skill_name" <<'PY'
import json
import sys
from pathlib import Path

prompt_path = Path(sys.argv[1])
plugin_name = sys.argv[2]
skill_name = sys.argv[3]

with prompt_path.open(encoding="utf-8") as handle:
    prompt = json.load(handle)

visible_text = json.dumps(prompt)
qualified = f"{plugin_name}:{skill_name}"
if qualified not in visible_text:
    print(f"Skill is not visible when explicitly invoked: {qualified}")
    raise SystemExit(1)
PY
done < <(python3 - "$expected_json" <<'PY'
import json
import sys
for skill in json.loads(open(sys.argv[1], encoding="utf-8").read()):
    print(skill)
PY
)

python3 - "$expected_json" "$plugin_name" <<'PY'
import json
import sys

expected = json.loads(open(sys.argv[1], encoding="utf-8").read())
plugin_name = sys.argv[2]
print(f"Codex install exposes {len(expected)} explicitly invokable skills from {plugin_name}.")
PY
