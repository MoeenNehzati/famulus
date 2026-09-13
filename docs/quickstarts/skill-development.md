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
| Check whether node certificates are current or obtain canonical hashes | `node-drift` |
| Issue fresh certificates for final node state | `node-certify` |
| Move a registered node or its owned files without changing behavior | `relocate-nodes` |
| Operate a named Rutter through its public lifecycle | `using-compass` |
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
regeneration. After the final reviewed state is in place, use `node-drift` to
check certificate currentness. Use `node-certify` only when fresh
certificates are requested.

Moving a node is its own route. `refactor-node` changes a node in place and
`relocate-nodes` changes where it lives, so a relocation that also rewrites
behavior is two changes and should be done as two: the relocation engine
rewrites declared paths and typed identities and nothing else, which is what
lets its second preflight prove the move was exact.

All repository changes still follow `git-workflow`. Reserve
`semantic-integration` for substantial architectural divergence that a normal
merge or localized conflict resolution cannot preserve correctly.
