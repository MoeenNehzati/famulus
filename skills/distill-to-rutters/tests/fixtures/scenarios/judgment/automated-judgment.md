# Human judgment replaced by automation

This mutation keeps the graph's human owner but removes the Rutter request and
claims deterministic automation instead.

```distill-scenario-contract
scenario: judgment
assignment:
  assignments:
    - part_id: part-approval
      voyage_id: voyage-approval
      rutter_definition_id: approval
      charter_fields: [candidate]
      input_ids: [candidate]
      output_ids: [approval-result]
      inseparability:
        status: inseparable
        reason: The request and validated transition share the candidate decision state.
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
    retry_owner: approval
    cancellation: []
    failure_propagation: []
    authorization: []
    release: []
graph:
  rutters:
    - rutter_id: approval
      voyage_ids: [voyage-approval]
      version: 1
      initial_evolution: request-approval
      charter_fields: [candidate]
      evolutions:
        - evolution_id: request-approval
          evolution_type: owner-decision
          obligation_ids: [obl-approval]
          decision_owner: human
          validator: validate_human_approval
          outcomes: [approved, rejected]
      transitions:
        - {from: request-approval, outcome: approved, to: complete}
        - {from: request-approval, outcome: rejected, to: rejected}
      terminal_results: [complete, rejected]
logic:
  enforcement_matrix:
    - obligation_id: obl-approval
      original_decision_owner: human
      automation_permission: deterministic
      public_runtime_capability: rutter.interface.bound-operations
      public_runtime_version: 1
      public_binding_contract_version: 1
      capability_verified: false
      capability_gap:
        absent_binding_contract: rutter.interface.bound-operations semantic_enforcement@1
        exact_repair: Expose versioned request, validation, transition, successor, and rejection-code bindings.
      owning_evolution: approval/request-approval
      exact_mechanism:
        enforcement_class: operation-name-only
        request: null
        validation:
          operation: validate
          input_ref: input
          evidence_ref: input.evidence
          validator_ref: validate_human_approval
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
      precondition: The candidate is ready for human review.
      postcondition: Automation substitutes for the human decision.
      failure_result: Missing evidence leaves the Fix unchanged.
      observable_evidence:
        operation: validate
        evidence_ref: input.evidence
        output_ref: result
      positive_trace:
        operation: advance
        input_ref: input
        evidence_ref: input.evidence
        outcome: approved
        expected: {kind: successor, state: complete, result_ref: result}
      negative_trace:
        operation: validate
        input_ref: input
        evidence_ref: input.evidence
        outcome: automated
        expected: {kind: rejection, rejection_code: owner-evidence-invalid}
```
