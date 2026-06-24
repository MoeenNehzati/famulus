# tw Logging and Pane Management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add btop logging window, scratch popup (prefix+e), smart break/join with position stack (prefix+b/j), raw join shortcut (prefix+@), and monitor panel toggle (prefix+m) to the tmux-workspace `llm` template.

**Architecture:** Three new companion scripts (`tw-break`, `tw-join`, `tw-monitor`) live in `bin/` alongside `tmux-workspace`. At session creation, `tmux-workspace` resolves its own path and passes absolute script paths to `bind-key run-shell`. Pane position metadata is stored in the tmux session environment variable `TW_PANE_STACK` as a space-separated list of pipe-delimited entries.

**Tech Stack:** bash, tmux (≥3.0)

---

## File Structure

```
bin/tmux-workspace          modify: logs→btop; add bind-key for e, @, b, j, m
bin/tw-break                new: save position, break pane to window
bin/tw-join                 new: pop stack, restore pane to source window
bin/tw-monitor              new: toggle slim btop pane on right of current window
tests/test_tw_scripts.sh    new: unit tests for stack and position logic
```

---

## Task 1: Update logs window and add simple bindings

**Files:**
- Modify: `bin/tmux-workspace`

- [ ] **Step 1: Change logs window to launch btop**

In `bin/tmux-workspace`, find the `logs` window section (around line 147) and replace:

```bash
    tmux_do new-window -t "$session:" -n logs -c "$dir"
    tmux_do send-keys -t "$session:logs" "echo 'Use this window for logs, tests, servers, watchers, etc.'" C-m
```

with:

```bash
    tmux_do new-window -t "$session:" -n logs -c "$dir"
    tmux_do send-keys -t "$session:logs" "btop" C-m
```

- [ ] **Step 2: Resolve the companion script directory**

Add this block immediately after the `tmux_do set-option` calls for `status` (around line 128, before the `case "$template"` statement):

```bash
# Resolve path to companion scripts (works through symlinks).
_tw_bin="$(dirname "$(readlink -f "$0")")"
```

- [ ] **Step 3: Add scratch popup binding (prefix+e)**

After the `_tw_bin` line, add:

```bash
# prefix+e — scratch popup (empty shell, stateless)
tmux_do bind-key e display-popup -E -w 80% -h 50% -x C -y C
```

- [ ] **Step 4: Add raw join binding (prefix+@)**

```bash
# prefix+@ — raw join: pull last active window into current as h-split
tmux_do bind-key '@' join-pane -s '!' -h
```

- [ ] **Step 5: Verify the session creates correctly**

```bash
# Kill any existing test session first
tmux kill-session -t tw-test 2>/dev/null || true
# Create a test session
tw tw-test /tmp
```

Expected: session opens, `assistant` window is active, `logs` window shows btop running. Switch to `logs` window with `prefix+2` (or your window navigation) to verify.

- [ ] **Step 6: Test prefix+e**

Inside the new session, press `prefix+e`. Expected: a floating shell popup appears centered on screen. Type `exit`, popup closes. Layout unchanged.

- [ ] **Step 7: Test prefix+@**

Create a second window (`prefix+c`), then press `prefix+@`. Expected: the previously active window is pulled in as a right-side pane.

- [ ] **Step 8: Commit**

```bash
git add bin/tmux-workspace
git commit -m "feat(tw): launch btop in logs window, add prefix+e popup and prefix+@ raw join"
```

---

## Task 2: Implement tw-break

**Files:**
- Create: `bin/tw-break`
- Create: `tests/test_tw_scripts.sh`

The script detects the current pane's position within its window and pushes a stack entry to `TW_PANE_STACK` before breaking the pane out to its own window with `break-pane -d`.

**Stack entry format:** `<saved-window-name>|<source-window-name>|<H|V>|<before|after>|<size%>`

- `H` = horizontal split (pane is a column); `V` = vertical split (pane is a row)
- `before` = pane was left-of or above other panes; `after` = right-of or below
- `size%` = percentage of window width (H) or height (V) the pane occupied

- [ ] **Step 1: Write the failing test**

Create `tests/test_tw_scripts.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
PASS=0; FAIL=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$(dirname "$SCRIPT_DIR")/bin"

assert_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        echo "PASS: $desc"; ((PASS++)) || true
    else
        echo "FAIL: $desc"
        echo "  expected: $(printf %q "$expected")"
        echo "  actual:   $(printf %q "$actual")"
        ((FAIL++)) || true
    fi
}

# ── tw-break: position detection ─────────────────────────────────────────────
# Source scripts if available (does not run main — guarded by BASH_SOURCE check)
source "$BIN_DIR/tw-break"

# Left pane in H split: pane_left=0, narrow, full height
result=$(_tw_compute_position 0 0 40 24 120 24)
assert_eq "H-split left pane → H|before|33" "H|before|33" "$result"

# Right pane in H split: pane_left=41, rest of width, full height
result=$(_tw_compute_position 41 0 79 24 120 24)
assert_eq "H-split right pane → H|after|65" "H|after|65" "$result"

# Top pane in V split: full width, pane_top=0
result=$(_tw_compute_position 0 0 120 8 120 24)
assert_eq "V-split top pane → V|before|33" "V|before|33" "$result"

# Bottom pane in V split: full width, pane_top=9
result=$(_tw_compute_position 0 9 120 15 120 24)
assert_eq "V-split bottom pane → V|after|62" "V|after|62" "$result"

# ── tw-break: stack push ──────────────────────────────────────────────────────
result=$(_tw_stack_push "" "win-a|src|H|before|30")
assert_eq "push onto empty stack" "win-a|src|H|before|30" "$result"

result=$(_tw_stack_push "win-a|src|H|before|30" "win-b|src2|V|after|50")
assert_eq "push onto non-empty stack" "win-b|src2|V|after|50 win-a|src|H|before|30" "$result"

# ── tw-join: stack pop ────────────────────────────────────────────────────────
# tw-join may not exist yet when this test is first created — skip gracefully
_TW_JOIN_AVAILABLE=0
if [ -f "$BIN_DIR/tw-join" ]; then
    source "$BIN_DIR/tw-join"
    _TW_JOIN_AVAILABLE=1
fi

if [ "$_TW_JOIN_AVAILABLE" -eq 1 ]; then
    _tw_stack_top "win-b|src2|V|after|50 win-a|src|H|before|30"
    assert_eq "stack top entry" "win-b|src2|V|after|50" "$TW_TOP"
    assert_eq "stack rest after pop" "win-a|src|H|before|30" "$TW_REST"

    _tw_stack_top "win-a|src|H|before|30"
    assert_eq "stack top single entry" "win-a|src|H|before|30" "$TW_TOP"
    assert_eq "stack rest single entry (empty)" "" "$TW_REST"

    # ── tw-join: join flags from entry ─────────────────────────────────────────
    result=$(_tw_join_flags "win-a|src|H|before|30")
    assert_eq "H before → -h -b -l 30%" "-h -b -l 30%" "$result"

    result=$(_tw_join_flags "win-b|src|H|after|65")
    assert_eq "H after → -h -l 65%" "-h -l 65%" "$result"

    result=$(_tw_join_flags "win-c|src|V|before|33")
    assert_eq "V before → -b -l 33%" "-b -l 33%" "$result"

    result=$(_tw_join_flags "win-d|src|V|after|50")
    assert_eq "V after → -l 50%" "-l 50%" "$result"
else
    echo "SKIP: tw-join tests (not yet created)"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
bash tests/test_tw_scripts.sh
```

Expected: errors — `tw-break` and `tw-join` do not exist yet.

- [ ] **Step 3: Create tw-break**

Create `bin/tw-break`:

```bash
#!/usr/bin/env bash
# tw-break: save current pane position to TW_PANE_STACK, then break it out.
# Called via: tmux bind-key b run-shell "/path/to/tw-break"
set -euo pipefail

# _tw_compute_position pane_left pane_top pane_width pane_height win_width win_height
# Outputs: "H|before|<size%>" or "H|after|<size%>" or "V|before|..." or "V|after|..."
_tw_compute_position() {
    local pane_left="$1" pane_top="$2" pane_width="$3" pane_height="$4"
    local win_width="$5" win_height="$6"
    local split position size

    if [ "$pane_width" -lt "$win_width" ]; then
        split="H"
        size=$(( pane_width * 100 / win_width ))
        if [ "$pane_left" -eq 0 ]; then position="before"; else position="after"; fi
    else
        split="V"
        size=$(( pane_height * 100 / win_height ))
        if [ "$pane_top" -eq 0 ]; then position="before"; else position="after"; fi
    fi
    echo "${split}|${position}|${size}"
}

# _tw_stack_push current_stack new_entry → new_stack string
_tw_stack_push() {
    local stack="$1" entry="$2"
    if [ -z "$stack" ]; then echo "$entry"; else echo "$entry $stack"; fi
}

_tw_main() {
    local window_name pane_left pane_top pane_width pane_height win_width win_height
    window_name=$(tmux display-message -p "#{window_name}")
    pane_left=$(tmux display-message -p "#{pane_left}")
    pane_top=$(tmux display-message -p "#{pane_top}")
    pane_width=$(tmux display-message -p "#{pane_width}")
    pane_height=$(tmux display-message -p "#{pane_height}")
    win_width=$(tmux display-message -p "#{window_width}")
    win_height=$(tmux display-message -p "#{window_height}")

    local pos
    pos=$(_tw_compute_position "$pane_left" "$pane_top" "$pane_width" "$pane_height" \
                                "$win_width" "$win_height")

    # Generate unique saved window name
    local saved_name="tw-saved-$$"

    # Encode entry: saved-window|source-window|split|position|size
    local entry="${saved_name}|${window_name}|${pos}"

    # Push onto session stack
    local current_stack
    local raw
    raw=$(tmux show-environment TW_PANE_STACK 2>/dev/null || echo "-TW_PANE_STACK")
    if [[ "$raw" == -* ]]; then current_stack=""; else current_stack="${raw#TW_PANE_STACK=}"; fi

    local new_stack
    new_stack=$(_tw_stack_push "$current_stack" "$entry")
    tmux set-environment TW_PANE_STACK "$new_stack"

    # Break pane out without following
    tmux break-pane -d -n "$saved_name"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    _tw_main "$@"
fi
```

- [ ] **Step 4: Run tests for tw-break portions**

```bash
bash tests/test_tw_scripts.sh 2>&1 | grep -E "^(PASS|FAIL): (H-split|V-split|push)"
```

Expected: all tw-break tests PASS, tw-join tests still fail (not created yet).

- [ ] **Step 5: Commit tw-break**

```bash
git add bin/tw-break tests/test_tw_scripts.sh
git commit -m "feat(tw): add tw-break script with position detection and stack push"
```

---

## Task 3: Implement tw-join

**Files:**
- Create: `bin/tw-join`

- [ ] **Step 1: Create tw-join**

Create `bin/tw-join`:

```bash
#!/usr/bin/env bash
# tw-join: pop TW_PANE_STACK and restore the pane to its source window.
# Called via: tmux bind-key j run-shell "/path/to/tw-join"
set -euo pipefail

# _tw_stack_top stack_string → sets TW_TOP and TW_REST
_tw_stack_top() {
    local stack="$1"
    TW_TOP="${stack%% *}"
    if [ "$TW_TOP" = "$stack" ]; then TW_REST=""; else TW_REST="${stack#* }"; fi
}

# _tw_join_flags entry → join-pane flag string
_tw_join_flags() {
    local entry="$1"
    local split position size flags=""
    split=$(echo "$entry" | cut -d'|' -f3)
    position=$(echo "$entry" | cut -d'|' -f4)
    size=$(echo "$entry" | cut -d'|' -f5)

    if [ "$split" = "H" ]; then flags="-h"; fi
    if [ "$position" = "before" ]; then flags="$flags -b"; fi
    flags="$flags -l ${size}%"
    # Trim leading space
    echo "${flags# }"
}

_tw_main() {
    # Read stack
    local raw
    raw=$(tmux show-environment TW_PANE_STACK 2>/dev/null || echo "-TW_PANE_STACK")
    local stack
    if [[ "$raw" == -* ]]; then stack=""; else stack="${raw#TW_PANE_STACK=}"; fi

    if [ -z "$stack" ]; then
        tmux display-message "tw: nothing to restore"
        exit 0
    fi

    # Pop entries until one succeeds or stack is exhausted
    local TW_TOP TW_REST
    while [ -n "$stack" ]; do
        _tw_stack_top "$stack"
        local saved_name source_window
        saved_name=$(echo "$TW_TOP" | cut -d'|' -f1)
        source_window=$(echo "$TW_TOP" | cut -d'|' -f2)
        stack="$TW_REST"

        # Check saved window exists
        if ! tmux list-windows -F "#{window_name}" | grep -Fxq "$saved_name"; then
            tmux display-message "tw: window ${saved_name} gone, skipping"
            continue
        fi

        # Build and run join-pane
        local flags
        flags=$(_tw_join_flags "$TW_TOP")

        # Determine target: source window if it exists, else current window
        local target
        if tmux list-windows -F "#{window_name}" | grep -Fxq "$source_window"; then
            target=":${source_window}"
        else
            target=""
        fi

        # shellcheck disable=SC2086
        if [ -n "$target" ]; then
            tmux join-pane $flags -s ":${saved_name}" -t "$target" 2>/dev/null && break
        else
            tmux join-pane $flags -s ":${saved_name}" 2>/dev/null && break
        fi

        tmux display-message "tw: could not restore ${saved_name}, skipping"
    done

    # Write updated stack back
    tmux set-environment TW_PANE_STACK "$stack"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    _tw_main "$@"
fi
```

- [ ] **Step 2: Run the full test suite**

```bash
bash tests/test_tw_scripts.sh
```

Expected: all tests PASS, output ends with `N passed, 0 failed`.

- [ ] **Step 3: Commit tw-join**

```bash
git add bin/tw-join tests/test_tw_scripts.sh
git commit -m "feat(tw): add tw-join script with stack pop and position restore"
```

---

## Task 4: Implement tw-monitor

**Files:**
- Create: `bin/tw-monitor`

- [ ] **Step 1: Create tw-monitor**

Create `bin/tw-monitor`:

```bash
#!/usr/bin/env bash
# tw-monitor: toggle a slim btop pane on the right of the current window.
# Called via: tmux bind-key m run-shell "/path/to/tw-monitor"
set -euo pipefail

_tw_main() {
    # Find a pane titled "tw-monitor" in the current window
    local monitor_pane
    monitor_pane=$(tmux list-panes -F "#{pane_id}|#{pane_title}" \
                   | awk -F'|' '$2 == "tw-monitor" {print $1}')

    if [ -n "$monitor_pane" ]; then
        # Panel is visible — remove it
        tmux kill-pane -t "$monitor_pane"
    else
        # Panel is not visible — add slim btop on the right
        local new_pane
        if ! new_pane=$(tmux split-window -h -l 25% -P -F "#{pane_id}" "btop" 2>&1); then
            tmux display-message "tw: terminal too narrow for monitor panel"
            exit 0
        fi
        tmux select-pane -t "$new_pane" -T "tw-monitor"
        # Return focus to the pane that was active before the split
        tmux select-pane -l
    fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    _tw_main "$@"
fi
```

- [ ] **Step 2: Make scripts executable**

```bash
chmod +x bin/tw-break bin/tw-join bin/tw-monitor
```

- [ ] **Step 3: Test monitor toggle manually**

Inside a live `tw` session, press `prefix+m` (once bindings are wired in Task 5). Expected: slim btop pane appears on the right (~25% width). Press `prefix+m` again. Expected: pane is removed, layout restored.

- [ ] **Step 4: Commit tw-monitor**

```bash
git add bin/tw-monitor
git commit -m "feat(tw): add tw-monitor script for slim btop panel toggle"
```

---

## Task 5: Wire up b, j, m bindings in tmux-workspace

**Files:**
- Modify: `bin/tmux-workspace`

- [ ] **Step 1: Add b, j, m bind-key calls**

After the existing `prefix+e` and `prefix+@` bind-key lines added in Task 1, add:

```bash
# prefix+b — smart break-and-stay (saves position to stack)
tmux_do bind-key b run-shell "$_tw_bin/tw-break"

# prefix+j — smart join (pops stack, restores position)
tmux_do bind-key j run-shell "$_tw_bin/tw-join"

# prefix+m — monitor toggle (slim btop panel on right)
tmux_do bind-key m run-shell "$_tw_bin/tw-monitor"
```

- [ ] **Step 2: Update the usage string**

In the `usage()` function at the top of `bin/tmux-workspace`, append to the help text:

```bash
Key bindings (set on each new session):
  prefix+e   scratch popup (empty shell, stateless)
  prefix+b   break current pane to its own window, save position
  prefix+j   restore last broken pane back (stack, LIFO)
  prefix+@   raw join: pull last active window into current as h-split
  prefix+m   toggle slim btop monitor panel on the right
  prefix+!   (tmux default) break-and-follow, no stack — use prefix+@ to bring back
```

- [ ] **Step 3: Smoke test the full session**

```bash
tmux kill-session -t tw-smoke 2>/dev/null || true
tw tw-smoke /tmp
```

Verify each binding works:
- `prefix+e` → popup appears, `exit` closes it
- `prefix+b` on any pane → pane disappears to its own window, you stay put
- `prefix+j` → pane comes back roughly where it was
- `prefix+@` → last window pulled in as right-side split
- `prefix+m` → slim btop panel added on right; again → removed

- [ ] **Step 4: Run test suite one final time**

```bash
bash tests/test_tw_scripts.sh
```

Expected: all tests pass.

- [ ] **Step 5: Final commit**

```bash
git add bin/tmux-workspace tests/test_tw_scripts.sh
git commit -m "feat(tw): wire up prefix+b/j/m bindings and update usage docs"
```
