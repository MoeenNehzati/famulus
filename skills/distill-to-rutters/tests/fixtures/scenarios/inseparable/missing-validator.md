# Inseparable contract missing its semantic validator

This mutation preserves the locally valid contract shape but removes the
validator binding required by the fixture oracle.

```distill-scenario-contract
scenario: inseparable
assignment:
  assignments:
    - part_id: part-review
      voyage_id: voyage-review
      rutter_definition_id: review
      charter_fields: [candidate]
      input_ids: [candidate]
      output_ids: [review-result]
      inseparability:
        status: inseparable
        reason: Inspection and decision share candidate state and transition semantics.
      independent_workflows: []
  orchestration:
    mode: single
    coordinator_rutter_id: null
    starts: []
    dependencies: []
    joins: []
    aggregate_results: []
    partial_failure: []
    retries: []
    retry_owner: review
    cancellation: []
    failure_propagation: []
    authorization: []
    release: []
graph:
  rutters:
    - rutter_id: review
      voyage_ids: [voyage-review]
      version: 1
      initial_evolution: inspect
      charter_fields: [candidate]
      evolutions:
        - evolution_id: inspect
          evolution_type: operation
          obligation_ids: [obl-review]
          decision_owner: rutter
          validator: validate_review
          outcomes: [accepted, malformed]
      transitions:
        - {from: inspect, outcome: accepted, to: complete}
        - {from: inspect, outcome: malformed, to: failed}
      terminal_results: [complete, failed]
logic:
  enforcement_matrix:
    - obligation_id: obl-review
      original_decision_owner: rutter
      automation_permission: deterministic
      public_runtime_capability: rutter.interface.bound-operations
      public_runtime_version: 1
      public_binding_contract_version: 1
      capability_verified: false
      capability_gap:
        absent_binding_contract: rutter.interface.bound-operations semantic_enforcement@1
        exact_repair: Expose versioned request, validation, transition, successor, and rejection-code bindings.
      owning_evolution: review/inspect
      exact_mechanism:
        enforcement_class: operation-name-only
        request: null
        validation:
          operation: validate
          input_ref: input
          evidence_ref: input.evidence
          validator_ref: null
          validator_binding_ref: binding.state.input_validator
          output_ref: result
        transition:
          operation: advance
          input_ref: input
          evidence_ref: input.evidence
          authority: owning-rutter-evolution
          owning_evolution_ref: binding.fix.current_state_id
          authority_ref: binding.state.next_state
          successor_ref: result
      precondition: The candidate is bound in the Charter.
      postcondition: Valid review evidence authorizes the declared successor.
      failure_result: Invalid evidence leaves the Fix unchanged.
      observable_evidence:
        operation: validate
        evidence_ref: input.evidence
        output_ref: result
      positive_trace:
        operation: advance
        input_ref: input
        evidence_ref: input.evidence
        outcome: accepted
        expected: {kind: successor, state: complete, result_ref: result}
      negative_trace:
        operation: validate
        input_ref: input
        evidence_ref: input.evidence
        outcome: malformed
        expected: {kind: rejection, rejection_code: invalid-review}
```
