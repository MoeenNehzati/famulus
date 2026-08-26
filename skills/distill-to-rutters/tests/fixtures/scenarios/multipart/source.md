# Coordinated independent retrieval

Start two independent retrieval Voyages, wait for both validated results, and
release one aggregate only after the coordinator authorizes the join. The
coordinator owns start, dependency, join, aggregation, partial failure, retry,
cancellation, failure propagation, authorization, and release decisions.
Both worker Voyages use the shared `fetch-worker` Rutter definition, whose
`fetch` evolution validates each retrieved result before completion.
