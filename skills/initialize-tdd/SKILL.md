---
name: initialize-tdd
description: >-
  Use when the user asks to initialize a brand-new TDD project. Do not use for adding TDD to an existing project.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: software-development; topics: repository-workflow, assistant-assurance; visibility: listed
Activation: user-request; persistent modifier: no

Skill Version: 2

Uses Interfaces:
- `initialize-tdd.source.gateway -> initialize-tdd._rtx.interface.setup-compat-aliases@1`

Public Interfaces:
- `initialize-tdd.interface.default`
<!-- END BLUEPRINT CONTRACT -->

<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Executable Interfaces:

Call `famulus.invoke` with required `caller` (caller skill), `interface`, `version`, and `arguments`; optional `dry_run` defaults to false. Compact uses ordered `positionals` plus an option mapping; ordered raw argv uses `positionals: []` plus every argv token in list `options`. Never mix forms.
- `initialize-tdd._rtx.interface.setup-compat-aliases` — Create every host compatibility alias symlink (e.g. a legacy filename some host looks for specifically) in a freshly scaffolded project directory.
  - Caller: `initialize-tdd`
  - Version: 1
  - Alternative: `default`
    Arguments JSON (replace labels with actual values). Omit optional positionals and options that are not needed.
    {"options": {}, "positionals": ["project-dir"], "stdin": null}
    Required options: []; positional arity: 1..1; stdin: forbidden

Instruction Interfaces:

These are LLM-readable instruction surfaces. Read and follow them directly; do not invoke the MCP server for them.
- `initialize-tdd.interface.default` — Create a new approval-gated TDD project scaffold from explicit project name and language inputs, verify the Python starter when selected, and never commit it.
<!-- END BLUEPRINT INTERFACES -->
# Initialize TDD Project

Require the human-readable project name and implementation language before
creating anything. Derive the directory slug by lowercasing the name, replacing
spaces and underscores with hyphens, and removing characters outside
`[a-z0-9-]`. Create the slug directory in the caller's current working
directory. Reuse it only when it is empty; ask before proceeding when it is
nonempty, and never overwrite existing work.

## Select and render the scaffold

Copy all `assets/common/` content first.
Preserve the assets' staged design-and-stubs, tests, implementation, and
documentation workflow and its approval gates; render placeholders, but do not
rewrite that workflow.

For Python, overlay all `assets/python/` content, append its `.gitignore` to the
common one rather than replacing it, and make the copied bootstrap asset
executable. Keep the import package named `project`; only the distribution name
and document titles vary by project. Do not create `.env` or `.testenv`: the
generated modules use `LOG_LEVEL=INFO` and `LOG_DIR=logs` defaults when those
files are absent.

For any other language, keep the generic scaffold, add obvious build and
dependency exclusions, and adapt the generated project-conventions section
only where the requested language has clear idioms. Tell the user that this is
a best-effort generic scaffold: it does not include the Python environment,
logger, configuration modules, or starter tests.

After selecting the assets, replace `{{PROJECT_NAME}}` in every copied text
file with the name exactly as supplied and replace `{{PACKAGE_DIST_NAME}}` with
the slug wherever it appears.

## Finalize and verify

1. Invoke `initialize-tdd._rtx.interface.setup-compat-aliases` with the scaffolded
   project directory and require successful alias state before continuing.
2. Initialize Git inside the project directory without creating a commit.
3. For Python, run the copied bootstrap asset to create the virtual environment
   and install the declared dependencies. If its interpreter assumption does
   not fit the environment, adjust that copied asset rather than regenerating
   it. Run the full generated starter test suite from the virtual environment
   and require it to pass.
4. Fix any scaffold, bootstrap, or test failure before reporting success. If it
   cannot be fixed, report the exact failure and remaining partial state; inspect
   that state before retrying. Report the created files and fresh test result.
   Never commit unless the user separately asks.
