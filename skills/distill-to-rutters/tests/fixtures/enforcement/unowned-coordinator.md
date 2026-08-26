# Unowned coordinator decision false-success claim

```distill-enforcement-fixture
normative_obligations: [obl-join, obl-release]
assignment:
  assignments:
    - part_id: part-main
      voyage_id: voyage-main
      rutter_definition_id: coordinator
      charter_fields: [child-results]
      input_ids: [child-results]
      output_ids: [aggregate]
      inseparability: {status: independent, reason: Child voyages are independent.}
      independent_workflows: []
  orchestration:
    mode: coordinated
    coordinator_rutter_id: coordinator
    starts: []
    dependencies: []
    joins:
      - {obligation_id: obl-join, owning_transition: join, evidence: child results}
    aggregate_results: []
    partial_failure: []
    retries: []
    retry_owner: coordinator
    cancellation: []
    failure_propagation: []
    authorization: []
    release:
      - {obligation_id: obl-release, owning_transition: authorize-release, evidence: validated aggregate}
graph:
  rutters:
    - rutter_id: coordinator
      voyage_ids: [voyage-main]
      version: 1
      initial_evolution: join
      charter_fields: [child-results]
      evolutions:
        - evolution_id: join
          evolution_type: coordinator
          obligation_ids: [obl-join]
          decision_owner: rutter
          validator: validate_join
          outcomes: [ready, wait]
      transitions:
        - {from: join, outcome: ready, to: complete}
        - {from: join, outcome: wait, to: waiting}
      terminal_results: [complete, waiting]
logic:
  enforcement_matrix:
    - obligation_id: obl-join
      original_decision_owner: rutter
      automation_permission: deterministic
      public_runtime_capability: rutter.interface.bound-operations
      public_runtime_version: 6
      public_binding_contract_version: 1
      capability_verified: true
      owning_evolution: coordinator/join
      exact_mechanism:
        enforcement_class: rutter-state-transition
        request: null
        validation:
          operation: validate
          input_ref: input
          evidence_ref: input.evidence
          validator_ref: validate_join
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
      precondition: child results are present
      postcondition: coordinator selects ready or wait
      failure_result: invalid join evidence is rejected
      observable_evidence: {operation: validate, evidence_ref: input.evidence, output_ref: result}
      positive_trace:
        operation: advance
        input_ref: input
        evidence_ref: input.evidence
        outcome: ready
        expected: {kind: successor, state: complete, result_ref: result}
      negative_trace:
        operation: validate
        input_ref: input
        evidence_ref: input.evidence
        outcome: invalid
        expected: {kind: rejection, rejection_code: invalid-join-evidence}
    - obligation_id: obl-release
      original_decision_owner: rutter
      automation_permission: deterministic
      public_runtime_capability: rutter.interface.bound-operations
      public_runtime_version: 6
      public_binding_contract_version: 1
      capability_verified: true
      owning_evolution: coordinator/authorize-release
      exact_mechanism:
        enforcement_class: rutter-state-transition
        request: null
        validation:
          operation: validate
          input_ref: input
          evidence_ref: input.evidence
          validator_ref: validate_release
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
      precondition: aggregate result is validated
      postcondition: coordinator authorizes release
      failure_result: release remains blocked
      observable_evidence: {operation: validate, evidence_ref: input.evidence, output_ref: result}
      positive_trace:
        operation: advance
        input_ref: input
        evidence_ref: input.evidence
        outcome: released
        expected: {kind: successor, state: complete, result_ref: result}
      negative_trace:
        operation: validate
        input_ref: input
        evidence_ref: input.evidence
        outcome: invalid
        expected: {kind: rejection, rejection_code: invalid-release-evidence}
```
