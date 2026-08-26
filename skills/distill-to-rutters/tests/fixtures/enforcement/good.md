# Capability-checked owner decision

```distill-enforcement-fixture
logic_outcome: logic-gap
graph:
  rutters:
    - rutter_id: review
      voyage_ids: [voyage-main]
      version: 1
      initial_evolution: inspect
      charter_fields: [artifact]
      evolutions:
        - evolution_id: inspect
          evolution_type: operation
          obligation_ids: [obl-inspect]
          decision_owner: rutter
          validator: validate_inspection
          outcomes: [ready, failed]
        - evolution_id: request-approval
          evolution_type: owner-decision
          obligation_ids: [obl-approval]
          decision_owner: human
          validator: validate_human_approval
          outcomes: [approved, rejected]
      transitions:
        - {from: inspect, outcome: ready, to: request-approval}
        - {from: inspect, outcome: failed, to: failed}
        - {from: request-approval, outcome: approved, to: complete}
        - {from: request-approval, outcome: rejected, to: rejected}
      terminal_results: [complete, rejected, failed]
logic:
  enforcement_matrix:
    - obligation_id: obl-inspect
      original_decision_owner: rutter
      automation_permission: deterministic
      public_runtime_capability: rutter.interface.bound-operations
      public_runtime_version: 6
      public_binding_contract_version: 1
      capability_verified: false
      capability_gap:
        absent_binding_contract: rutter.interface.bound-operations semantic_enforcement@1
        exact_repair: Add semantic_enforcement version 1 to the public bound-operations contract with structural request, validation, transition, successor, and rejection-code bindings.
      owning_evolution: review/inspect
      exact_mechanism:
        enforcement_class: operation-name-only
        request: null
        validation:
          operation: validate
          input_ref: input
          evidence_ref: input.evidence
          validator_ref: validate_inspection
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
      precondition: artifact is bound in the Charter
      postcondition: inspection evidence authorizes one declared successor
      failure_result: invalid evidence leaves the Fix unchanged
      observable_evidence:
        operation: validate
        evidence_ref: input.evidence
        output_ref: result
      positive_trace:
        operation: advance
        input_ref: input
        evidence_ref: input.evidence
        outcome: ready
        expected: {kind: successor, state: request-approval, result_ref: result}
      negative_trace:
        operation: validate
        input_ref: input
        evidence_ref: input.evidence
        outcome: malformed
        expected: {kind: rejection, rejection_code: invalid-inspection-evidence}
    - obligation_id: obl-approval
      original_decision_owner: human
      automation_permission: request-owner-decision
      public_runtime_capability: rutter.interface.bound-operations
      public_runtime_version: 6
      public_binding_contract_version: 1
      capability_verified: false
      capability_gap:
        absent_binding_contract: rutter.interface.bound-operations semantic_enforcement@1
        exact_repair: Add semantic_enforcement version 1 to the public bound-operations contract with structural request, validation, transition, successor, and rejection-code bindings.
      owning_evolution: review/request-approval
      exact_mechanism:
        enforcement_class: operation-name-only
        request:
          operation: get-status
          owner: human
          owner_ref: result.requested_owner
          evidence_ref: input.evidence
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
      precondition: inspection completed
      postcondition: only validated human evidence selects approved or rejected
      failure_result: missing or invalid owner evidence leaves the Fix unchanged
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
