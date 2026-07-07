# {{PROJECT_NAME}}

## Development workflow (TDD, staged approval)

For every new piece of functionality, follow these steps **in order**. Stop and
wait for explicit user approval at the end of each step before starting the
next one.

1. **Design**
   - Decide which modules/functions are needed.
   - Write stubs (signatures + docstrings only, empty/placeholder bodies).
     Docstrings must explain:
     - Desired behavior
     - Inputs and outputs (types, shapes, units, formats)
     - How the component interacts with other existing/new components
   - Wait for approval.

2. **Tests**
   - Write unit tests for each new component, plus integration tests covering
     how it connects to existing components.
   - Tests should encode the *expected* behavior (will fail against stubs).
   - For anything driven by I/O streams or external events (input devices,
     network calls, subprocesses, timers, etc.), write tests that mock that
     stream/event for full testability.
   - Wait for approval.

3. **Implementation**
   - Before writing any implementation, run the new tests and confirm they
     fail against the stubs (RED) — and that they fail for the expected
     reason (missing behavior, not a typo/import error).
   - Implement the stubs so the tests from step 2 pass (GREEN). Write the
     minimal code needed — don't add behavior beyond what the tests require.
   - Run the full test suite and confirm everything passes, with clean
     output (no errors/warnings).
   - Wait for approval.

4. **Docs**
   - Update `README.md` to reflect the new functionality.
   - Update docstrings/module docs for any existing code touched by the
     change.

## Project conventions

- **Structure/refactoring**: keep modules organized by responsibility. If a
  refactor of existing structure seems warranted, propose the suggestion and
  get confirmation before doing it.
- No language-specific tooling is set up for this project (no venv, logger,
  or config scaffold) — add conventions here as the project's stack is
  decided.

## Project layout

```
{{PROJECT_NAME}}/
├── AGENTS.md          # a compatibility symlink alongside this file also exists
├── README.md
└── .gitignore
```
