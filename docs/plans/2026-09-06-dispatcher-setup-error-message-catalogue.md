# Dispatcher and Setup Error Message Catalogue

Status: normative wording proposal for the dispatcher/setup error refactor.
Audience: maintainers implementing and reviewing the refactor.

This catalogue is organized by semantic emission condition, not exception
class. Its purpose is to prevent a factual boundary failure from becoming a
misleading diagnosis or recovery instruction when rendered for an LLM.
Public warnings, including certification diagnostics, are outside this
error-only catalogue and require a separate later audit.

## Contract

Every implementation row records an observable `emit_when`, public code or
protocol state, exact message, non-default policy, disposition, and source.
The actor and operation must be unambiguous from the message together with its
registered code and context.

Unless a row says otherwise, the following defaults are mandatory:

- safe context: registered caller, target, interface, version, operation, and
  numeric exit code only when each is already validated and relevant; every
  validated template placeholder is also allowlisted for that row;
- cause: none;
- clues: none;
- recovery: none;
- redaction: no paths, raw exception text, traceback, stdout/stderr, argv/stdin,
  environment values, credentials, ledger contents, or supplied data;
- implication: no cause, state, or action beyond the literal message.

`Cause` below means one reduced diagnosis from another named catalogue entry;
if no existing entry matches, cause is omitted. Thus every public cause already
has an exact predicate, code, message, and context policy in this document.
`Clue` names its exact observable predicate and tentative text. `Flow recovery`
means the authorized live-flow object defined by the main design. `Operator`
means no ordinary LLM action is exposed. Rows may be grouped only when all
these semantics match.

Disposition describes the migration, not byte-for-byte wording. `Retain` keeps
the current predicate and public code; every displayed template is still the
normative post-refactor text. `Replace` changes a code or classification.
`Split` separates current producer conditions into catalogue entries; one
public code may retain multiple factual templates and cause policies when its
code-level category and code-only consumer handling remain identical.
`Contain` adds a structured public boundary.

Every message follows four rules:

1. Make the actor and attempted operation unambiguous from the message, code,
   and safe context. A pure validation message need not repeat them.
2. State only the observed failure; never guess its cause.
3. State execution or completion only when the boundary proves it.
4. Keep advice out of the message. Put a possibility in a labelled clue and an
   executable response in recovery.

## Dispatcher: request and resolution

| ID | Disposition and `emit_when` | Public code and exact message | Non-default policy and forbidden implication | Producers |
| --- | --- | --- | --- | --- |
| D01 | Replace: trace graph schema is not 6 | `dispatcher.blueprint_schema_mismatch` — “Dispatcher metadata trace requires blueprint graph schema 6; received schema {schema_version}.” | Must not imply runtime repair. | `src/officina/dispatcher/core.py:110,160` |
| D02 | Retain: caller module is absent | `dispatcher.caller_not_found` — “The dispatcher caller module does not exist.” | Do not echo the unvalidated caller value. | `core.py:173` |
| D03 | Retain: caller does not declare target use | `dispatcher.interface_use_undeclared` — “Caller module `{caller_module_id}` does not declare use of `{interface_id}`.” | Must not imply an access-policy denial. | `core.py:176` |
| D04 | Replace: an access gate rejects the caller | `dispatcher.unauthorized_caller` — “Caller `{caller_module_id}` is not allowed to invoke `{interface_id}`.” | Gate identity is safe context, never a bracketed diagnostic. | `core.py:191`; `direct_authorization.py:518,543,571` |
| D05 | Retain: caller identity is empty | `dispatcher.invalid_request` — “`caller_skill` must be a nonempty string.” | Default. | `direct_runtime.py:305,363` |
| D06 | Replace: invocation lacks repository configuration | `dispatcher.repository_config_missing` — “The dispatcher invocation did not supply the required repository configuration path.” | Must not imply missing Python, packages, or setup. | `direct_runtime.py:250` |
| D07 | Split: supplied repository configuration fails validation | `dispatcher.repository_config_invalid` — “The dispatcher could not load the supplied repository configuration.” | No cause; must not imply bootstrap. | `direct_runtime.py:268`; `mcp_server.py:369` |
| D08 | Replace: resolved route has no Python process target | `dispatcher.python_target_missing` — “Resolved route `{interface_id}` has no Python process target.” | Authored route defect, not a missing interpreter. | `direct_runtime.py:156` |
| D09 | Replace: Python target lacks package or entry point | `dispatcher.python_identity_missing` — “Resolved Python target for `{interface_id}` lacks a logical package or entry point.” | Must not claim only the package is absent. | `direct_runtime.py:163` |
| D10 | Retain: OS cannot start the resolved command | `dispatcher.launch_failed` — “The dispatcher could not start `{interface_id}`.” | No cause; must not imply setup failure or target execution. | `direct_runtime.py:447` |
| D11 | Retain: module ID is noncanonical | `dispatcher.invalid_module_id` — “The dispatcher request contains an invalid module ID.” | Do not echo the invalid value. | `direct_blueprints.py:29` |
| D12 | Retain: interface ID is noncanonical | `dispatcher.invalid_interface_id` — “The dispatcher request contains an invalid interface ID.” | Do not echo the invalid value. | `direct_blueprints.py:55` |
| D13 | Retain: top-level module is absent | `dispatcher.module_not_found` — “Module not found: `{module_id}`.” | Default. | `direct_blueprints.py:145` |
| D14 | Retain: module occurs in multiple roots | `dispatcher.module_ambiguous` — “Module `{module_id}` is present in multiple configured roots.” | Default. | `direct_blueprints.py:151` |
| D15 | Split: blueprint path cannot be inspected | `dispatcher.unsafe_blueprint_path` — “The dispatcher could not inspect the blueprint path for module `{module_id}`.” | No cause; never publish the path. | `direct_blueprints.py:108,126` |
| D16 | Split: blueprint path contains a symlink | `dispatcher.unsafe_blueprint_path` — “The blueprint path for module `{module_id}` contains a symbolic link.” | Default. | `direct_blueprints.py:119` |
| D17 | Split: blueprint is not a regular file | `dispatcher.unsafe_blueprint_path` — “The blueprint for module `{module_id}` is not a regular file.” | Default. | `direct_blueprints.py:169` |
| D18 | Split: blueprint read or YAML decode fails | `dispatcher.blueprint_malformed` — “The blueprint for module `{module_id}` could not be read as YAML.” | No cause. | `direct_blueprints.py:178` |
| D19 | Retain: blueprint top level is not a mapping | `dispatcher.blueprint_malformed` — “The blueprint for module `{module_id}` must be a mapping.” | Default. | `direct_blueprints.py:187` |
| D20 | Retain: module blueprint has wrong schema/kind | `dispatcher.blueprint_schema_mismatch` — “The blueprint for module `{module_id}` must be a schema-6 module blueprint.” | Default. | `direct_blueprints.py:193` |
| D21 | Retain: blueprint identity differs from registration | `dispatcher.blueprint_identity_mismatch` — “Blueprint identity does not match registered module `{module_id}`.” | Default. | `direct_blueprints.py:199` |
| D22 | Retain: blueprint version is invalid | `dispatcher.blueprint_malformed` — “Blueprint version must be a positive integer for module `{module_id}`.” | Default. | `direct_blueprints.py:207` |
| D23 | Retain: required blueprint field is not a mapping | `dispatcher.blueprint_malformed` — “Blueprint field `{field}` must be a mapping for module `{module_id}`.” | Default. | `direct_blueprints.py:214` |
| D24 | Retain: child registrations are invalid | `dispatcher.blueprint_malformed` — “Blueprint child registrations are invalid for module `{module_id}`.” | Default. | `direct_blueprints.py:227` |
| D25 | Retain: registered child blueprint is absent | `dispatcher.module_not_found` — “Registered module blueprint not found: `{module_id}`.” | Default. | `direct_blueprints.py:243` |
| D26 | Retain: parent omits traversed child | `dispatcher.child_unregistered` — “Module `{parent_module_id}` does not register child `{child_name}`.” | Default. | `direct_blueprints.py:269` |
| D27 | Retain: relative caller lacks suffix or escapes its root | `dispatcher.invalid_caller_reference` — “Relative caller reference is invalid: it {reason}.” | `reason` is the allowlisted enum `has no local suffix` or `escapes its registration root`; never echo the reference. | `direct_authorization.py:73,84` |
| D28 | Retain: access declaration is absent or malformed | `dispatcher.access_invalid` — “Access declaration is missing or invalid for {access_kind} `{interface_id}`.” | Default. | `direct_authorization.py:95,119` |
| D29 | Split: source/gateway relative path is unsafe | `dispatcher.unsafe_blueprint_path` — “The source `{field_name}` is not a safe module-relative path.” | Validated module ID may be context; never echo source ID/path. | `direct_authorization.py:154,214,254` |
| D30 | Split: source path cannot be inspected | `dispatcher.source_not_found` — “The dispatcher could not inspect the source path for module `{module_id}`.” | No cause; never publish path. | `direct_authorization.py:166` |
| D31 | Split: source path contains symlink or is not regular | `dispatcher.unsafe_blueprint_path` — “The source for module `{module_id}` {reason}.” | `reason`: `has a path containing a symbolic link` or `is not a regular file`. | `direct_authorization.py:180,186` |
| D32 | Retain: source locator has invalid shape/base | `dispatcher.source_locator_invalid` — “The source locator has {reason}.” | `reason`: `an invalid shape` or `an unsupported base`; validated module ID only. | `direct_authorization.py:201,207` |
| D33 | Retain: source blueprint cannot be decoded | `dispatcher.blueprint_malformed` — “The source blueprint could not be read as YAML.” | No cause; validated module ID only. | `direct_authorization.py:220` |
| D34 | Retain: source schema/kind is wrong | `dispatcher.blueprint_schema_mismatch` — “Direct dispatch requires a schema-6 behavioral source blueprint.” | Validated module ID only. | `direct_authorization.py:231` |
| D35 | Retain: source identity differs | `dispatcher.blueprint_identity_mismatch` — “Source blueprint identity does not match its registered source.” | Validated module ID only. | `direct_authorization.py:241` |
| D36 | Retain: source gateway/version is absent or invalid | `dispatcher.blueprint_malformed` — “The source blueprint has {reason}.” | `reason`: `no gateway declaration` or `an invalid version`; validated module ID only. | `direct_authorization.py:247,259` |
| D37 | Retain: host caller is not discoverable | `dispatcher.host_caller_invalid` — “Host caller `{caller_module_id}` is not a discoverable top-level skill.” | Default. | `direct_authorization.py:310` |
| D38 | Retain: requested interface is absent or owned elsewhere | `dispatcher.interface_not_found` — “Interface `{interface_id}` {reason}.” | `reason`: `was not found` or `is not owned by {target_module_id}`. | `direct_authorization.py:359,367` |
| D39 | Retain: source-interface relation is invalid | `dispatcher.source_interface_invalid` — “Source-interface declaration for `{interface_id}` {reason}.” | Allowlisted reason: invalid, absent, wrong owner, or invalid version. | `direct_authorization.py:374,381,390,400` |
| D40 | Retain: requested and available versions differ | `dispatcher.interface_version_mismatch` — “Version mismatch for `{interface_id}`: requested {requested_version}, available {available_version}.” | Default. | `direct_authorization.py:407` |
| D41 | Retain: namespace route is absent | `dispatcher.namespace_route_missing` — “Namespace export `{route_owner_module_id}->{child_segment}` is missing.” | Default. | `direct_authorization.py:470` |
| D42 | Retain: namespace and child versions disagree | `dispatcher.namespace_version_mismatch` — “Namespace version does not match child `{child_module_id}`.” | Default. | `direct_authorization.py:481` |
| D43 | Retain: namespace surface omits interface/version | `dispatcher.namespace_surface_excludes_interface` — “Namespace surface excludes `{interface_id}@{interface_version}`.” | Default. | `direct_authorization.py:495` |
| D44 | Retain: namespace access map is malformed | `dispatcher.access_invalid` — “Namespace route `{route_owner_module_id}` has invalid `interface_access`.” | Default. | `direct_authorization.py:525` |
| D45 | Retain: argument/binding compilation fails | `dispatcher.resolution_failed` — “The dispatcher could not compile arguments for `{interface_id}`.” | No cause. | `direct_authorization.py:651` |
| D46 | Retain: Python target construction fails | `dispatcher.resolution_failed` — “The dispatcher could not construct the Python target for `{interface_id}`.” | No cause. | `direct_authorization.py:677` |
| D47 | Retain: CLI target lacks `.interface.` qualification | `dispatcher.invalid_request` — “Target must be a fully qualified `<module>.interface.<name>` export.” | Default. | `cli.py:105` |
| D48 | Contain: argparse rejects a CLI field | `dispatcher.invalid_request` — “The dispatcher CLI request does not match the declared command-line signature.” | JSON mode must use the structured carrier; never echo supplied values. | `cli.py:48-114` |

Declared dispatcher classes with no scoped producer are not catalogue entries
and receive no invented message: `BlueprintInvalidError`,
`ModuleNotCallableError`, `ExportAccessMissingError`,
`CertificationRejectedError`, `UnsupportedLanguageError`,
`GatewayOutsideModuleError`, and `RuntimeInvalidError`.

## Dispatcher: MCP and manager boundary

| ID | Disposition and `emit_when` | Public code and exact message | Non-default policy and forbidden implication | Producers |
| --- | --- | --- | --- | --- |
| D49 | Replace: MCP Python is older than 3.11 | `dispatcher.mcp_python_unsupported` — “Famulus MCP startup requires Python 3.11 or newer; running {major}.{minor}.” | No clue: the message already states the complete fact. | `mcp_server.py:69` |
| D50 | Split: plugin-data root/child fails safety checks | `dispatcher.mcp_persistence_invalid` — “Famulus MCP startup rejected an unsafe plugin-data {kind}.” | `kind`: root or child directory. No path. Must not imply setup failure. | `mcp_server.py:74-132` |
| D51 | Replace: persistence cannot initialize | `dispatcher.mcp_persistence_invalid` — “Famulus MCP startup could not initialize plugin persistence.” | No cause; must not imply missing setup. | `mcp_server.py:100-132` |
| D52 | Contain: declared MCP server package cannot import | `dispatcher.mcp_package_unavailable` — “Famulus MCP startup could not import its declared server package `{module_name}`.” | Validated declared module name only; no clue because the message is conclusive. | `mcp_server.py:443-449` |
| D53 | Contain: server initialization or execution raises | `dispatcher.mcp_server_failed` — “Famulus MCP server initialization or execution failed.” | No chronology or cause; must not infer package/Python failure. | `mcp_server.py:799-815` |
| D54 | Replace: ordered form also supplies positionals | `dispatcher.invalid_request` — “MCP ordered arguments require `positionals=[]`.” | Default. | `mcp_server.py:139` |
| D55 | Replace: compact option contains a list | `dispatcher.invalid_request` — “MCP compact option values must be strings or `true`, not lists.” | No clue or usage hint. | `mcp_server.py:145` |
| D56 | Replace: manager cannot resolve/start, invocation raises a typed dispatcher error before yielding a manager payload, or manager returns nonzero process status without a syntactically valid JSON object | `dispatcher.manager_invocation_failed` — “The dispatcher could not obtain a valid `{operation}` result from the setup manager.” | Cause: any caught typed dispatcher failure that maps to a dispatcher row (D or R) and passes that row's context policy; none for untyped crash/exit. Must not imply setup required, missing package, or bootstrap. | `mcp_server.py:155-190` |
| D57 | Replace: manager returns zero process status with malformed JSON or non-object JSON | `dispatcher.manager_response_malformed` — “The setup manager returned no valid JSON object for `{operation}`.” | Default; do not echo output. | `mcp_server.py:176-189` |
| D58 | Replace: manager returns a syntactically valid JSON object that fails all variant validation or contradicts operation/exit | `dispatcher.manager_response_invalid` — “The setup manager `{operation}` response is invalid for that operation or process status.” | Must not imply manager runtime failure. | adapter at `mcp_server.py:155` |
| D59 | Replace: status pending stack has wrong container/item/field shape or duplicate setup-interface entries | `dispatcher.manager_response_invalid` — “The setup manager `status` response contains an invalid `pending_stack`.” | Safe context may contain allowlisted failed invariant, not returned values. | `mcp_server.py:225-252` plus uniqueness validation |
| D60 | Replace: ready authorization lacks required confirmation | `dispatcher.manager_response_invalid` — “The setup manager `authorize` response did not confirm `state=ready` and `resume_original=true`.” | Must not invent intentional refusal. | `mcp_server.py:264-279` |
| D61 | Replace: `setup_required` lacks root/stack | `dispatcher.manager_response_invalid` — “The setup manager `status` result `setup_required` lacked a nonempty root or pending stack.” | Default. | `mcp_server.py:280-303` |
| D62 | Replace: `setup_busy` lacks flow ID | `dispatcher.manager_response_invalid` — “The setup manager `status` result `setup_busy` lacked a nonempty `flow_id`.” | Default. | `mcp_server.py:304-319` |
| D63 | Replace: status code is unsupported | `dispatcher.manager_response_invalid` — “The setup manager `status` response contains an unsupported code.” | Never echo raw unvalidated value. | `mcp_server.py:320` |
| D64 | Replace: valid setup diagnosis is returned internally | `dispatcher.manager_operation_failed` — “The setup manager `{operation}` failed: {setup_error}” | Map validated `setup_error_code`; preserve only the cause permitted by the matched E-row and its exact clues. Never infer an additional clue/recovery. | current rejection at `mcp_server.py:170` |
| D65 | Replace: setup flow ID is combined with dry-run/manager target | `dispatcher.invalid_request` — “MCP request field `setup_flow_id` cannot be used with `dry_run` or a setup-manager target.” | Default. | `mcp_server.py:336` |
| D66 | Split: managed-setup projection load fails | `dispatcher.setup_projection_unavailable` — “MCP preflight could not evaluate the managed-setup projection for `{interface_id}`.” | Cause: any caught `DirectBlueprintError` that maps to a D-row and passes that row's context policy; otherwise none. Must not imply setup or bootstrap. | `mcp_server.py:384-395` |
| D68 | Contain: unstructured `InvocationError` reaches MCP | `dispatcher.error` — “The dispatcher request failed at an unclassified invocation boundary.” | Invariant breach; no arbitrary exception text. | `mcp_server.py:438-440` |
| D69 | Contain: dispatched process exceeds its requested timeout | `dispatcher.execution_timeout` — “The dispatched process for `{interface_id}` exceeded its timeout; completion is unknown.” | Validated timeout may be context; no retry or side-effect claim. | `direct_runtime.py:415-503`; `python_machine_interface.py:1062-1123` |
| D70 | Contain: a `check=true` process has nonzero status and requested text decoding, if any, did not fail | `dispatcher.checked_process_failed` — “The dispatched process for `{interface_id}` returned nonzero process status {returncode}; checked execution treated the result as an error.” | The process ran and may have side effects; no cause/recovery. | same execution paths, caught `CalledProcessError` |
| D71 | Contain: captured output cannot decode as requested text | `dispatcher.output_decode_failed` — “The dispatcher could not decode captured output from `{interface_id}` as text.” | No raw bytes, encoding guess, or completion claim. | same execution paths, caught `UnicodeDecodeError` |

## Dispatcher: Python runner boundary

These messages apply only when the failure is carried through a registered
private runner diagnosis. A normal target nonzero exit remains the target's
result and is not reclassified as a dispatcher error.

The dispatcher creates a private diagnostic pipe and passes its writer as
`--diagnostic-writer TOKEN` before the gateway operands. On POSIX the token is
an fd inherited with `pass_fds`. On Windows it is a handle passed as the only
`STARTUPINFO` `handle_list` entry with `close_fds=true`; the child immediately
clears handle inheritance and converts it to an owned CRT fd. A module-global
lock covers the parent's make-inheritable, `Popen`, restore, and close window;
future repository-owned broad-inheritance Windows launches must share it.
Launch uses `Popen`; the parent closes its writer immediately, retains at most 16 KiB while
continuing to drain/discard to EOF, and marks overflow invalid. The runner
parses and removes the private option before interface argv exists, writes at
most one compact registry payload, closes the descriptor, and exits 70. On
launch failure the parent closes both ends. Diagnostic reads are interruptible
or nonblocking; no reader join is unbounded. On timeout the parent terminates
the process, waits for a bounded grace period, then kills and finally waits if
necessary; it closes the reader and performs only a bounded final join. Once
the deadline expires, cleanup completes and D69 wins: no later runner
diagnosis, D71, or D70 is accepted. For a non-timeout completion, the dispatcher
collects ordinary output as bytes and first validates any complete exit-70
diagnosis. An accepted diagnosis becomes the registered dispatcher error. Only
when none is accepted does processing fall back to ordinary subprocess
semantics: requested text decoding runs before `check`, so D71 precedes D70
when both predicates would otherwise apply. Multiple records or trailing bytes
invalidate the diagnosis. This is the sole transport for R01-R25. Because the
dispatcher constructs the option, malformed
`--diagnostic-writer` is an internal invariant failure and is not diagnosed through
that same descriptor.

All R rows have disposition `Contain`: they add the private structured runner
boundary while preserving ordinary target results.

| ID | `emit_when` | Public code and exact message | Policy | Producers |
| --- | --- | --- | --- | --- |
| R01 | Gateway/entry operands absent | `dispatcher.runner_request_invalid` — “Python interface runner requires a gateway path and process entry.” | Default. | `python_machine_interface_runner.py:781,920` |
| R02 | Private option lacks operands | `dispatcher.runner_request_invalid` — “Python interface runner option `{option}` is missing required arguments.” | Default. | `:814` |
| R03 | Singleton private option repeats | `dispatcher.runner_request_invalid` — “Python interface runner option `{option}` was supplied more than once.” | Default. | `:820` |
| R04 | Descriptor is non-integer | `dispatcher.runner_request_invalid` — “Python interface runner option `{option}` requires an integer descriptor.” | Default. | `:880` |
| R05 | Snapshot path/digest are unpaired | `dispatcher.runner_request_invalid` — “Python interface runner requires package snapshot path and SHA-256 together.” | Default. | `:889` |
| R06 | Logical package/entry point are unpaired | `dispatcher.runner_request_invalid` — “Python interface runner requires logical package and entry point together.” | Default. | `:895` |
| R07 | Physical prefix lacks logical package or is unsafe | `dispatcher.runner_request_invalid` — “Python interface runner physical package prefix is invalid for this request.” | Default; no path. | `:901,907` |
| R08 | Snapshot and descriptor transports are mixed | `dispatcher.runner_request_invalid` — “Python interface runner cannot combine package snapshot and descriptor transports.” | Default. | `:914` |
| R09 | Confined root lacks identity or differs from working directory | `dispatcher.runner_request_invalid` — “Python interface runner confined-root request is inconsistent.” | Default; no paths. | `:985,989` |
| R10 | Gateway/process metadata is noncanonical | `dispatcher.runner_target_invalid` — “Python interface runner received invalid gateway or process-entry metadata.” | No cause. | `:647` |
| R11 | Bound source escapes root, duplicates a module, or is not regular | `dispatcher.runner_source_invalid` — “Python interface runner received an invalid bound package source.” | Allowlisted reason context; no path. | `:199,243,267,439,506` |
| R12 | Snapshot digest/payload/read validation fails | `dispatcher.runner_snapshot_invalid` — “Python interface runner rejected its package snapshot: {reason}.” | Allowlisted reason; no cause or path. | `:292-313` |
| R13 | Gateway source cannot be read or lies outside validated package | `dispatcher.runner_source_invalid` — “Python interface runner could not load the gateway source within its validated package boundary.” | No cause or path. | `:455,461,518,540` |
| R14 | Confined import is outside the snapshot/root, unsafe, invalid, ambiguous, uninspectable, or mutates `sys.path` | `dispatcher.runner_import_rejected` — “Python interface runner rejected a confined import: {reason}.” | Allowlisted reason enum only; no module or path. The machine interface did not load. | `:66-164` |
| R15 | Gateway import/execute raises | `dispatcher.runner_import_failed` — “Python interface runner could not load the resolved gateway module.” | No cause; request execution did not begin. | `:529,945` |
| R16 | Interface entry is absent or wrong type | `dispatcher.runner_interface_invalid` — “Resolved Python gateway has an invalid machine-interface entry: {reason}.” | Allowlisted reason; no value representation. | `:687-702` |
| R17 | Interface parser rejects arguments | `dispatcher.invalid_request` — “The Python interface request does not match the declared interface signature.” | Never echo supplied values; no runner/setup implication. | `:743` |
| R18 | Route-smoke callback raises | `dispatcher.runner_route_smoke_failed` — “Python machine-interface route-smoke execution failed.” | No cause. | `:743` |
| R19 | Interface execution raises before return | `dispatcher.runner_execution_failed` — “Python machine-interface execution failed before returning an exit code; completion is unknown.” | No cause. | `:747` |
| R20 | Interface returns unsupported value | `dispatcher.runner_interface_invalid` — “Python machine interface returned an unsupported result type.” | Default; no value representation. | `python_machine_interface.py:1156` |
| R21 | Package snapshot construction or source confinement fails | `dispatcher.runner_source_invalid` — “Python interface runner could not construct a validated package-source snapshot.” | No cause or physical path. | `python_machine_interface_runner.py:470-501` |
| R22 | Lazy-confined load lacks logical identity | `dispatcher.runner_target_invalid` — “Python interface runner received a confined gateway without a logical entry point.” | The machine interface did not load. | `:672-676` |
| R23 | Interface constructor raises | `dispatcher.runner_interface_initialization_failed` — “Python interface constructor failed before request handling began.” | No cause or traceback. | `:687-697` |
| R24 | `build_parser()` raises or returns the wrong type | `dispatcher.runner_interface_initialization_failed` — “Python machine interface did not provide a valid argument parser.” | No cause; request execution did not begin. | `:739-742` |
| R25 | Custom `parse_args()` raises outside normal argparse rejection | `dispatcher.runner_request_validation_failed` — “Python machine interface failed while validating request arguments.” | No cause; request execution did not begin. | `:747-748` |

## Setup: statuses and successful flow results

These are not errors and have no message, cause, clues, or recovery unless the
row explicitly says otherwise.

All S rows have disposition `Retain`.

| ID | `emit_when` | Result | Safe meaning and forbidden implication | Producers |
| --- | --- | --- | --- | --- |
| S01 | Target has no managed owner | status `unmanaged` | No managed setup applies; do not imply ready, missing, or broken. | `_setup_evaluation.py:127-144` |
| S02 | Closure receipts are current and no flow is active | status `ready` | Setup evaluation is ready; authorization has not necessarily succeeded. | `:136-144` |
| S03 | First noncurrent receipt yields a pending closure | status `setup_required` | Exact root/LIFO stack only; do not infer why receipt is absent or that execution began. | `:136-143` |
| S04 | Any active flow exists | status `setup_busy` | Flow ID only; do not imply ownership, failure, or recovery action. | `:132-135` |
| S05a | Operation has no external work | flow `ready` | Operation completed without an external step; `resume_original=false`. | `_setup_manager.py:451-466` |
| S05b | Final settlement clears a setup/teardown flow | flow `ready` | Current step settled and `resume_original=false`. Claim external verification only when a verifier returned true; otherwise record only the zero action status or submitted Markdown completion. Do not generalize teardown completion to setup readiness. | `_setup_manager.py:451-466,795-815,844-868` |
| S05c | Ready-target authorization atomically claims receipts | flow `ready` | Authorization completed and `resume_original=true`; no setup action ran. | `_setup_manager.py:593-605` |
| S05d | Authorized cancellation clears a flow | flow `ready` | Flow was cancelled and `resume_original=false`; do not imply the setup/teardown operation completed. | `_setup_manager.py:1006-1078` |
| S05e | `invalidate` atomically removes selected stored receipts | flow `ready` | `removed` lists only receipts actually removed; do not imply cancellation, external teardown, or setup readiness. | `_setup_manager.py:883-906` |
| S06 | Flow advances to external step | flow `run-step` | Exact next step; do not imply it ran. | `:451-466` |
| S07 | Markdown instructions are returned | flow `awaiting-settlement` | Reviewed instructions; do not imply execution or verification. | `:755-772` |
| S08 | Helper is allowlisted for current Markdown step | flow `authorized-markdown-call` | Authorization only; do not imply helper execution or settlement. | `:715-747` |

## Setup: failures

All E rows have disposition `Replace`: they replace arbitrary exception text,
generic codes, or incorrect status/flow classification with the stated
contract.

`Fresh flow recovery` below means recovery is emitted only after a new
canonical ledger read reconstructs the same flow, step, and verified owner;
otherwise the result is `failed` and operator-only.

| ID | `emit_when` | Public code and exact message | Non-default policy and forbidden implication | Producers |
| --- | --- | --- | --- | --- |
| E00 | Known active flow blocks `begin`, `teardown-all`, or `invalidate` | flow `state=busy`, `error_code=setup.flow_busy` — “Another managed setup flow is active.” | Passive authorized context only; no recovery actions or ownership implication. | `_setup_manager.py:530-537,629-639,883-894` |
| E01 | Manager argv violates interface signature | `setup.request_invalid` — “The setup-manager request does not match the declared interface signature.” | Exit 64; must not imply broken setup/runtime. | `_setup_manager.py:191-198,1102-1128` |
| E02 | Action input is not one JSON object | `setup.arguments_not_object` — “Setup action input must be one JSON object.” | No supplied value. | `:201-209` |
| E03 | Input fields are missing/undeclared | `setup.arguments_shape_invalid` — “Setup action input has missing required fields or undeclared fields.” | Do not list private names/values. | `:210-218` |
| E04 | Value is not supported scalar | `setup.argument_type_invalid` — “Declared setup arguments must be string, integer, or Boolean JSON values.” | No value. | `:221-226` |
| E05 | Positional value is Boolean | `setup.positional_argument_invalid` — “Positional setup arguments cannot be Boolean.” | Must not imply execution began. | `:227-230` |
| E06 | Optional positionals leave a gap | `setup.positional_arguments_noncontiguous` — “Optional positional setup arguments cannot leave gaps.” | No omitted values. | `:238-240` |
| E08 | Repository configuration context is absent | `setup.repository_configuration_missing` — “The setup manager received no repository configuration.” | No clue: the message is conclusive. Must not imply dispatcher-runtime bootstrap or a `setup_required` state. | `:1218-1225` |
| E09 | Repository configuration fails validation | `setup.repository_configuration_invalid` — “The repository configuration is invalid.” | No cause. | `:1222-1235` |
| E10 | Managed graph metadata is invalid, including module-parent cycles or a ready target without an owner | `setup.graph_invalid` — “Managed-setup metadata is invalid.” | Cause: any caught `DirectBlueprintError` that maps to a D-row and passes its context policy; otherwise none. Not unreadability/setup required. | `:1130-1138,1222-1235`; `_setup_evaluation.py:91-112,147-173` |
| E11 | Graph/config read raises `OSError` other than permission denial | `setup.graph_read_failed` — “Managed-setup metadata could not be read.” | No cause or clue. | same loaders |
| E11p | Graph/config read is denied | `setup.permission_denied` — “The setup manager was denied permission to access managed-setup metadata.” | Match only when the caught exception itself, its direct `__cause__`, or that cause's direct `__cause__` is `PermissionError`. Never inspect `__context__`, message text, or errno/winerror categories. No path, clue, or recovery. | same loaders |
| E12 | Before flow persistence, required binding is absent | `setup.binding_missing` — “A required managed setup interface has no declared runtime binding.” | Flow `failed`; affected validated interface may be context; operator only; no action ran. Existing-flow cases use E32/E33. | `:261-266` |
| E13 | Before flow persistence, binding differs from metadata | `setup.binding_mismatch` — “The declared setup runtime binding does not match the live managed metadata.” | Flow `failed`; operator only; no package/ledger inference. Existing-flow cases use E32/E33. | `:266-278` |
| E14 | Static runtime declarations conflict | `setup.initialization_invalid` — “Managed-setup runtime declarations are inconsistent.” | Must not imply state/user/package failure. | `_setup_dispatches.py:65-149,407-447` |
| E15 | Atomic-ledger capability absent | `setup.storage_capability_missing` — “The setup manager has no configured atomic-ledger capability.” | Operator; no clue because the message is conclusive. | `_setup_state.py:350-356` |
| E16 | Fixed path getter dispatch raises typed error | `setup.status_path_dispatch_failed` — “The setup-status path lookup dispatch failed; no ledger path was obtained.” | Cause only when the typed failure maps to a dispatcher row (D or R); otherwise none. No clue or recovery. | `_setup_manager.py:1143-1148` |
| E17 | Path getter returns a valid `CompletedProcess` with nonzero process status | `setup.status_path_process_failed` — “The setup-status path lookup returned nonzero process status {returncode}.” | Return-code context; no child-output clue/recovery. | `:1149-1150` |
| E18 | Getter result is not a `CompletedProcess` | `setup.status_path_result_invalid` — “The setup-status path lookup returned an invalid process result.” | Evaluate before E17; must not imply execution/failure. | `:1149-1150` |
| E19 | Getter response is nontext or not one absolute path | `setup.status_path_response_invalid` — “The setup-status path lookup did not return {expected}.” | `expected`: `text` or `one absolute path`; never returned text/path. | `:1151-1158` |
| E20 | Ledger path/access fails safely for a reason other than permission denial | `setup.ledger_access_failed` — “Managed-setup state could not be accessed safely.” | No cause, clue, or path. | `_setup_state.py:371-388` |
| E20p | Ledger access is denied | `setup.permission_denied` — “The setup manager was denied permission to access its state ledger.” | Match only when the caught exception itself, its direct `__cause__`, or that cause's direct `__cause__` is `PermissionError`. Never inspect `__context__`, message text, or errno/winerror categories. No path, clue, or recovery. | `_setup_state.py:371-388`, retained nested cause |
| E21 | Ledger bytes violate canonical schema | `setup.ledger_invalid` — “Managed-setup state is not valid canonical ledger data.” | Operator; never `setup_busy` or ordinary recovery. | `:79-273`; status catch `_setup_manager.py:560-580` |
| E22 | CAS predecessor changes/retries exhaust | `setup.ledger_conflict` — “Managed-setup state changed while this operation was updating it.” | Clue, predicate `predecessor changed or bounded CAS retries exhausted`: “Another setup-manager process may have updated the ledger concurrently.” No corruption implication. | `_setup_state.py:420-449` |
| E23 | Post-write reread differs | `setup.ledger_write_uncertain` — “The final managed-setup ledger state could not be confirmed after writing.” | Completion unknown. Flow recovery only after a fresh canonical reread reconstructs the same live flow/step and verified owner; otherwise operator. | `:390-402` |
| E24 | Required live flow is absent | `setup.flow_not_found` — “No active managed setup flow matches this request.” | No flow/recovery; not busy/stale/completed. | `_setup_manager.py:286,722` |
| E25 | Supplied flow ID or requested flow identity differs | `setup.flow_mismatch` — “The request does not match the active managed setup flow.” | Do not expose the other flow or offer recovery. | `_setup_manager.py:724,914`; `_setup_evaluation.py:176-182` |
| E26 | Operation is invalid for the current step kind/action | `setup.operation_not_allowed` — “This operation is not allowed for the active managed setup step.” | Authorized step context only; not runtime/completion failure. | `_setup_manager.py:325-327,733-737,759,786,794,850-852` |
| E27 | Supplied root is unmanaged | `setup.root_not_managed` — “The requested root is not a managed setup interface.” | Must not imply incomplete setup. | `:640-641` |
| E28 | Begin evaluation is not actionable | `setup.begin_state_invalid` — “Managed setup cannot begin from the current evaluated state.” | Busy remains separately validated busy; no graph/runtime inference. | `:642-653` |
| E29 | Ready authorization races to required | `setup.target_not_ready` — “The target requires setup before it can be authorized.” | Exact evaluated next step may be context; not action/authorization failure. | `:592-613` |
| E30 | Ready authorization races to busy | `setup.flow_busy` — “Another managed setup flow became active before authorization completed.” | Passive flow only; no ownership/recovery implication. | `:592-613` |
| E31 | State changes before begin commits | `setup.begin_conflict` — “Managed setup state changed before the new flow could begin.” | Clue, predicate `atomic begin observes a changed evaluation or active flow`: “Another setup-manager operation may have changed the managed state concurrently.” No flow/action began. | `:685-701` |
| E32 | Active setup flow/current binding conflicts with metadata or receipts | `setup.active_flow_stale` — “The active setup flow no longer matches live state: {mismatch_subject}.” | Allowlisted subject: metadata, receipts, current step, or binding. Operator only: ordinary recovery cannot reconstruct it. | `_setup_evaluation.py:185-225`; `_setup_manager.py:727,731` |
| E33 | Active teardown flow/current binding conflicts with graph or plan | `setup.active_flow_stale` — “The active teardown flow no longer matches live state: {mismatch_subject}.” | Allowlisted subject: metadata, plan, current step, or binding. Operator only; completion unknown. | `_setup_manager.py:289,295,306,308` |
| E34 | Stored receipts conflict with the live graph and prevent plan reconstruction | `setup.ledger_graph_mismatch` — “Stored setup receipts do not match the live managed-setup metadata.” | Operator only; ordinary recovery cannot reconstruct the plan. | `_setup_evaluation.py:272-286` |
| E35 | After a transition is recorded, its returned next state lacks the required persisted flow/step | `setup.transition_state_invalid` — “The managed setup transition did not produce the required persisted next state.” | Exclusive to post-record result invariants. Recovery only after fresh reconstruction of the same owned flow/step; previous action completion unknown. | `_setup_manager.py:396-446`; `_setup_evaluation.py:242-245,369-372` |
| E35a | Ready-target authorization produces no evaluated result | `setup.authorization_state_invalid` — “Managed setup authorization did not produce an evaluated result.” | Flow `failed`; no flow/recovery; no setup action ran. | `_setup_evaluation.py:147-173` |
| E36 | Active flow changes before teardown or cancellation is applied | `setup.active_flow_changed` — “The active managed setup flow changed before the requested operation could be applied.” | Recovery only after fresh reconstruction of the same owned flow/step; no actor/completion inference. | `_setup_manager.py:490-501,977-1069` |
| E37 | An action call to shared dispatch raises typed dispatcher error | `setup.action_dispatch_failed` — “The managed setup action dispatch failed; action completion is unknown.” | Cause only when the typed failure maps to a dispatcher row (D or R); otherwise none. Fresh flow recovery; no inferred runtime cause. | `_dispatch_result:342-350`; action call sites `:510-514,795-803` |
| E38 | An action call to shared dispatch returns an invalid result type | `setup.action_result_invalid` — “The managed setup action dispatch returned an invalid process result; action completion is unknown.” | Fresh flow recovery; no cause. | `_dispatch_result:351-353`; action call sites `:510-514,795-803` |
| E39 | Action returns nonzero process status | `setup.action_failed` — “The managed {operation} action for `{interface}@{version}` returned nonzero process status {returncode}.” | Fresh flow recovery; no child-output clue. Side effects may have occurred. | `:510-514,795-803` |
| E40 | Verifier returns nonzero process status, including during retry/cancel | `setup.verifier_failed` — “The verifier for `{interface}@{version}` returned nonzero process status {returncode}; the managed step's completion is unknown.” | Fresh flow recovery; not a reported false result. | `:355-371,924-932,977-982` |
| E41 | Verifier returns exact false | `setup.verification_incomplete` — “The verifier reported that `{interface}@{version}` is incomplete.” | Fresh flow recovery; not crash/malformed. | `:378-382` |
| E42 | Verifier JSON/shape is invalid | `setup.verifier_response_invalid` — “The verifier returned {reason}; the managed step's completion is unknown.” | `reason`: `malformed JSON` or `an unsupported response`; fresh flow recovery. | `:372-383` |
| E44 | Verifier confirms completion, then recording settlement raises | `setup.settlement_failed` — “The verifier confirmed external completion, but the setup manager could not record settlement.” | Cause only from E10, E20, E20p, E21-E23, E25, or E32-E34. Fresh flow recovery; causes preventing reconstruction are operator-only. | `:401-405,510-521,804-815,853-868,924-939,984-989` |
| E45 | Verifier confirms incomplete state, then recording cancellation raises | `setup.cancellation_failed` — “The verifier reported the current step incomplete, but the setup manager could not cancel the flow.” | Cause only from E20, E20p, E21-E23, E25, E32-E34, or E36. Fresh flow recovery. | `:977-1005` |
| E46 | No verifier result establishes completion before recording cancellation raises | `setup.cancellation_failed` — “The setup manager could not cancel the flow; external completion remains unknown.” | Covers absent and deliberately uninvoked verifiers. Cause only from E20, E20p, E21-E23, E25, E32-E34, or E36. Fresh flow recovery. | `:977-1069` |
| E47 | Runtime dispatch key/context declaration is inconsistent | `setup.dispatch_declaration_invalid` — “The managed setup runtime dispatch declaration is inconsistent.” | Flow `failed`; operator only. Must not imply missing setup, package, or bootstrap. | `_setup_manager.py:342-353`; `python_machine_interface.py:1062-1115` |
| E48 | Runtime caller differs from continuation caller supplied to `begin` | `setup.continuation_caller_mismatch` — “The runtime caller does not match the continuation caller supplied to `begin`.” | Flow `failed`; no flow, clue, recovery, or other caller identity. | new validation in `_setup_manager.py:615-683` |
| E49 | Active flow lacks new-schema verified ownership for ordinary recovery | `setup.recovery_owner_unverified` — “The active flow has no verified owner for ordinary recovery.” | Flow `failed`; operator only; do not expose another caller identity. | new validation before `_setup_manager.py:907-915` |
| E50 | `teardown-all` encounters a managed binding declaring arguments | `setup.teardown_all_binding_invalid` — “Global teardown cannot process a managed binding that declares arguments.” | Flow `failed`; operator only. Must not imply an external teardown action, setup, bootstrap, or retry. | `_setup_manager.py:538-545` |
| E51 | Action exits zero without verifier, then recording settlement raises | `setup.settlement_failed` — “The managed action exited successfully, but the setup manager could not record settlement.” | External state was not independently verified. Cause only from E10, E20, E20p, E21-E23, E25, or E32-E34. Fresh flow recovery. | `:394-407,510-521,795-815` |
| E52 | Markdown step is submitted complete without verifier, then recording settlement raises | `setup.settlement_failed` — “The setup manager could not record settlement after the current step was submitted as complete.” | Do not claim verified external completion. Cause only from E10, E20, E20p, E21-E23, E25, or E32-E34. Fresh flow recovery. | `:394-407,844-868` |
| E53 | A verifier call to shared dispatch raises typed dispatcher error | `setup.verifier_dispatch_failed` — “The verifier dispatch failed; the managed step's completion is unknown.” | Cause only when the typed failure maps to a dispatcher row (D or R); otherwise none. Fresh flow recovery. | `_dispatch_result:342-350`; verifier call `:355-370` |
| E54 | A verifier call to shared dispatch returns an invalid result type | `setup.verifier_result_invalid` — “The verifier dispatch returned an invalid process result; the managed step's completion is unknown.” | No cause. Recovery only after fresh reconstruction of the same owned flow/step. | `_dispatch_result:351-353`; verifier call `:355-370` |

## Boundary translation

Manager-response classification first parses JSON, then validates exactly one
status or flow-shaped variant before judging its process status. Status and
internal-preflight failures use the existing flow-shaped `state=failed`
envelope; there is no bare-diagnosis variant.
D59-D63 cover their exact object predicates. Every other syntactically valid
JSON object is D58, including an object that matches no variant or contradicts
its operation/exit pairing. Without a valid object, resolution/start failure or
nonzero process status is D56; malformed or non-object output with zero status
is D57. One response maps to the first matching row only.

| Input | Dispatcher output |
| --- | --- |
| Valid setup status | Preserve internally; MCP enriches only the documented status fields. |
| Valid direct lifecycle flow result | Canonically reserialize into existing `stdout`, preserve exit code, expose empty public `stderr`. |
| Valid flow-shaped setup diagnosis used internally | Emit D64. Map `error_code` to allowlisted `setup_error_code`, `error` into the factual outer message, setup clues to outer clues, and the reduced setup cause to the outer cause. |
| Valid non-success authorization flow used internally | Apply D64; never translate flow `state` into status `code`. A success-shaped but non-authorizing response is D58. |
| Malformed/contradictory manager response | Emit D57-D63 under the ordered classification above. |
| No syntactically valid JSON object and nonzero process status | Emit D56. |
| Recovery exception without a verified live flow | Emit the predicate-specific E code as flow `failed`; expose no recovery. |
| Bootstrap/ledger failure at any manager operation | Emit the predicate-specific E08-E23 code in the existing flow-shaped envelope with `state=failed`; expose no recovery. |

## Green audit

The message catalogue is green only when:

- every scoped public producer maps to exactly one row;
- every row is backed by an observable predicate and makes actor/operation
  unambiguous under the contract rule;
- rendered text distinguishes not started, failed, and completion unknown;
- no code spans incompatible meanings without a recorded split;
- every cause is confirmed and adds information;
- every clue has a named predicate, is labelled tentative, and gives no command;
- recovery targets the proven layer and is authorized;
- negative tests assert each material forbidden implication;
- text and JSON render the same semantic fields; and
- every displayed template has an exact contract test; byte-compatible
  behavior is required only when a row explicitly says so.
