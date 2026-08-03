# Instruction Refactoring

Use the validated instruction standard closure supplied by the router. Scoped
repository instructions remain authoritative when they are more specific.

For the selected instruction source, compare authored and generated regions
with all applicable rule assertions and guidance from the imported base and
instruction standards. Diagnose concrete deviations with file evidence. Treat
typed examples as explanations, not independent rules; use declared checks,
tests, assurances, and limitations to distinguish mechanically proven
violations from semantic-review work and findings.

Propose the smallest ordered repairs that restore the desired behavior. For
each violated assertion, use its returned `remedied-by`
procedure; if none is returned, report the missing remedy and stop that repair.

Return the applicable assertions, evidence, proposed repairs, invariants, and
verification to the router. The router owns approval, mutation order, diff
inspection, and stop-on-failure behavior.

On approved re-entry, apply exactly the approved move within this instruction
partition, run the selected verification, and return the exact diff and
verification evidence to the router.
