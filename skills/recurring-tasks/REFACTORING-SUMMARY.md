# Recurring-Tasks Refactoring Summary

## What Changed

### **Before (Complex)**
- 6 invocation layers: runner → run-skill.sh → invoke-agent.sh → assistant → skill
- Per-job runner scripts generated in `scripts/runners/`
- Multiple shell scripts (invoke-agent.sh, run-skill.sh, env.sh)
- Hardcoded paths in systemd units
- Complex test infrastructure with temp directories (caused path-clobbering bug)
- 279 lines of healthcheck bash
- 194 lines of sync-units.py
- 537 lines of tests focused on edge cases from complexity

**Total complexity:** ~2000 lines of code + extensive documentation

### **After (Simple)**
- 2 invocation layers: systemd service → bash -c "command"
- Single universal invocation: `invoke-skill <name>` (on PATH)
- No per-job runner scripts (command embedded in systemd unit)
- No hardcoded paths (uses invoke-skill on PATH)
- No test infrastructure creating temp directories
- ~150 lines of healthcheck Python (clearer logic)
- ~130 lines of sync-units.py (easier to understand)
- Simple unit tests only (no end-to-end temp environment tests)

**Total complexity:** ~500-600 lines of code + simpler documentation

## Key Simplifications

### 1. **Removed Invocation Layers**

**Before:**
```bash
# Runner script
DBUS=... PATH=... /path/to/scripts/runners/job.sh
  → /path/to/scripts/run-skill.sh job
    → /path/to/scripts/invoke-agent.sh job
      → reads AI_AGENT_COMMAND_TEMPLATE
        → assistant --local --claude -p "/job"
```

**After:**
```bash
# Systemd unit directly
ExecStart=/bin/bash -c "invoke-skill job"
```

### 2. **Removed Path Hardcoding**

**Before:**
- Systemd unit → points to runner script at fixed path
- If test creates temp dir, runner path becomes invalid
- This broke the system (our bug fix)

**After:**
- Systemd unit → calls `invoke-skill` by name (on PATH)
- Works regardless of where invoke-skill is installed
- Test installations generate their own invoke-skill in their temp bin dir

### 3. **Removed Per-Job Runner Generation**

**Before:**
- `sync-units.py` generated a runner script for each job
- Runner scripts duplicated environment setup
- Each runner was essentially identical

**After:**
- Command runs directly from systemd ExecStart
- No runner scripts to generate
- Command template comes from jobs.yaml

### 4. **Removed Test-Infrastructure Complexity**

**Before:**
- `test-live-job.py`: 178 lines
- Creates temporary skill, job, and manifest
- Runs through real backend
- Causes temporary installations to clobber real environment

**After:**
- Unit tests for YAML parsing and unit generation
- No temporary directory creation
- No environment mutation
- Much simpler to understand and maintain

### 5. **Simplified Healthcheck**

**Before:**
- 279 lines of bash
- Complex environment variable parsing
- Multiple command invocations
- Shell-specific error handling

**After:**
- ~150 lines of Python
- Clear, straightforward logic
- Easier to extend
- Cross-platform compatible

## Files Removed

```
scripts/invoke-agent.sh          (invoke-skill on PATH now handles this)
scripts/run-skill.sh             (command runs directly from systemd)
scripts/runners/                 (no per-job runners needed)
  *.sh                          (all removed)
scripts/enable-job.py           (merged into manage-job-refactored.py)
scripts/disable-job.py          (merged into manage-job-refactored.py)
scripts/test-job.py             (replaced with scripts-test command)
scripts/status.sh               (replaced with scripts-status command)
scripts/view-logs.sh            (replaced with scripts-view-logs command)
tests/test_setup_tools_recurring_env.py  (no longer needed - no env.sh generation)
tests/                          (simplified to just sync-units and YAML tests)
healthcheck.sh                  (replaced with healthcheck-refactored.py)
```

## Files Added

```
scripts/sync-units-refactored.py     (simplified, ~130 lines)
scripts/healthcheck-refactored.py    (~150 lines)
scripts/manage-job-refactored.py     (~140 lines)
SKILL-refactored.md                  (clearer documentation)
```

## Migration Path

1. **Backup:** `git commit` current state
2. **Replace scripts:**
   - Remove old scripts
   - Move refactored versions to proper names
   - Update SKILL.md
3. **Update jobs.yaml:** Simplify commands if needed (already mostly compatible)
4. **Test:**
   - `python3 scripts/sync-units-refactored.py` → generates units
   - `systemctl --user status ai-*.timer` → verify timers
   - `python3 scripts/manage-job-refactored.py test daily-plan` → test
5. **Update install-assistant-tools:** Reflect that env.sh is no longer generated

## Lessons Learned (Implemented)

1. ✓ **No hardcoded paths** — Use PATH-based command invocation
2. ✓ **No temp directories in tests** — Unit tests only
3. ✓ **Minimal layers** — Direct invocation from systemd
4. ✓ **Don't repeat patterns** — Use single universal invocation (invoke-skill)
5. ✓ **Python for logic** — Cross-platform and clearer than bash
6. ✓ **Jobs.yaml as source of truth** — No other state

## Measurable Improvements

| Metric | Before | After | Improvement |
|--------|--------|-------|------------|
| Total code | ~2000 lines | ~500-600 lines | **75% reduction** |
| Invocation layers | 6 | 2 | **67% reduction** |
| Runner scripts | 1 per job | 0 | **Eliminated** |
| Test env creation | Yes (problematic) | No | **Safer** |
| Cross-platform | Partial (bash) | Yes (Python) | **Better** |
| Documentation | 257 lines | Simpler | **Clearer** |

## Testing Strategy

Instead of complex end-to-end tests with temp environments:

1. **Unit tests** for sync-units.py:
   - Parse jobs.yaml ✓
   - Generate valid systemd units ✓
   - Cron → OnCalendar conversion ✓

2. **Integration tests**:
   - Create test job in jobs.yaml
   - Run sync-units.py
   - Verify systemd units created correctly
   - Clean up

3. **Manual validation**:
   - Install on fresh machine
   - Enable/disable/test commands work
   - Jobs fire on schedule

## Benefits

1. **Easier to understand** — Less code, simpler flow
2. **Easier to maintain** — Fewer moving parts
3. **Less error-prone** — No temp directory issues
4. **Cross-platform** — Python instead of bash
5. **More reliable** — Fewer failure modes
6. **Better aligned** — Uses invoke-skill pattern from install-assistant-tools

## Not Changed

- **jobs.yaml format** — Same schema, fully compatible
- **systemd timer behavior** — Same Persistent=true, OnCalendar scheduling
- **Log locations** — logs/<name>/run.log same as before
- **CLI interfaces** — Same dispatcher commands (but simpler implementation)
