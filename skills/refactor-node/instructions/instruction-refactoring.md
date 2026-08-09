# Instruction Refactoring

Use the validated instruction standard closure supplied by the router. Scoped
repository instructions remain authoritative when they are more specific.

Extend the router's preservation map before diagnosing the selected instruction
source:

- Map every directive that would change or disappear to authored guidance, a
  generated blueprint view, another public interface, or dead content. Generated
  text counts only when it supports a first-attempt-correct invocation.
- Record each affected route as predicate, branch outcome, fallback or recovery,
  and approval boundary. Do not turn a branch-specific rule into a universal one.
- Trace each affected cross-module directive from producer output through
  authorized consumer invocation and interpretation, with integration evidence
  from both owners.
- Treat generated usage, public identifiers, process-pattern names, dry-run
  plans, output fields, and ordering as behavior when users, callers, or tests can
  observe them.

Then compare authored and generated regions with all applicable rule assertions
and guidance. Diagnose concrete deviations with file evidence. Treat typed
examples as explanations, not independent rules. Use declared checks, tests,
assurances, and limitations to separate mechanical proof from semantic review.
Perform remaining semantic-review work explicitly. Prose-string assertions and
schema-invalid or unloadable fixtures are not behavioral evidence.

Propose the smallest ordered repairs that restore the desired behavior. For
each violated assertion, use its returned `remedied-by`
procedure; if none is returned, report the missing remedy and stop that repair.

Return the completed preservation map, applicable assertions, evidence and
limitations, proposed repairs, invariants, and verification to the router. For
removed content, state where the necessary behavior remains or why the content
was non-directive. The router owns approval, mutation order, diff inspection,
and stop-on-failure behavior.

On approved re-entry, apply exactly the approved move within this instruction
partition, run the selected verification, and return the exact diff and
verification evidence to the router.
