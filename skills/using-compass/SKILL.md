---
name: using-compass
description: Use when a user or another skill directs the agent to use a named compass.
---

<!-- BEGIN BLUEPRINT CONTRACT -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Catalog: assistant-operations; topics: task-automation, session-management; visibility: listed
Activation: user-request, skill-workflow; persistent modifier: no

Skill Version: 7

Uses Interfaces:
- `using-compass.source.gateway -> rutter.interface.bound-operations@5`

Public Interfaces:
- `using-compass.interface.default`
<!-- END BLUEPRINT CONTRACT -->
<!-- BEGIN BLUEPRINT INTERFACES -->
> Generated from `blueprint.yaml`. Do not edit this block by hand.

Instruction Interfaces:

These interfaces are documented prompt surfaces. They are not executed through `dispatcher`:
- `using-compass.interface.default` — Ask one authorized Voyage for its public operating interface and follow only the methods it advertises.
<!-- END BLUEPRINT INTERFACES -->
# Using Compass

`Use compass on <rutter-name>`.

Use one invoker-provided authorized `Voyage` supplied with the activation
request. If it is absent, report a public-interface gap and stop.

Invoke `voyage.help()`. Its result is valid only when every entry provides a
public name, bound signature, and nonempty docstring. If the result is missing
or malformed, report a public-interface gap and stop. Use only its advertised
methods, following their returned signatures and docstrings exactly.
