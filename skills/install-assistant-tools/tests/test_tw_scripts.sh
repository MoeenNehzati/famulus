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
