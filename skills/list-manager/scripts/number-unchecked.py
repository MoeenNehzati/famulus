#!/usr/bin/env python3
"""
Read a Markdown checklist from stdin, filter to unchecked [ ] task lines,
add hierarchical numbers (1, 1.1, 2, 2.1, ...), strip <!-- #id --> comments,
and print the result.

Checked items and all their children are excluded.
Continuation lines (description / deadline) of unchecked tasks are included.
Structural title lines (area / action headers, no checkbox) are excluded.
"""
import sys
import re

ID_RE = re.compile(r'\s*<!-- #[0-9a-f]+ -->')
TASK_RE = re.compile(r'^(\s*)- \[(.)\](.*)')


def main() -> None:
    lines = sys.stdin.read().splitlines()

    # Find the minimum indent among all task lines to use as depth-0 baseline.
    min_indent: int | None = None
    for line in lines:
        m = TASK_RE.match(line)
        if m:
            ind = len(m.group(1))
            if min_indent is None or ind < min_indent:
                min_indent = ind

    if min_indent is None:
        print("(no unchecked items)")
        return

    # counters[indent] = current sequential count at this indent level
    counters: dict[int, int] = {}
    # Indent of a checked item whose children we're skipping (-1 = not skipping)
    skip_above: int = -1
    # Indent of the last unchecked task line printed (for continuation-line detection)
    last_unchecked_indent: int = -1
    found_any = False

    for line in lines:
        m = TASK_RE.match(line)
        if m:
            indent = len(m.group(1))
            state = m.group(2)
            rest = m.group(3)

            # Leaving skip zone when we return to the same or shallower indent.
            if skip_above != -1 and indent <= skip_above:
                skip_above = -1

            if skip_above != -1:
                continue  # child of a checked item

            if state != ' ':
                # Checked (or other non-unchecked) item — skip it and its children.
                skip_above = indent
                last_unchecked_indent = -1
                continue

            # Reset counters for any indent levels deeper than this one
            # (we've come back up from a nested block).
            for k in list(counters.keys()):
                if k > indent:
                    del counters[k]

            counters[indent] = counters.get(indent, 0) + 1

            # Build hierarchical number from min_indent up to this indent.
            levels = sorted(k for k in counters if min_indent <= k <= indent)
            num_str = '.'.join(str(counters[k]) for k in levels)

            # Strip ID comment and clean up rest of line.
            rest_clean = ID_RE.sub('', rest)

            # Emit with relative indentation (2 spaces per nesting level).
            depth = (indent - min_indent) // 2
            spaces = '  ' * depth
            print(f'{spaces}{num_str}. [{state}]{rest_clean}')

            last_unchecked_indent = indent
            found_any = True

        else:
            # Non-task line.
            if skip_above != -1:
                continue  # inside a skipped (checked) block

            if last_unchecked_indent == -1:
                continue  # not inside an unchecked task block

            stripped = line.lstrip()
            if not stripped:
                # Blank line — reset continuation tracking.
                last_unchecked_indent = -1
                continue

            cur_indent = len(line) - len(stripped)
            if cur_indent > last_unchecked_indent:
                # Continuation line of the last unchecked task.
                clean = ID_RE.sub('', line)
                depth = (last_unchecked_indent - min_indent) // 2
                # Continuation lines get one extra indent level.
                cont_spaces = '  ' * (depth + 1)
                print(f'{cont_spaces}{stripped.rstrip()}')
            else:
                # Back at or above task indent — structural title line, stop continuations.
                last_unchecked_indent = -1

    if not found_any:
        print("(no unchecked items)")


if __name__ == '__main__':
    main()
