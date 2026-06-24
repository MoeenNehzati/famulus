# tw: Logging and Pane Management — Design

Date: 2026-06-24

## Overview

Improve the `tw`/`tmux-workspace` script with better logging (btop in the `logs`
window), a scratch popup, and a suite of pane management bindings for popping
panes in and out of windows. Changes live in `bin/tmux-workspace` and three new
companion scripts in `bin/`.

---

## Changes to `llm` template

The `logs` window currently echoes a placeholder message. It will instead launch
`btop` automatically on session creation.

---

## New key bindings

All bindings are set globally (`bind-key -n` is NOT used — prefix is required)
when `tw` creates a session. They apply for the lifetime of that tmux session.

### New bindings added by tw

| Binding | Script | Action |
|---|---|---|
| `prefix + e` | — | Scratch popup: floating empty shell, 80×50% centered. Dismiss with `exit` or `Ctrl-d`. Stateless — each open is fresh. Mnemonic: **e**xtra. |
| `prefix + b` | `tw-break` | Smart break-and-stay: breaks the current pane out to its own window, stays in current window. Saves position to a per-session stack. Mnemonic: **b**reak. |
| `prefix + j` | `tw-join` | Smart join: pops the most recently saved entry from the stack and joins that pane back into its source window, restoring roughly the same side and size. Mnemonic: **j**oin. |
| `prefix + @` | — | Raw join: `join-pane -s :! -h` — pulls the last active window into the current window as a horizontal split. No stack involved. Mnemonic: bring something **@** here. |
| `prefix + m` | `tw-monitor` | Monitor toggle: adds a slim btop pane (~25% width) on the right side of the current window. Press again to remove it. Independent instance from the `logs` window. Mnemonic: **m**onitor. |

### Existing tmux defaults — how they fit in

These are untouched default tmux bindings that complement the new ones:

| Binding | tmux default | Role in this workflow |
|---|---|---|
| `prefix + !` | `break-pane` (raw, follows) | Break-and-follow: breaks current pane to a new window and switches to it. Does **not** save to the stack — use `prefix + @` (not `j`) to bring it back. |
| `prefix + z` | Toggle pane zoom | Temporarily fullscreen any pane without breaking it out. Good alternative to `b` when you just need more space briefly. |
| `prefix + n` / `p` | Next / previous window | Standard navigation to reach windows created by `prefix + !` or `prefix + b`. |
| `prefix + l` | Last window | Jump back to the most recently used window — useful after `prefix + !` break-and-follow. |

---

## Companion scripts

### `bin/tw-break`

Smart break-and-stay. Called by `prefix + b`.

**Behavior:**
1. Capture current pane's: source window name, side (determined by pane position
   within the layout — leftmost = left, rightmost = right, etc.), and size as a
   percentage.
2. Push an entry onto the session stack (tmux environment variable
   `TW_PANE_STACK` — space-separated list of encoded entries).
3. Run `break-pane -d` to break the pane out without following.

**Position encoding:** `<window-name>:<H|V>:<before|after>:<size%>`
- `H`/`V` — horizontal or vertical split direction to use on restore
- `before`/`after` — whether the pane was before or after other panes (controls
  `-b` flag on `join-pane`)
- `size%` — percentage width or height to restore

**Edge cases:**
- Only one pane in window: break still works; position entry records the window
  name so `j` can join it back correctly.

### `bin/tw-join`

Smart join. Called by `prefix + j`.

**Behavior:**
1. Read `TW_PANE_STACK` from tmux environment.
2. If empty: `display-message "tw: nothing to restore"` and exit.
3. Pop the top entry. Decode window name, split direction, before/after, size.
4. Run `join-pane -s :<window-name> [-h|-v] [-b] -l <size%>` targeting the
   source window.
5. Write updated stack back to `TW_PANE_STACK`.

**Edge cases:**
- Target window no longer exists (e.g. was closed): skip entry, display message
  `"tw: window <name> gone, skipping"`, pop and continue to next entry.
- Stack has one entry: after pop, stack is empty; next `j` shows "nothing to
  restore".

### `bin/tw-monitor`

Monitor toggle. Called by `prefix + m`.

**Behavior:**
1. Check if any pane in the current window has title `tw-monitor` (set via
   `select-pane -T tw-monitor` when created).
2. If **not present**: split a new pane on the right (`split-window -h -l 25%
   btop`), set its title to `tw-monitor`.
3. If **present**: kill that pane (`kill-pane -t <pane-id>`).

Independent from the `logs` window — both can run btop simultaneously.

**Edge cases:**
- Current window has no room for a 25% split (very narrow terminal): tmux will
  error; script catches the error and shows `display-message "tw: terminal too
  narrow for monitor panel"`.

---

## Position-saving mechanism

Uses tmux session environment variables — no temp files, session-scoped,
survives pane switches and window changes.

```
TW_PANE_STACK="logs:H:after:30% scratch:V:before:50%"
```

Each entry is a space-separated token. Stack grows left (push prepends, pop
takes first token).

---

## What is NOT changed

- `prefix + !` default binding — left untouched. Documented above.
- `shell` and `raw` templates — unchanged.
- The `install_assistant_tools.sh` installer — no changes needed since scripts
  are symlinked from `bin/` on install.
- Since installed commands are symlinks into `bin/`, editing the scripts takes
  effect immediately with no reinstall.

---

## Files changed

```
bin/tmux-workspace        # logs window: btop; add bind-key calls for e, b, j, @, m
bin/tw-break              # new: smart break-and-stay
bin/tw-join               # new: smart join with stack restore
bin/tw-monitor            # new: monitor panel toggle
```

The installer only symlinks named scripts (`assistant`, `collab`, `tmux-workspace`,
`tw`). The new companion scripts do **not** need to be on PATH — `tmux-workspace`
resolves its own location at session-creation time and passes absolute paths to
`bind-key`:

```bash
_tw_bin="$(dirname "$(readlink -f "$0")")"
tmux_do bind-key b run-shell "$_tw_bin/tw-break"
tmux_do bind-key j run-shell "$_tw_bin/tw-join"
tmux_do bind-key m run-shell "$_tw_bin/tw-monitor"
```

No installer changes needed.
