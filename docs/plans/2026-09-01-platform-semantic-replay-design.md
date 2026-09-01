# Platform Semantic Replay Design

## Functional tests and injected system semantics

The tests selected by this mechanism remain functional tests. They assert
platform-independent behavior and contain no replay marker, platform branch,
alternate-platform parameter, or injected-fault setup.

Production code supplies the system-specific part. When a functional test
implicitly reaches a registered semantic boundary, the ordinary Linux pass
records that fact. The replay mechanism then runs the same test, with the same
functional assertions, while injecting one alternate system model at that
boundary. The mechanism therefore adds macOS or Windows policy specificity to
the relevant subset of functional tests; it does not turn those tests into
platform-specific tests.

Semantic replay is not native emulation. A model may select pure policy such
as application-directory layout or environment-key treatment. It does not
reproduce the real kernel, filesystem, process, pipe, socket, browser, keyring,
or performance environment. Behavior that depends on those physical surfaces
retains native CI evidence.

## First-release goal

Prove selective semantic replay with one real boundary. On Linux, the
portability gate runs its normal functional tests once, discovers exactly which
passing tests implicitly used the `famulus-paths` boundary, and replays only
those tests under the applicable macOS and Windows policy models.

The first release establishes the observation and replay loop. It does not add
automatic fault injection, repository-wide platform classification, or a
general platform-codec framework.

## First-release model

The registered `platforms` node owns three canonical model identities:

- `linux`;
- `macos`, selected by host token `darwin`;
- `windows`, selected by host token `win32`.

Unknown replay model names fail closed. Existing explicit Famulus platform
inputs retain their compatibility contract: `darwin` selects macOS, `win32`
selects Windows, and every other explicit token selects the existing
POSIX-style policy without triggering observation. The first release has one
immutable contract, `famulus-paths`, whose supported alternate models are
`macos` and `windows`. Contracts are a fixed process-global mapping; the
first release has no dynamic registration API.

The node exports one read-only Python interface,
`platforms.interface.model@1`, with `allow_all_modules: true` and no process
binding. This lets both registered consumers and compatibility facades reuse
the same pure authority without inventing blueprint nodes for dispatcher code.
The `common.source.famulus-paths` blueprint declares the source dependency and
interface use. The existing
`officina.dispatcher.platforms.current_platform_name()` remains a compatibility
facade but delegates token normalization to this single authority, preserving
unsupported host tokens unchanged.

The same authority keeps compatibility naming separate from policy selection:
`current_platform_name(token)` returns an unsupported dispatcher host token
unchanged, while its internal host-policy selector maps `darwin` to `macos`,
Windows host tokens to `windows`, and every other token to the `linux` model's
POSIX policy. This ensures an implicit Famulus call on a FreeBSD-like host
retains its existing POSIX fallback without adding an unsupported replay model.

One context-local replay state holds the active model and optional observer.
`boundary_model(boundary_id, explicit=...)` behaves as follows:

- an explicit platform token returns its mapped model without notifying the
  observer, preserving existing conformance calls;
- an omitted platform returns the active replay model, or the host-policy model
  selected by the mapping above outside replay, and notifies the observer of
  the boundary ID;
- context changes are reset by their `ContextVar` token in `finally`.

The model identity is deliberately small. Generic newline, PATH-separator,
executable-suffix, process-representation, and fault fields are added only when
a second proven boundary needs them.

## Discovery and replay

The pytest plugin observes ordinary, unannotated functional items. For each
exact pytest node ID it buffers boundary IDs reached during setup, call, and
teardown. It records the node only when the complete protocol passes. Skips,
expected failures, unexpected passes carrying xfail metadata, setup failures,
call failures, and teardown failures are excluded.

The focused portability task remains serial, matching the existing CI
sentinel. Its child pytest plugin uses a `pytest_runtest_protocol` hookwrapper
only to install and reset per-item observation. A
`pytest_runtest_makereport` hookwrapper accumulates setup, call, teardown, and
`wasxfail` state. On a passing teardown it either retains a fully passing
discovery item or, during replay, converts the report to a normal failed test
with an explicit diagnostic if the required implicit boundary was not reached.
The plugin deduplicates repeated boundary visits and deterministically groups
exact passing node IDs. There is no worker-report transport or worker side-file
protocol in the first release.

At session finish, the child uses the contracts imported inside the tested
repository view to publish one run-identified manifest from model ID to sorted
exact node IDs. It writes the manifest through
`officina.common.atomic_files.atomic_replace_bytes()` inside the existing
run-private artifact root. The parent runner consumes only that manifest; it
does not resolve contracts from a potentially different working tree.

After a green Linux baseline, the runner starts one fresh serial pytest
subprocess per non-empty alternate model. Replays:

- use the same staged or working repository view as the baseline;
- select only the exact node IDs in the manifest, preserving targeted runs;
- activate the requested model and disable discovery, preventing recursion;
- verify that each selected test still reaches an applicable implicit boundary;
- receive separate cache, timing, and task labels;
- propagate every nonzero status to the repository gate.

A red baseline starts no replay. Non-Linux hosts run the baseline only.

## Real pilot and honest evidence

`resolve_famulus_paths()` and `FamulusPaths.get()` accept
`platform: str | None = None`. Existing explicit callers keep their current
conformance behavior. The public `famulus-paths-get` gateway omits the
platform argument so at least one real production route reaches the implicit
boundary.

The pilot proves platform-policy selection: application-directory layout and
normalized environment lookup. Because Linux still supplies the concrete
`pathlib.Path` objects, the pilot does not claim native Windows path parsing,
separator, case-folding, permissions, or filesystem behavior. Those claims
remain owned by explicit boundary-contract tests and the native matrix.

## Release gate

The central `precommit` suite adds one focused `tests:portability` phase
after its existing combined phase. The installed hook already runs that suite,
so routine local commits receive replay without new hook orchestration. The
focused phase duplicates only the small portability sentinel set, keeps replay
failures isolated from validator execution, and uses the same staged repository
view as the combined phase.

Replay activates from the resolved `tests:portability` task, not from the
outer suite name. The existing CI sentinel
`--suite full --task tests:portability` therefore exercises the same
discovery/replay route. On non-Linux hosts the focused task remains a baseline
only.

## Deferred scope

The following are separate follow-up designs:

- transparent and result-changing fault injection;
- `active_fault()` and boundary fault profiles;
- repository-wide platform-debt inventory and native declarations;
- cross-platform validator enforcement for new semantic/native boundaries;
- canonical standard revision;
- generic platform codecs and additional production boundaries;
- thread or background-task observation;
- xdist discovery/report transport or folding discovery into a combined
  pytest session;
- parallel replay and broad native-CI reduction.

## Success

The first release is complete when the default local Linux precommit gate and
the existing CI portability sentinel discover one
annotation-free functional Famulus-path test, replays its exact node ID under
the macOS and Windows policy models, excludes untouched and non-passing tests,
fails closed on invalid protocol state or manifest data, propagates replay
failures, and leaves physical platform claims in native CI.
