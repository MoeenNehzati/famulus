# Getting Started with Officina

> **Status:** Illustrative guide.
>
> This walkthrough simplifies the current Famulus `get-weather` skill-node
> profile. Its skill layout and `_rtx` child are one way to apply Officina, not
> the universal form of an Officina node.

Officina helps contributors change one part of a mixed system without first
having to understand every nearby instruction, script, schema, and host
permission. It does that by putting behavior behind explicit boundaries and
recording the relationships across those boundaries in data that tools can
inspect.

The current `get-weather` profile is a compact example. Human-language
instructions turn a request such as “weather in Boston tomorrow” into concrete
arguments and a useful summary. Python resolves the location and retrieves the
hourly forecast. These pieces cooperate, but they have different reasons to
change and need different kinds of review.

## 1. Start with the boundary

Without an explicit boundary, a contributor might treat the instructions,
Python client, network access, and output conventions as one informal bundle.
A change to any one of them would require repository inspection to discover
what else relies on it.

Officina instead asks which behavior must be understood and changed together.
In this profile, the instruction behavior and the executable weather client
are separate behind declared boundaries. That makes their relationship visible
without pretending they are independent: the instructions still declare that
they use the client interface.

## 2. Separate modules from behavioral sources

Officina has two node kinds. A **module** owns external identity, namespace,
discovery, access policy, and authority. A **behavioral source** owns one
cohesive unit of instructions or implementation inside a module.

The simplified profile has two modules and one source within each:

```text
skills/get-weather/
  blueprint.yaml                         module: get-weather
  SKILL.md                               instruction gateway
  blueprints/gateway.yaml                instruction source
  _rtx/
    blueprint.yaml                       module: get-weather._rtx
    __init__.py                          child-module gateway
    _weather_client.py                   executable gateway
    blueprints/rtx-weather-client.yaml   executable source
```

The top-level module makes the skill discoverable and exposes its instruction
behavior. Its `_rtx` child contains the executable weather client. This
module/source separation keeps outward authority distinct from the behavior
that exercises it.

## 3. Pair each gateway with a blueprint

A node has an operational face and a descriptive face. The **gateway** is the
file or system through which the node behaves. The **blueprint** is the
machine-readable description of the node.

For the instruction source, `SKILL.md` is the gateway and
`blueprints/gateway.yaml` describes what those instructions accept, use, and
produce. For the executable source, `_weather_client.py` is the gateway and
`blueprints/rtx-weather-client.yaml` describes its callable interface, direct
I/O, outcomes, and process binding. The module blueprints describe how those
sources are contained and exposed.

The blueprint lets tools and contributors inspect architectural facts without
reverse-engineering them from implementation. It is not self-proving: a valid
description can still be incomplete or inaccurate.

## 4. Declare ownership and dependencies

Every owned artifact has one most-specific direct owner. The top-level source
owns `SKILL.md`; the executable source owns `_weather_client.py`. The parent
module contains the `_rtx` child but does not thereby own the child's files or
gain an undeclared dependency on its behavior.

Relationships are declared separately from physical nesting. The instruction
source records that it uses version 1 of
`get-weather._rtx.interface.scripts-weather`. This declaration tells reviewers
and repository tools which boundary the instructions cross. Merely placing the
files in adjacent directories would not create that relationship or grant
permission to use it.

## 5. Make boundary contracts checkable

An interface contract describes the interaction at a boundary. In this
example, the executable interface declares optional `date`, `end-date`, and
`location` arguments, the allowed date format, success and error outcomes,
direct network and stream I/O, and the forecast and diagnostic outputs. Its
process binding narrows invocation to the declared flags and forbids stdin and
positional arguments.

These declarations turn many review questions into mechanical checks: Is the
argument required? Which output is produced on success? Is the operation
read-only? May the process receive stdin? When structured data needs a shared,
mechanically specified shape, the owning contract can reference a JSON Schema.
JSON Schema fits at that data boundary; it does not replace the blueprint's
architectural facts or the semantic meaning that still requires judgment.

## 6. Expose and authorize an interface

A behavioral source owns the meaning of its interface. Its module can export
that interface while adding a public identity and caller-access policy. In the
weather profile, the `_rtx` module exports the source's weather interface, and
the parent module explicitly routes that child interface through its
namespace.

Dispatcher follows those declarations for a call. It resolves the requested
interface and version, checks each relevant module policy, compiles arguments
against the process binding, and launches the gateway only if the route is
authorized. A source's statement that it uses an interface remains an
architectural fact for validation and review; it does not by itself grant
runtime authority.

## 7. Validate, then certify

Mechanical validation checks what can be decided from structured facts: the
blueprint shape, canonical identities, ownership, references, dependency and
interface rules, process bindings, and generated views. These checks catch
many broken boundaries, but they cannot establish that a description faithfully
captures the behavior of `SKILL.md` or `_weather_client.py`.

Certification adds semantic review. A reviewer compares operational behavior
with the blueprints and applicable standards, then retains evidence for the
exact committed state that was reviewed. Schema validity and certification
therefore answer different questions: one asks whether the declarations are
structurally acceptable; the other asks whether the declared and actual
behavior agree.

## 8. Treat drift as stale assurance

Certification does not make future edits safe automatically. Officina hashes
the relevant state and compares it with the state supported by the retained
evidence. When a relevant gateway, blueprint, dependency, standard, or other
certification input changes, the prior assurance becomes stale.

Drift is a prompt to review the changed relationship and certify the new exact
state. It is not proof that the change is wrong, and a current certificate is
not a guarantee about inputs outside its declared basis.

## Vocabulary recap

- **Node:** a cohesive, explicit Officina boundary.
- **Module:** the node that owns identity, namespace, access, and authority.
- **Behavioral source:** the node that owns cohesive instructions or
  implementation inside a module.
- **Gateway:** the operational file or system through which a node behaves.
- **Blueprint:** the machine-readable description of a node.
- **Interface contract:** the declared meaning and boundary of an interaction.
- **Dispatcher:** the bounded router that authorizes and launches declared
  machine interfaces.
- **Certification:** retained semantic assurance for an exact committed state.
- **Drift:** a relevant change that makes retained assurance stale.

## Choose the next document by task

- To make or review architectural decisions, read
  [Architectural Principles](architectural-principles.md).
- To read or author node declarations, continue with
  [Blueprints](blueprints.md).
- To choose a machine-checkable data boundary, read
  [Schemas](schema.md).
- To follow invocation and authorization, read
  [Dispatcher](dispatcher.md).
- To validate semantic alignment and manage stale evidence, read
  [Certification and Drift](certification_and_drift.md).
- To change the current Famulus skill-node profile, use
  [Skill-node Maintainer Scaffolding](scaffolding/README.md) and
  [Refactoring Officina Nodes](refactor.md).
- To locate the current implementation owner, use the
  [Implementation Map](utility-map.md).

If this guide conflicts with
[Architectural Principles](architectural-principles.md), Architectural
Principles prevails.
