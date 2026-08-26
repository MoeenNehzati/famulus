# Missing validator false-success claim

```distill-enforcement-fixture
graph:
  rutters:
    - rutter_id: review
      voyage_ids: [voyage-main]
      version: 1
      initial_evolution: request-approval
      charter_fields: [artifact]
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
      automation_permission: request-owner-decision
      public_runtime_capability: rutter.interface.bound-operations
      public_runtime_version: 1
      public_binding_contract_version: 1
      capability_verified: true
      owning_evolution: review/request-approval
      exact_mechanism:
        enforcement_class: rutter-state-transition
        request:
          operation: get-instruction
          owner: human
          owner_ref: result.requested_owner
          evidence_ref: input.evidence
        validation:
          operation: null
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
      precondition: approval is required
      postcondition: approval would select a successor
      failure_result: no validator is available
      observable_evidence: {operation: validate, evidence_ref: input.evidence, output_ref: result}
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
        outcome: forged
        expected: {kind: rejection, rejection_code: validator-missing}
```
