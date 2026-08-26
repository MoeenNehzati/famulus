# Known coordinated multi-Voyage contract shape

This fixture demonstrates the positive local shape deferred from Task 3: two
independent worker Voyages plus a coordinator Voyage and explicit ownership of
every orchestration rule. It is parsed-contract evidence, not execution.

```distill-scenario-contract
scenario: multipart
assignment:
  assignments:
    - part_id: part-left
      voyage_id: voyage-left
      rutter_definition_id: fetch-worker
      charter_fields: [left-input]
      input_ids: [left-input]
      output_ids: [left-result]
      inseparability:
        status: independent
        reason: The left retrieval has no mutable state exchange with the right retrieval.
      independent_workflows:
        - {voyage_id: voyage-right, join_transition: coordinate}
    - part_id: part-right
      voyage_id: voyage-right
      rutter_definition_id: fetch-worker
      charter_fields: [right-input]
      input_ids: [right-input]
      output_ids: [right-result]
      inseparability:
        status: independent
        reason: The right retrieval has no mutable state exchange with the left retrieval.
      independent_workflows:
        - {voyage_id: voyage-left, join_transition: coordinate}
    - part_id: part-coordinator
      voyage_id: voyage-coordinator
      rutter_definition_id: coordinator
      charter_fields: [left-result, right-result]
      input_ids: [left-result, right-result]
      output_ids: [aggregate]
      inseparability:
        status: independent
        reason: The coordinator consumes validated worker outputs through declared transitions.
      independent_workflows:
        - {voyage_id: voyage-left, join_transition: coordinate}
        - {voyage_id: voyage-right, join_transition: coordinate}
  orchestration:
    mode: coordinated
    coordinator_rutter_id: coordinator
    starts:
      - {obligation_id: obl-join, owning_transition: coordinate, evidence: Both worker charters are ready.}
    dependencies:
      - {obligation_id: obl-join, owning_transition: coordinate, evidence: The aggregate depends on both worker results.}
    joins:
      - {obligation_id: obl-join, owning_transition: coordinate, evidence: Both worker results validate before the join.}
    aggregate_results:
      - {obligation_id: obl-join, owning_transition: coordinate, evidence: The aggregate contains the two validated results.}
    partial_failure:
      - {obligation_id: obl-join, owning_transition: coordinate, evidence: One failed worker blocks release.}
    retries:
      - {obligation_id: obl-join, owning_transition: coordinate, evidence: Retry evidence names the failed worker.}
    retry_owner: coordinator
    cancellation:
      - {obligation_id: obl-join, owning_transition: coordinate, evidence: Cancellation is recorded before propagation.}
    failure_propagation:
      - {obligation_id: obl-join, owning_transition: coordinate, evidence: Worker failure reaches the coordinator result.}
    authorization:
      - {obligation_id: obl-join, owning_transition: coordinate, evidence: The coordinator transition exclusively authorizes release.}
    release:
      - {obligation_id: obl-join, owning_transition: coordinate, evidence: Joined evidence exists before aggregate release.}
graph:
  rutters:
    - rutter_id: fetch-worker
      voyage_ids: [voyage-left, voyage-right]
      version: 1
      initial_evolution: fetch
      charter_fields: [retrieval-input]
      evolutions:
        - evolution_id: fetch
          evolution_type: operation
          obligation_ids: [obl-fetch]
          decision_owner: rutter
          validator: validate_fetch
          outcomes: [fetched, invalid]
      transitions:
        - {from: fetch, outcome: fetched, to: complete}
        - {from: fetch, outcome: invalid, to: failed}
      terminal_results: [complete, failed]
    - rutter_id: coordinator
      voyage_ids: [voyage-coordinator]
      version: 1
      initial_evolution: coordinate
      charter_fields: [left-result, right-result]
      evolutions:
        - evolution_id: coordinate
          evolution_type: operation
          obligation_ids: [obl-join]
          decision_owner: rutter
          validator: validate_join
          outcomes: [joined, incomplete]
      transitions:
        - {from: coordinate, outcome: joined, to: complete}
        - {from: coordinate, outcome: incomplete, to: failed}
      terminal_results: [complete, failed]
logic:
  enforcement_matrix:
    - obligation_id: obl-fetch
      original_decision_owner: rutter
      automation_permission: deterministic
      public_runtime_capability: rutter.interface.bound-operations
      public_runtime_version: 6
      public_binding_contract_version: 1
      capability_verified: false
      capability_gap:
        absent_binding_contract: rutter.interface.bound-operations semantic_enforcement@1
        exact_repair: Expose versioned request, validation, transition, successor, and rejection-code bindings.
      owning_evolution: fetch-worker/fetch
      exact_mechanism:
        enforcement_class: operation-name-only
        request: null
        validation:
          operation: validate
          input_ref: input
          evidence_ref: input.evidence
          validator_ref: validate_fetch
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
      precondition: A retrieval result is available.
      postcondition: Only a validated retrieval result completes the worker.
      failure_result: Invalid retrieval evidence leaves the worker incomplete.
      observable_evidence:
        operation: validate
        evidence_ref: input.evidence
        output_ref: result
      positive_trace:
        operation: advance
        input_ref: input
        evidence_ref: input.evidence
        outcome: fetched
        expected: {kind: successor, state: complete, result_ref: result}
      negative_trace:
        operation: validate
        input_ref: input
        evidence_ref: input.evidence
        outcome: invalid
        expected: {kind: rejection, rejection_code: invalid-fetch-evidence}
    - obligation_id: obl-join
      original_decision_owner: rutter
      automation_permission: deterministic
      public_runtime_capability: rutter.interface.bound-operations
      public_runtime_version: 6
      public_binding_contract_version: 1
      capability_verified: false
      capability_gap:
        absent_binding_contract: rutter.interface.bound-operations semantic_enforcement@1
        exact_repair: Expose versioned request, validation, transition, successor, and rejection-code bindings.
      owning_evolution: coordinator/coordinate
      exact_mechanism:
        enforcement_class: operation-name-only
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
      precondition: Both worker results are present.
      postcondition: Only validated join evidence authorizes aggregate release.
      failure_result: Incomplete evidence blocks the join and release.
      observable_evidence:
        operation: validate
        evidence_ref: input.evidence
        output_ref: result
      positive_trace:
        operation: advance
        input_ref: input
        evidence_ref: input.evidence
        outcome: joined
        expected: {kind: successor, state: complete, result_ref: result}
      negative_trace:
        operation: validate
        input_ref: input
        evidence_ref: input.evidence
        outcome: incomplete
        expected: {kind: rejection, rejection_code: invalid-join-evidence}
```
