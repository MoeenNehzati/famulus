# Skill Development Quickstart

Famulus uses different skills for behavioral changes, behavior-preserving
refactors, blueprint maintenance, standards, hooks, and certification. Choose
the route from the intended outcome rather than invoking every lifecycle skill
for every change.

## What to use when

| Need | Skill |
|---|---|
| Create a personal skill or change its intended behavior or public interface | `skill-maker` |
| Audit or refactor a registered node without changing behavior | `refactor-node` |
| Create or change a cross-host assistant lifecycle hook | `hook-maker` |
| Regenerate an existing blueprint when regeneration is specifically needed | `regenerate-blueprints` |
| Create, change, or audit a canonical repository standard | `update-standards` |
| Check whether node certificates are current or obtain canonical hashes | `skill-drift` |
| Issue fresh certificates for final node state | `skill-certifier` |
| Integrate branches whose structures have diverged beyond a normal merge | `semantic-integration` |
| Apply branch safety and exact-scope Git hygiene | `git-workflow` |

## A typical change workflow

First classify the change. Use `skill-maker` for new behavior or a changed
public contract, and `refactor-node` when behavior must remain unchanged. Use
`hook-maker` instead when the requested product is a cross-host assistant
lifecycle hook. Use `update-standards` only when the canonical standard itself
is in scope.

Blueprint regeneration is not a routine synonym for editing or synchronization:
invoke `regenerate-blueprints` only when an existing blueprint actually needs
regeneration. After the final reviewed state is in place, use `skill-drift` to
check certificate currentness. Use `skill-certifier` only when fresh
certificates are requested.

All repository changes still follow `git-workflow`. Reserve
`semantic-integration` for substantial architectural divergence that a normal
merge or localized conflict resolution cannot preserve correctly.
