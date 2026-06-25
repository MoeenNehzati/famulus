#!/usr/bin/env bash
# Tests for lists.sh — runs fully locally using a filesystem mock of cloud-files.
#
# The mock mirrors the directory layout lists.sh expects so that the relative
# path  ${script_dir}/../../cloud-files/scripts/cloud-files.sh  resolves to
# the mock instead of the real rclone-backed script.
#
# Usage:
#   bash tests/test_lists.sh          # from list-manager/ directory
#   ./tests/test_lists.sh             # if executable
set -euo pipefail

TESTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/.." && pwd)"
REAL_LISTS_SH="$SKILL_DIR/scripts/lists.sh"

# ── Isolated workspace ───────────────────────────────────────────────────────
# lists.sh computes:  script_dir = <workspace>/list-manager/scripts
#                     cloud_files = <workspace>/cloud-files/scripts/cloud-files.sh
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/list-manager/scripts"
mkdir -p "$WORK/cloud-files/scripts"
mkdir -p "$WORK/store/lists"

# Symlink the real script into the workspace so it resolves its own path correctly.
ln -s "$REAL_LISTS_SH" "$WORK/list-manager/scripts/lists.sh"
LISTS="$WORK/list-manager/scripts/lists.sh"

# Mock cloud-files.sh: read/write/delete/list against a local store directory.
# TEST_STORE_DIR is exported before each test group so the mock knows where to look.
cat > "$WORK/cloud-files/scripts/cloud-files.sh" << 'MOCK'
#!/usr/bin/env bash
set -euo pipefail
op="${1:-}"; relpath="${2:-}"
STORE="${TEST_STORE_DIR:?TEST_STORE_DIR not set}"
case "$op" in
  list)
    if [ -d "$STORE/$relpath" ]; then ls "$STORE/$relpath"; fi
    ;;
  read)
    if [ -f "$STORE/$relpath" ]; then cat "$STORE/$relpath"; fi
    ;;
  write)
    mkdir -p "$(dirname "$STORE/$relpath")"
    cat > "$STORE/$relpath"
    ;;
  delete)
    rm -f "$STORE/$relpath"
    ;;
  *) echo "mock: unknown op '$op'" >&2; exit 1 ;;
esac
MOCK
chmod +x "$WORK/cloud-files/scripts/cloud-files.sh"

export TEST_STORE_DIR="$WORK/store"

# ── Helpers ──────────────────────────────────────────────────────────────────

pass=0; fail=0

ok() {
  echo "  PASS  $1"
  pass=$((pass + 1))
}

fail() {
  echo "  FAIL  $1"
  printf '        expected: %s\n' "$2"
  printf '        actual:   %s\n' "$3"
  fail=$((fail + 1))
}

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  [ "$expected" = "$actual" ] && ok "$desc" || fail "$desc" "$expected" "$actual"
}

assert_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if printf '%s' "$haystack" | grep -qF "$needle"; then
    ok "$desc"
  else
    fail "$desc" "(contains) $needle" "$haystack"
  fi
}

assert_not_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if ! printf '%s' "$haystack" | grep -qF "$needle"; then
    ok "$desc"
  else
    fail "$desc" "(does NOT contain) $needle" "$haystack"
  fi
}

assert_exit_nonzero() {
  local desc="$1"; shift
  if ! "$@" >/dev/null 2>&1; then
    ok "$desc"
  else
    fail "$desc" "non-zero exit" "zero exit"
  fi
}

# Reset the store between test groups.
reset() {
  rm -rf "$TEST_STORE_DIR/lists"
  mkdir -p "$TEST_STORE_DIR/lists"
}

# Extract the 4-char hex id from a line containing <!-- #xxxx -->.
extract_id() {
  printf '%s' "$1" | grep -oE '<!-- #[0-9a-f]{4} -->' | head -1 | grep -oE '[0-9a-f]{4}'
}

# ── Test groups ──────────────────────────────────────────────────────────────

echo
echo "══ append: basic item ══"
reset
out=$("$LISTS" append groceries 2>&1 <<'EOF'
- [ ] (06/25/26) Buy milk
EOF
)
assert_contains "reports an id on stderr" "appended with id #" "$out"
content=$(cat "$TEST_STORE_DIR/lists/groceries.md")
assert_contains "title in file" "Buy milk" "$content"
assert_contains "id comment injected" "<!-- #" "$content"
# ID must appear on the same line as the title, not on a separate line
id_line=$(printf '%s\n' "$content" | grep '<!-- #')
assert_contains "id is on the checkbox line" "Buy milk" "$id_line"

echo
echo "══ append: continuation lines ==="
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Pick up prescription
  Pharmacy closes at 6pm.
  deadline: by Friday
EOF
content=$(cat "$TEST_STORE_DIR/lists/groceries.md")
assert_contains "title present" "Pick up prescription" "$content"
assert_contains "description present" "Pharmacy closes at 6pm." "$content"
assert_contains "deadline present" "deadline: by Friday" "$content"
# ID should be on the checkbox line only
id_line=$(printf '%s\n' "$content" | grep '<!-- #')
assert_contains     "id on checkbox line"        "Pick up prescription" "$id_line"
assert_not_contains "id not on description line" "Pharmacy"             "$id_line"
assert_not_contains "id not on deadline line"    "deadline"             "$id_line"

echo
echo "══ append: second append adds to existing list ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Eggs
EOF
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Bread
EOF
content=$(cat "$TEST_STORE_DIR/lists/groceries.md")
assert_contains "first item still present" "Eggs"  "$content"
assert_contains "second item added"        "Bread" "$content"

echo
echo "══ append: multiple items get unique ids ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Eggs
EOF
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Bread
EOF
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Butter
EOF
content=$(cat "$TEST_STORE_DIR/lists/groceries.md")
id_count=$(printf '%s\n' "$content" | grep -c '<!-- #' || true)
assert_eq "three items, each with one id" "3" "$id_count"
ids=$(printf '%s\n' "$content" | grep -oE '[0-9a-f]{4}' | sort)
unique_ids=$(printf '%s\n' "$ids" | sort -u)
assert_eq "all ids are distinct" "$ids" "$unique_ids"

echo
echo "══ unchecked: shows only [ ] items ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Eggs
EOF
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Bread
EOF
# Inject a checked item directly into the store.
printf '%s\n' "$(cat "$TEST_STORE_DIR/lists/groceries.md")"$'\n'"- [x] (06/24/26) Paid bill <!-- #aa00 -->" \
  > "$TEST_STORE_DIR/lists/groceries.md"
out=$("$LISTS" unchecked groceries)
assert_contains     "shows unchecked eggs"         "Eggs"      "$out"
assert_contains     "shows unchecked bread"         "Bread"     "$out"
assert_not_contains "does not show checked item"   "Paid bill" "$out"

echo
echo "══ unchecked: no unchecked items message ══"
reset
cat > "$TEST_STORE_DIR/lists/tasks.md" << 'EOF'
- [x] (06/25/26) Done thing <!-- #bb01 -->
EOF
out=$("$LISTS" unchecked tasks)
assert_eq "no unchecked items message" "(no unchecked items)" "$out"

echo
echo "══ toggle: check by id ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Eggs
EOF
content=$(cat "$TEST_STORE_DIR/lists/groceries.md")
id=$(extract_id "$content")
toggled=$("$LISTS" toggle groceries "$id" check)
assert_contains     "toggled output shows [x]"    "[x]" "$toggled"
assert_not_contains "toggled output has no [ ]"   "[ ]" "$toggled"
updated=$(cat "$TEST_STORE_DIR/lists/groceries.md")
assert_contains     "file persisted with [x]"     "[x] (06/25/26) Eggs" "$updated"

echo
echo "══ toggle: uncheck ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Eggs
EOF
id=$(extract_id "$(cat "$TEST_STORE_DIR/lists/groceries.md")")
"$LISTS" toggle groceries "$id" check   >/dev/null
toggled=$("$LISTS" toggle groceries "$id" uncheck)
assert_contains "uncheck output shows [ ]" "[ ]" "$toggled"
updated=$(cat "$TEST_STORE_DIR/lists/groceries.md")
assert_contains "file back to [ ]" "[ ] (06/25/26) Eggs" "$updated"

echo
echo "══ toggle: only the target item changes ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Eggs
EOF
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Bread
EOF
content=$(cat "$TEST_STORE_DIR/lists/groceries.md")
# Get the id of the Eggs line specifically
eggs_line=$(printf '%s\n' "$content" | grep "Eggs")
eggs_id=$(extract_id "$eggs_line")
"$LISTS" toggle groceries "$eggs_id" check >/dev/null
updated=$(cat "$TEST_STORE_DIR/lists/groceries.md")
assert_contains     "eggs is now [x]"       "[x] (06/25/26) Eggs"  "$updated"
assert_contains     "bread still unchecked" "[ ] (06/25/26) Bread" "$updated"

echo
echo "══ toggle: title with [brackets] does not confuse sed ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Fix [bug] in [parser]
EOF
id=$(extract_id "$(cat "$TEST_STORE_DIR/lists/groceries.md")")
toggled=$("$LISTS" toggle groceries "$id" check)
assert_contains "checkbox toggled to [x]"       "[x]"                   "$toggled"
assert_contains "title with brackets preserved" "Fix [bug] in [parser]" "$toggled"

echo
echo "══ toggle: [ ] in description does not confuse sed ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Review checklist
  Check items: [ ] a, [ ] b, [ ] c
EOF
id=$(extract_id "$(cat "$TEST_STORE_DIR/lists/groceries.md")")
"$LISTS" toggle groceries "$id" check >/dev/null
updated=$(cat "$TEST_STORE_DIR/lists/groceries.md")
assert_contains     "checkbox line is [x]"               "[x] (06/25/26) Review checklist" "$updated"
assert_contains     "continuation line brackets intact"  "[ ] a, [ ] b, [ ] c"             "$updated"

echo
echo "══ toggle: unknown id exits nonzero ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Eggs
EOF
assert_exit_nonzero "unknown id → nonzero exit" "$LISTS" toggle groceries "ffff" check

echo
echo "══ grep: finds item by text (case-insensitive) ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Almond milk
EOF
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Oat flour
EOF
out=$("$LISTS" grep groceries "almond")
assert_contains     "finds almond milk"          "Almond milk" "$out"
assert_not_contains "does not return oat flour"  "Oat flour"   "$out"

echo
echo "══ grep: [brackets] in search term treated as literals ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Fix [bug] in parser
EOF
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Buy bread
EOF
out=$("$LISTS" grep groceries "[bug]")
assert_contains     "finds item with literal bracket text" "Fix [bug]" "$out"
assert_not_contains "does not spuriously match others"     "Buy bread" "$out"

echo
echo "══ grep: returns line numbers ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Eggs
EOF
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Bread
EOF
out=$("$LISTS" grep groceries "Bread")
# grep -n prefixes each match with "N:"
assert_contains "output has line number prefix" "2:" "$out"

echo
echo "══ grep: no matches ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Eggs
EOF
out=$("$LISTS" grep groceries "zzznomatch")
assert_eq "no matches message" "(no matches)" "$out"

echo
echo "══ grep: searches description and deadline lines ══"
reset
"$LISTS" append tasks 2>/dev/null <<'EOF'
- [ ] (06/25/26) Send report
  Quarterly numbers for the board.
  deadline: by Friday
EOF
out=$("$LISTS" grep tasks "Quarterly")
assert_contains "finds text in description" "Quarterly" "$out"
out=$("$LISTS" grep tasks "by Friday")
assert_contains "finds text in deadline"    "by Friday" "$out"

echo
echo "══ write with empty stdin: deletes the list ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Eggs
EOF
"$LISTS" write groceries <<'EOF'
EOF
remaining=$(ls "$TEST_STORE_DIR/lists/" 2>/dev/null | tr -d '\n')
assert_eq "list file is gone" "" "$remaining"

echo
echo "══ migrate: adds ids to bare task lines ══"
reset
cat > "$TEST_STORE_DIR/lists/tasks.md" << 'EOF'
- [ ] (06/20/26) First task
  deadline: by Monday
- [x] (06/19/26) Done task
- [ ] (06/21/26) Task with [brackets] in title
EOF
out=$("$LISTS" migrate tasks)
assert_contains "reports count" "added IDs to 3 items" "$out"
content=$(cat "$TEST_STORE_DIR/lists/tasks.md")
id_count=$(printf '%s\n' "$content" | grep -c '<!-- #' || true)
assert_eq "all 3 task lines have ids" "3" "$id_count"
assert_contains "continuation line preserved" "deadline: by Monday" "$content"
assert_contains "bracket title preserved"     "[brackets]"          "$content"
ids=$(printf '%s\n' "$content" | grep -oE '[0-9a-f]{4}' | sort)
unique_ids=$(printf '%s\n' "$ids" | sort -u)
assert_eq "migrated ids are all unique" "$ids" "$unique_ids"

echo
echo "══ migrate: skips lines that already have ids ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Eggs
EOF
original_id=$(extract_id "$(cat "$TEST_STORE_DIR/lists/groceries.md")")
out=$("$LISTS" migrate groceries)
assert_contains "reports 0 new ids" "added IDs to 0 items" "$out"
after_id=$(extract_id "$(cat "$TEST_STORE_DIR/lists/groceries.md")")
assert_eq "existing id unchanged" "$original_id" "$after_id"

echo
echo "══ migrate: skips persistent title lines and continuation lines ══"
reset
cat > "$TEST_STORE_DIR/lists/tasks.md" << 'EOF'
- Area One
  - Action A
    - [ ] (06/25/26) Do the thing
      description here
EOF
"$LISTS" migrate tasks
content=$(cat "$TEST_STORE_DIR/lists/tasks.md")
id_count=$(printf '%s\n' "$content" | grep -c '<!-- #' || true)
assert_eq "only the task line gets an id" "1" "$id_count"
assert_not_contains "title line has no id"        "Area One <!-- #"       "$content"
assert_not_contains "continuation line has no id" "description here <!-- #" "$content"
assert_contains     "task line has id"            "Do the thing <!-- #"   "$content"

echo
echo "══ gen-id: returns a 4-char hex id ══"
reset
"$LISTS" append groceries 2>/dev/null <<'EOF'
- [ ] (06/25/26) Eggs
EOF
id=$("$LISTS" gen-id groceries)
assert_contains "id is 4 hex chars" "" "$(printf '%s' "$id" | grep -E '^[0-9a-f]{4}$' && echo ok)"
# gen-id must not duplicate an existing id
existing_id=$(extract_id "$(cat "$TEST_STORE_DIR/lists/groceries.md")")
# (collision extremely unlikely with 65536 possibilities; not tested explicitly)

echo
echo "══ gen-id: id is unique across a populated list ══"
reset
# Fill the list with 10 items and collect their ids
for title in A B C D E F G H I J; do
  "$LISTS" append groceries 2>/dev/null <<EOF
- [ ] (06/25/26) Item $title
EOF
done
content=$(cat "$TEST_STORE_DIR/lists/groceries.md")
new_id=$("$LISTS" gen-id groceries)
assert_not_contains "gen-id not already in file" "<!-- #${new_id} -->" "$content"

echo
echo "══ toggle works on migrated items ══"
reset
cat > "$TEST_STORE_DIR/lists/tasks.md" << 'EOF'
- [ ] (06/25/26) Old item without id
EOF
"$LISTS" migrate tasks
id=$(extract_id "$(cat "$TEST_STORE_DIR/lists/tasks.md")")
"$LISTS" toggle tasks "$id" check >/dev/null
updated=$(cat "$TEST_STORE_DIR/lists/tasks.md")
assert_contains "migrated item can be checked" "[x] (06/25/26) Old item" "$updated"

# ── Summary ──────────────────────────────────────────────────────────────────

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf '  %d passed  %d failed\n' "$pass" "$fail"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
[ "$fail" -eq 0 ]
