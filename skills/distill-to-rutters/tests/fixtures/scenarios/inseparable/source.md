# Inseparable review

Inspect one candidate and decide whether it satisfies the review rule. The
inspection and decision share the same candidate state, so they must remain in
one Rutter evolution. Malformed review evidence must be rejected by
`validate_review`; accepted evidence advances to `complete`.
