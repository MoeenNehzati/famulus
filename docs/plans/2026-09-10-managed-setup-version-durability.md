# Managed setup version durability

## Cause and boundary

The centralized path cutover moved Cloud Files, Online Calendar, and Email
Client configuration without changing their managed setup versions, so preserved
v1 receipts falsely remained ready. Legacy configuration migration is out of
scope; existing shared Google credentials and service binders remain authoritative.

## Minimal repair

1. Publish setup v2 for the three affected services and update their finite
   setup-manager bindings.
2. Pin List Manager's Cloud Files prerequisite to v2.
3. Synchronize generated SKILL interface blocks.
4. Keep configuration paths centralized. The resolver's source-local contract,
   cross-platform path tests, exact-version evaluator tests, and production
   graph/binding parity make the setup-epoch obligation visible at its seams.

No refresh, ledger, OAuth, migration, or per-call verification code changes.

## Green gates

- Blueprint sync check; focused setup, refresh, and credential-binder tests.
- `python3 repo_checks.py --suite precommit --jobs 8`.
- The same three auditors approve the final diff.
- Post-source: non-reset refresh and fresh-process Todo, Calendar, and Gmail reads.
