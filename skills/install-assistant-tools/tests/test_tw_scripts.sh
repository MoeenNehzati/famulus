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
# New entry format: saved-name|split|position|size|source-window (source-window last)
result=$(_tw_stack_push "" "tw-saved-3|H|before|30|@3")
assert_eq "push onto empty stack" "tw-saved-3|H|before|30|@3" "$result"

result=$(_tw_stack_push "tw-saved-3|H|before|30|@3" "tw-saved-5|V|after|50|@5")
assert_eq "push onto non-empty stack" "tw-saved-5|V|after|50|@5 tw-saved-3|H|before|30|@3" "$result"

# Entry format: saved-name|split|position|size|source-window-id (@N)
entry="tw-saved-3|H|before|33|@3"
saved_name=$(echo "$entry" | cut -d'|' -f1)
split=$(echo "$entry" | cut -d'|' -f2)
position=$(echo "$entry" | cut -d'|' -f3)
size=$(echo "$entry" | cut -d'|' -f4)
source_window=$(echo "$entry" | cut -d'|' -f5-)
assert_eq "entry parse: saved_name" "tw-saved-3" "$saved_name"
assert_eq "entry parse: split" "H" "$split"
assert_eq "entry parse: position" "before" "$position"
assert_eq "entry parse: size" "33" "$size"
assert_eq "entry parse: source_window_id (last field)" "@3" "$source_window"

# Tiny pane: size clamps to 1 (not 0)
result=$(_tw_compute_position 0 0 1 24 120 24)
assert_eq "H-split tiny pane clamps to 1%" "H|before|1" "$result"

# NOTE: the sole-pane guard (pane fills entire window → exit 0) lives in _tw_main
# which requires a live tmux session. It is verified manually.

# ── tw-join: stack pop ────────────────────────────────────────────────────────
# tw-join may not exist yet when this test is first created — skip gracefully
_TW_JOIN_AVAILABLE=0
if [ -f "$BIN_DIR/tw-join" ]; then
    source "$BIN_DIR/tw-join"
    _TW_JOIN_AVAILABLE=1
fi

if [ "$_TW_JOIN_AVAILABLE" -eq 1 ]; then
    _tw_stack_top "tw-saved-5|V|after|50|@5 tw-saved-3|H|before|30|@3"
    assert_eq "stack top entry" "tw-saved-5|V|after|50|@5" "$TW_TOP"
    assert_eq "stack rest after pop" "tw-saved-3|H|before|30|@3" "$TW_REST"

    _tw_stack_top "tw-saved-3|H|before|30|@3"
    assert_eq "stack top single entry" "tw-saved-3|H|before|30|@3" "$TW_TOP"
    assert_eq "stack rest single entry (empty)" "" "$TW_REST"

    # ── tw-join: join flags from entry ─────────────────────────────────────────
    result=$(_tw_join_flags "tw-saved-3|H|before|30|@3")
    assert_eq "H before → -h -b -l 30%" "-h -b -l 30%" "$result"

    result=$(_tw_join_flags "tw-saved-5|H|after|65|@5")
    assert_eq "H after → -h -l 65%" "-h -l 65%" "$result"

    result=$(_tw_join_flags "tw-saved-7|V|before|33|@7")
    assert_eq "V before → -b -l 33%" "-b -l 33%" "$result"

    result=$(_tw_join_flags "tw-saved-9|V|after|50|@9")
    assert_eq "V after → -l 50%" "-l 50%" "$result"
else
    echo "SKIP: tw-join tests (not yet created)"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
