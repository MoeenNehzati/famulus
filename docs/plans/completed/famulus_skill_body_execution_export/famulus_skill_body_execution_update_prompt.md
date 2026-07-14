# Prompt: Add Skill Body Execution Validation to Famulus

Status: done. Implemented as `skills/skill-maker/validators/skill_body_execution.py`
with focused tests in `tests/validate_skill_body_execution.py`, shared
`SKILL.md` body parsing in `validators/skill_md_body.py`, and guideline updates
in `references/skill-standards/skill-guidelines.md`.

We need to update Famulus’s skill validation system and skill guidelines to prevent command/execution leakage in hand-authored `SKILL.md` bodies.

## Goal

Add a validator that catches cases where a hand-authored `SKILL.md` tells the model to run commands directly, mentions raw script paths, includes command-line flags, or otherwise leaks executable mechanics. Normal execution must live behind blueprint-declared machine interfaces. The generated blueprint interface block may contain invocation commands; the hand-authored body should refer to interface names and outcomes.

Use the attached prototype as the starting point:

- `skill_body_execution_detector.py`
- `test_command_leakage_detector.py`
- `command_leakage_test_results_v6.json`
- `command_leakage_test_results_v6.txt`

Do not blindly copy it as a standalone CLI only. Integrate it into the repo’s validator architecture.

## Implementation tasks

1. Add a new validator:

   ```text
   skills/skill-maker/validators/skill_body_execution.py
   ```

   It must export:

   ```python
   validate(repo_root) -> list[str]
   ```

   matching the existing validator runner convention.

2. The validator must scan every observed local skill:

   ```text
   skills/*/SKILL.md
   ```

   where the directory contains a real `SKILL.md`.

3. Before scanning, strip:

   - YAML frontmatter;
   - generated blueprint contract block;
   - generated blueprint interface block.

   The check must apply to all remaining hand-authored text, including ordinary prose, bullets, inline code, fenced code, unfenced snippets, and malformed/defenced command examples.

4. The validator should detect command leakage with named rule families:

   - `SBX001`: raw script or executable paths, such as `scripts/foo.py`, `skills/foo/scripts/bar.py`, `./foo.sh`, `check.py`;
   - `SBX002`: shell prompt commands, such as `$ pytest` or `> python3 script.py`;
   - `SBX003`: shell operators/redirection, such as `&&`, `||`, pipes, `>`, `2>`, `>>`;
   - `SBX004`: CLI flag leakage, such as `--json`, `--checker`, `--caller-skill`;
   - `SBX005`: command-like executable invocation, such as `run pytest`, `git clone ...`, `python -m pip ...`, `pip install ...`, `brew audit --strict`, `docker compose up`, `make quick-release`;
   - `SBX006`: operational execution phrases, such as “run the following”, “from the terminal”, “make executable”, “change the executable bit”, “shell out”, “pipe into”, “redirect output”.

5. Avoid known false positives from conceptual prose. These should not be flagged:

   ```text
   pytest is used for testing.
   Git state matters for the repo.
   Python 3.10+ is supported.
   Docker Compose is a tool.
   The check interface reports whether tests passed.
   Run the doctor-check interface and use its verdict.
   ```

   Interface instructions are allowed when they refer to exported interface ids rather than executable commands.

6. Error messages must be educational. They should tell the model what to do instead, for example:

   - “Move executable paths into a blueprint machine interface and refer to the interface name instead.”
   - “Generated interface blocks are the place for invocation commands.”
   - “Shell composition belongs in scripts.”
   - “Move flags to blueprint usage/patterns or script help.”
   - “Make execution a script/interface, not prose.”

7. Add tests.

   Prefer a repo-conventional test file such as:

   ```text
   tests/validate_skill_body_execution.py
   ```

   or the closest existing convention used by the repo.

   Tests must cover at least:

   - generated blueprint blocks are ignored;
   - YAML frontmatter is ignored;
   - ordinary hand-authored prose is scanned;
   - fenced code is scanned if hand-authored;
   - unfenced commands are scanned;
   - defenced commands are scanned;
   - command lines survive perturbations such as removed fences, removed prompts, joined line breaks, and embedded prose;
   - raw script paths fail;
   - CLI flags fail;
   - shell operators fail;
   - operational phrases fail;
   - allowed interface language passes;
   - conceptual tool mentions pass.

8. Use the prototype test harness as inspiration. Include a compact stress test corpus with real-world-inspired examples, but do not bloat the repo with huge copied external README contents. Preserve the important mutation cases:

   - original;
   - defenced;
   - strip prompts;
   - join colon to next line;
   - paragraph smear;
   - all one line;
   - bulletize commands;
   - blockquote commands;
   - command only;
   - embedded no prompt;
   - inline commands;
   - typoed context;
   - remove line breaks near commands;
   - whitespace noise.

9. Update `references/skill-standards/skill-guidelines.md`.

   Add a section or strengthen the existing `SKILL.md` body rule:

   > Hand-authored `SKILL.md` must be command-free. It may state outcomes, sequencing, interface ids, and interpretation rules. It must not include shell commands, executable names used operationally, raw script paths, CLI flags, terminal snippets, or prose instructions to run commands manually. If normal operation requires execution, put it in `scripts/` and expose it through `blueprint.yaml`. The generated interface block is the normal place where invocation commands appear.

   Also clarify:

   - Validators catch command-shaped text.
   - `skill-doctor` must catch semantic leakage that regex cannot catch, such as “verify manually” or “change the executable bit” when no explicit command appears.
   - Mentioning exported interface ids is allowed.
   - Normal command invocation belongs in blueprint machine interfaces and runtime files.

10. Update any skill-maker or guideline-update skill text if needed so future skill creation/editing follows the new rule.

11. Run the repo’s required checks:

   - blueprint sync check;
   - validator runner;
   - relevant tests;
   - pre-commit hook if practical.

12. Final response should include:

   - files changed;
   - rule names added;
   - tests added;
   - checks run;
   - any known limitations.

## Important design constraints

- Do not add required dependencies on markdownlint, ShellCheck, Semgrep, or tree-sitter.
- Keep the validator Python-native and compatible with the existing validator runner.
- Existing external tools can be mentioned as future optional improvements, but this implementation should not require them.
- The validator is a tripwire, not an English theorem prover. It should catch common command leakage and teach the architecture through errors.
- Do not weaken the validator just because it cannot catch adversarial obfuscation.
- Do not scan generated blueprint interface blocks, because those are allowed to contain commands.

## Notes

The attached detector is a standalone prototype. For Famulus, adapt it into the repo’s validator shape rather than dropping the CLI script into `validators/` unchanged.
