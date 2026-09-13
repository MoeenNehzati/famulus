# Setup interface manager interactive evidence appendix

This appendix preserves the reproducible, redacted command surface and selected
structured evidence from the private Task 10 campaign. Raw Codex events,
private prompts, authentication, secret-bearing fixture inputs, and complete
temporary ledgers are intentionally not committed.

## Redacted command transcript

The standalone installer followed the [official Codex CLI procedure](https://developers.openai.com/codex/cli/):

```bash
curl -fsSL https://codex.openai.com/install.sh -o <TASK_ROOT>/codex-install.sh
env CODEX_INSTALL_DIR=<TASK_ROOT>/bin CODEX_HOME=<TASK_ROOT>/codex-home sh <TASK_ROOT>/codex-install.sh
<TASK_ROOT>/bin/codex --version
```

The installed result was `codex-cli 0.152.0`. Authentication was copied from
the normal Codex home into `<TASK_ROOT>/codex-home/auth.json` and restricted to
mode `0600`; neither its bytes nor a digest were recorded. The installer added
a three-line PATH block to the normal `.bashrc`; the controller removed that
exact block and verified that neither `<TASK_ROOT>` nor the installer marker
remained.

The local plugin installation followed the [official plugin workflow](https://developers.openai.com/codex/plugins/):

```bash
env CODEX_HOME=<TASK_ROOT>/codex-home \
  <TASK_ROOT>/bin/codex plugin marketplace add \
  <IMPLEMENTATION_WORKTREE> --json
env CODEX_HOME=<TASK_ROOT>/codex-home \
  <TASK_ROOT>/bin/codex plugin add famulus@nullkit --json
```

Production workers used this exact redacted launch shape. Global approval
options precede `exec`; every scenario used a new process and thread:

```bash
env \
  CODEX_HOME=<TASK_ROOT>/codex-home \
  HOME=<TASK_ROOT>/production-home \
  XDG_CONFIG_HOME=<TASK_ROOT>/xdg-config \
  XDG_DATA_HOME=<TASK_ROOT>/xdg-data \
  XDG_STATE_HOME=<TASK_ROOT>/xdg-state \
  XDG_CACHE_HOME=<TASK_ROOT>/xdg-cache \
  TMPDIR=<TASK_ROOT>/tmp \
  PATH=<TASK_ROOT>/bin:<SELECTED_PYTHON_BIN>:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  <TASK_ROOT>/bin/codex --approve-for-me exec --json \
  --sandbox workspace-write \
  -C <TASK_ROOT>/project \
  --skip-git-repo-check \
  -o <SCENARIO_EVIDENCE>/final.md \
  "<CARD_TEXT>"
```

Synthetic workers used the repaired fixture installation and deliberately
removed controller overrides so `mcp.json` supplied the one selected plugin
data path:

```bash
env -u FAMULUS_HOST -u FAMULUS_PLUGIN_DATA \
  CODEX_HOME=<TASK_ROOT>/fixture/codex-home-green \
  HOME=<TASK_ROOT>/fixture/home \
  XDG_CONFIG_HOME=<TASK_ROOT>/fixture/xdg-config \
  XDG_DATA_HOME=<TASK_ROOT>/fixture/xdg-data \
  XDG_STATE_HOME=<TASK_ROOT>/fixture/xdg-state \
  XDG_CACHE_HOME=<TASK_ROOT>/fixture/xdg-cache \
  TMPDIR=<TASK_ROOT>/fixture/tmp \
  PATH=<TASK_ROOT>/bin:<SELECTED_PYTHON_BIN>:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  <TASK_ROOT>/bin/codex --approve-for-me exec --json \
  --sandbox workspace-write \
  -C <TASK_ROOT>/fixture/project \
  --skip-git-repo-check \
  -o <SCENARIO_EVIDENCE>/final.md \
  "<CARD_TEXT>"
```

`<CARD_TEXT>` denotes the exact contents preserved separately as the private
scenario `card.md`; Codex 0.152.0 received those contents as its positional
prompt. For a file-driven repetition, the equivalent executable ending is
`- < <SCENARIO_EVIDENCE>/card.md`, because a bare pathname is not read as a
prompt file.

The controller sandbox could not reach the service. Each evaluated Codex
worker command therefore used the host-capable outer launcher while retaining
Codex's own `workspace-write` sandbox. No evaluated thread used `resume`,
`fork`, `--last`, or `--ephemeral`.

## Representative structured evidence

P04 first use returned one pending Markdown step without beginning a flow:

```json
{
  "code": "setup_required",
  "flow_id": null,
  "pending_stack": [
    {
      "action": "run-setup",
      "interface": "milestone-logging.interface.setup",
      "kind": "markdown",
      "version": 1
    }
  ],
  "root_setup_interface": "milestone-logging.interface.setup",
  "schema_version": 1
}
```

After P05 settlement and authorization, the production ledger contained only
the verified receipt and root claim:

```json
{
  "active_flow": null,
  "interfaces": {
    "milestone-logging.interface.setup": {
      "required_by": ["milestone-logging.interface.setup"],
      "version": 1
    }
  },
  "schema_version": 1
}
```

F07's shared state after authorizing both roots demonstrates the exact diamond
claim. B is version 2 because F05 installed the stale-version fixture:

```json
{
  "active_flow": null,
  "interfaces": {
    "task10-fixture-b.interface.setup": {
      "required_by": ["task10-fixture.interface.setup"],
      "version": 2
    },
    "task10-fixture-c.interface.setup": {
      "required_by": [
        "task10-fixture-peer.interface.setup",
        "task10-fixture.interface.setup"
      ],
      "version": 1
    },
    "task10-fixture-peer.interface.setup": {
      "required_by": ["task10-fixture-peer.interface.setup"],
      "version": 1
    },
    "task10-fixture.interface.setup": {
      "required_by": ["task10-fixture.interface.setup"],
      "version": 1
    }
  },
  "schema_version": 1
}
```

F08's public result was deliberately generic:

```text
dispatcher.runtime_misconfigured — original probe did not run.
```

The injected secret and malformed ledger bytes were absent from the scoped
public result and diagnostics. After private offline restoration, status again
returned the expected A1/B2/C1 pending stack.

F09 teardown ran A, B, C. All seven manager responses, including terminal
ready, returned `resume_original=false`; no authorize or ordinary probe ran.
The counter remained byte-identical and the final ledger was:

```json
{"active_flow":null,"interfaces":{},"schema_version":1}
```

## Synthetic overlay identity

- Production base: `a46f7f68b2ad146b4a914b1738dca1d3cda3c7b3`
- Validated v1 fixture: `0ce257451c14488dff489bbbda83d296dbf23d4e`
- Final v2 fixture used by F05–F09: `5968dfed54f07e36e77a78c8c215647543630bcf`
- Base-to-v1 binary diff SHA-256:
  `e9100ef028c4b93a1355e13bc23ee00c05455f67768abe2552eeced0c82baecd`
- Base-to-final-v2 binary diff SHA-256:
  `10d0320881ab683c5dc8b6df857512e50489c8186bfd4479ab428b7d3e492f28`
- V1-to-v2 five-file transition diff SHA-256:
  `810a2727112ff2505cfba6480f4c682a11dab2b2a3de58988d33a98c5b01dc45`

The five v1-to-v2 paths were:

```text
skills/setup-interface-manager/_rtx/_setup_dispatches.py
skills/setup-interface-manager/_rtx/blueprints/rtx-manager.yaml
skills/task10-fixture-b/SKILL.md
skills/task10-fixture-b/blueprints/setup.yaml
skills/task10-fixture/blueprint.yaml
```

The complete 49-path base-to-v2 overlay was:

```text
references/blueprint-schema/runtime_dependencies.json
skills/setup-interface-manager/_rtx/_setup_dispatches.py
skills/setup-interface-manager/_rtx/blueprints/rtx-manager.yaml
skills/setup-interface-manager/_rtx/tests/test_setup_manager.py
skills/task10-fixture-b/SKILL.md
skills/task10-fixture-b/_rtx/__init__.py
skills/task10-fixture-b/_rtx/_fixture_runtime.py
skills/task10-fixture-b/blueprint.yaml
skills/task10-fixture-b/blueprints/gateway.yaml
skills/task10-fixture-b/blueprints/runtime.yaml
skills/task10-fixture-b/blueprints/setup.yaml
skills/task10-fixture-b/blueprints/teardown.yaml
skills/task10-fixture-b/setup.md
skills/task10-fixture-b/teardown.md
skills/task10-fixture-c/SKILL.md
skills/task10-fixture-c/_rtx/__init__.py
skills/task10-fixture-c/_rtx/_fixture_runtime.py
skills/task10-fixture-c/blueprint.yaml
skills/task10-fixture-c/blueprints/gateway.yaml
skills/task10-fixture-c/blueprints/runtime.yaml
skills/task10-fixture-c/blueprints/setup.yaml
skills/task10-fixture-c/blueprints/teardown.yaml
skills/task10-fixture-c/setup.md
skills/task10-fixture-c/teardown.md
skills/task10-fixture-peer/SKILL.md
skills/task10-fixture-peer/_rtx/__init__.py
skills/task10-fixture-peer/_rtx/_fixture_runtime.py
skills/task10-fixture-peer/blueprint.yaml
skills/task10-fixture-peer/blueprints/gateway.yaml
skills/task10-fixture-peer/blueprints/runtime.yaml
skills/task10-fixture-peer/blueprints/setup.yaml
skills/task10-fixture-peer/blueprints/teardown.yaml
skills/task10-fixture-peer/setup.md
skills/task10-fixture-peer/teardown.md
skills/task10-fixture/SKILL.md
skills/task10-fixture/_rtx/__init__.py
skills/task10-fixture/_rtx/_fixture_runtime.py
skills/task10-fixture/_rtx/blueprint.yaml
skills/task10-fixture/_rtx/blueprints/runtime.yaml
skills/task10-fixture/blueprint.yaml
skills/task10-fixture/blueprints/gateway.yaml
skills/task10-fixture/blueprints/setup.yaml
skills/task10-fixture/blueprints/teardown.yaml
skills/task10-fixture/setup.md
skills/task10-fixture/teardown.md
tests/fixtures/setup_interface_manager/interactive/v2-b.patch
tests/test_setup_interface_manager_coverage.py
tests/test_setup_interface_manager_interactive_fixture.py
validators/skill/skill_md_dispatch.py
```

At the final v2 commit, these production state-machine files remained
byte-identical to the base:

```text
74007b073fbc8c6b89e3fb9f845795bb30edd7426afbe38bf99f4cb4123f01aa  mcp_server.py
436fd39ded546a11f085e32ca5ed925009a95668b14cd33bbe4b1ba5820e16ca  _setup_manager.py
a5729a08515db2a48648bdb1922a11d23e5a1ec0337fc69db84ed65c933d3d0b  _setup_state.py
2938e5bbfaf8b1f5dbd85e944f9b2484c954890daa03db55cfe502ff29790542  _setup_evaluation.py
```
