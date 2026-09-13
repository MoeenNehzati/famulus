# List Manager setup

`cloud-files.interface.setup` has already completed. Use the current `run-markdown`
flow id as `setup_flow_id` on every executable Famulus call below.

1. Call `cloud-files._rtx.interface.lists-exists lists/todo.yaml` and
   `cloud-files._rtx.interface.lists-exists lists/triage.yaml`.
2. Only for an `exists: false` result, initialize exactly that missing list:
   `list-manager._rtx.interface.cloud-init todo --cloud --schema todo` or
   `list-manager._rtx.interface.cloud-init triage --cloud --schema triage`.
3. Validate both with `list-manager._rtx.interface.cloud-read todo --cloud` and
   `list-manager._rtx.interface.cloud-read triage --cloud`.

Settle only after both lists exist and validate.
Never overwrite an existing list; an existence/read error is a failure, not absence.
