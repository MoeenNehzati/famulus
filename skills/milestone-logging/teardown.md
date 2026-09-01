# Milestone logging teardown

1. Perform no external mutation. Retain the logging directory, all milestone records, environment values, and plugin state.
2. Teardown is complete only when the independent teardown verifier returns exactly `{"torn_down": true}`. A false result means teardown is not complete.
