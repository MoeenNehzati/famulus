# Platform Semantic Replay Design

## Goal

Keep functional tests platform-agnostic while Linux automatically replays only tests that exercised governed platform semantics. Reduce native CI to behavior that depends on the real kernel, filesystem, process, socket, browser, keyring, or performance environment.

## Design

Platform-sensitive production code enters a registered boundary. Each semantic boundary declares supported Linux, macOS, and Windows models and optional faults that must be transparent to functional behavior. Models contain pure values and codecs—path flavor, PATH separator, executable suffix, newline, environment-key normalization, and process representation—and perform no host calls.

The normal pytest pass records the exact node ID of each passing test that implicitly selected the host model at a registered boundary. Calls with an explicit platform are boundary conformance cases and are not replay candidates. On Linux, the repository runner merges worker traces and starts fresh pytest subprocesses for only the implicit cases under each applicable alternate model. Multiple boundaries select a union of models, never a Cartesian product. Targeted selectors remain targeted.

Functional tests contain no replay annotations, platform branches, or fault logic. Boundary contract tests own exact platform encodings and result-changing faults. Automatic fault replay is limited to faults whose contract says the functional result is unchanged; startup, permission, and other result-changing faults require an explicit boundary-owner oracle.

## Enforcement

- A semantic boundary may use only its active model, not ambient `sys.platform`, `os.name`, `os.pathsep`, or host path formatting. Explicit platform arguments remain supported but do not trigger replay.
- New shared host-dependent code must register as semantic or native. A checked inventory freezes existing unclassified debt and may only shrink.
- Native boundaries declare their native task and reason; semantic replay never claims physical OS evidence.
- Replay failure fails the repository gate and reports the model, boundary/fault profile, and exact node ID.

## Initial Scope

Implement the registry, three pure models, pytest trace/replay plugin, runner integration, validation, and one real path/environment pilot through `resolve_famulus_paths`. Prove automatic selection, exact parametrized node IDs, xdist trace merging, no replay of untouched tests, model grouping, transparent fault selection, and failure propagation. Do not broadly migrate unrelated runtime modules.

## Success

Linux precommit runs ordinary functional tests once, then replays only observed semantic-boundary tests under applicable alternate models. The focused replay suite, portability task, precommit suite, and native CI matrix are green. Existing physical-boundary tests remain native.
