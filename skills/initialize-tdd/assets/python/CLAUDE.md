# {{PROJECT_NAME}}

## Development workflow (TDD, staged approval)

For every new piece of functionality, follow these steps **in order**. Stop and
wait for explicit user approval at the end of each step before starting the
next one.

1. **Design**
   - Decide which models/functions/modules are needed.
   - Write stubs (signatures + docstrings only, `raise NotImplementedError` or
     `pass` bodies). Docstrings must explain:
     - Desired behavior
     - Inputs and outputs (types, shapes, units, formats)
     - How the component interacts with other existing/new components
   - Wait for approval.

2. **Tests**
   - Write unit tests for each new component, plus integration tests covering
     how it connects to existing components.
   - Tests should encode the *expected* behavior (will fail against stubs).
   - For anything driven by I/O streams or external events (mic input, audio
     playback, network calls, subprocesses, timers, etc.), write tests that
     mock that stream/event for full testability.
   - Wait for approval.

3. **Implementation**
   - Before writing any implementation, run the new tests and confirm they
     fail against the stubs (RED) — and that they fail for the expected
     reason (missing behavior, not a typo/import error).
   - Implement the stubs so the tests from step 2 pass (GREEN). Write the
     minimal code needed — don't add behavior beyond what the tests require.
   - It's fine to add auxiliary parameters to stubs (e.g. injectable
     dependencies, clocks, streams) if needed for testability — just note
     what was added and why.
   - Run the full test suite and confirm everything passes, with clean
     output (no errors/warnings).
   - Wait for approval.

4. **Docs**
   - Update `README.md` to reflect the new functionality.
   - Update docstrings/module docs for any existing code touched by the
     change.

## Project conventions

- **Config**: project runtime parameters go in `.env`; parameters only needed
  for tests go in `.testenv`. Both are gitignored.
- **Logging**: use the centralized logger from `src/project/logger.py`
  (`get_logger(__name__)`) everywhere. Don't use bare `print()` or ad-hoc
  `logging.getLogger()` calls.
- **Dependencies**: whenever a new library/tool is introduced, add it to
  `requirements.txt`, and update `install.sh` if a new system-level tool is
  needed.
- **Environment**: all code runs inside the Python venv at `.venv/` (created
  via `install.sh`).
- **Structure/refactoring**: keep modules organized by responsibility under
  `src/project/`. If a refactor of existing structure seems warranted,
  propose the suggestion and get confirmation before doing it.

## Project layout

```
{{PROJECT_NAME}}/
├── CLAUDE.md
├── README.md
├── .env
├── .testenv
├── requirements.txt
├── install.sh
├── src/project/       # application source
├── tests/             # unit & integration tests
└── logs/              # runtime logs (gitignored)
```
