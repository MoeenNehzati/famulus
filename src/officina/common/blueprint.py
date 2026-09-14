"""Derived security classification for declared blueprint interfaces."""

from __future__ import annotations

import re
from typing import Mapping, Sequence


SecurityLevel = int
FilesystemAuthorityClaim = tuple[str, str, str, re.Pattern[str] | None]

_OUTPUT_MEDIA = frozenset({"stdout", "stderr", "event-stream"})
_FILESYSTEM_MEDIA = frozenset(
    {
        "local-filesystem",
        "repository-filesystem",
        "home-filesystem",
        "temporary-filesystem",
    }
)
_READ_MEDIA = frozenset(
    {
        *_FILESYSTEM_MEDIA,
        "remote-filesystem",
        "network-request",
        "network",
        "prompt",
        "stdin",
        "local-system",
        "calendar",
        "email",
        "subprocess",
    }
)
_DOWNLOAD_MEDIA = frozenset({"remote-filesystem", "network-request", "network"})
_MUTATING_AUTH_MODES = frozenset({"creates", "updates", "deletes"})
_OWNED_WRITE_EFFECTS = frozenset({"create", "update", "append"})


def _path_is_owned(
    module_id: str,
    path: object,
    path_match: object,
    authority_claims: Sequence[FilesystemAuthorityClaim],
) -> bool:
    """Check whether an exact local write falls under module authority.

    Intent
    ------
    Recognize only paths declared by the interface's owning module.

    Rationale
    ---------
    A level-one write is safe only when its declaration names an owned path;
    unknown and broad matches fail closed.

    Pseudocode
    ----------
    - if path is not string or path_match is not exact:
      - return false
    - set owned_claims = authority claims for module_id
    - return whether path matches owned_claims

    Wraps
    -----
    - none
    """
    if not isinstance(path, str) or path_match != "exact":
        return False
    return any(
        owner_id == module_id
        and (
            match == "exact"
            and path == owned_path
            or match == "regex"
            and pattern is not None
            and pattern.fullmatch(path) is not None
        )
        for owner_id, match, owned_path, pattern in authority_claims
    )


def direct_security_level(
    contract: Mapping[str, object] | None,
    *,
    module_id: str,
    authority_claims: Sequence[FilesystemAuthorityClaim],
) -> SecurityLevel:
    """Classify one interface's declared direct behavior.

    Intent
    ------
    Return level zero for reads, one for owned local writes, and two otherwise.

    Rationale
    ---------
    Incomplete or unrecognized declarations cannot establish a lower security
    tier, so they must fail closed at level two.

    Pseudocode
    ----------
    - set level = zero
    - for entry in declared direct I/O entries:
      - if entry is a permitted read:
        - continue
      - set is_owned = @._path_is_owned(entry path and authority claims)
      - if is_owned write has matching effect:
        - set level = one
      - else:
        - return two
    - return level

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._path_is_owned:
      why:
        computes: "Matches a claimed filesystem write to the declaring module's authority."
    """

    if not isinstance(contract, Mapping):
        return 2
    direct_io = contract.get("direct_io")
    if not isinstance(direct_io, Mapping):
        return 2
    level = 0
    owned_write_ids: set[str] = set()
    for section in ("reads", "writes", "network"):
        entries = direct_io.get(section)
        if not isinstance(entries, list):
            return 2
        for entry in entries:
            if not isinstance(entry, Mapping):
                return 2
            access = entry.get("access")
            medium = entry.get("medium")
            auth = entry.get("auth")
            if isinstance(auth, Mapping) and auth.get("mode") in _MUTATING_AUTH_MODES:
                return 2
            if (
                section == "reads"
                and medium in _READ_MEDIA
                and (
                    access == "read"
                    or access == "download" and medium in _DOWNLOAD_MEDIA
                )
            ):
                continue
            if (
                section == "network"
                and medium in {"network-request", "network"}
                and access in {"read", "download"}
            ):
                continue
            if section == "writes" and medium in _OUTPUT_MEDIA and access == "write":
                continue
            if (
                section == "writes"
                and medium in _FILESYSTEM_MEDIA
                and access in {"write", "read-write"}
                and _path_is_owned(
                    module_id,
                    entry.get("path"),
                    entry.get("path_match"),
                    authority_claims,
                )
            ):
                entry_id = entry.get("id")
                if not isinstance(entry_id, str):
                    return 2
                owned_write_ids.add(entry_id)
                level = max(level, 1)
                continue
            return 2
    execution = contract.get("execution")
    if not isinstance(execution, Mapping):
        return 2
    if execution.get("state_effect") == "read-only":
        return 2 if level else 0
    if execution.get("state_effect") != "mutating":
        return 2
    effects = execution.get("effects")
    if not isinstance(effects, list) or not effects:
        return 2
    for effect in effects:
        if (
            not isinstance(effect, Mapping)
            or effect.get("direct_io_ref") not in owned_write_ids
            or effect.get("action") not in _OWNED_WRITE_EFFECTS
        ):
            return 2
    return level


def interface_security_levels(
    declarations: Mapping[str, Mapping[str, object]],
    interface_sources: Mapping[str, str],
    interface_uses: Mapping[str, Sequence[tuple[str, int]]],
    source_modules: Mapping[str, str],
    authority_claims: Sequence[FilesystemAuthorityClaim],
) -> dict[str, SecurityLevel]:
    """Derive each source interface's direct and transitive security level.

    Intent
    ------
    Produce the maximum direct security level across every declared use closure.

    Rationale
    ---------
    A caller can exercise every interface it uses, so its security tier must
    not be lower than any reachable callee's tier.

    Pseudocode
    ----------
    - set levels = empty mapping
    - for interface_id in declarations:
      - set derived_level = @level_for(interface_id)
    - return levels

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .direct_security_level:
      why:
        constructs: "Builds direct levels used by the recursive source-level calculation."

    """

    levels: dict[str, SecurityLevel] = {}

    def level_for(interface_id: str, active: set[str]) -> SecurityLevel:
        """Return one source interface's memoized transitive level.

        Intent
        ------
        Join direct behavior with every interface reachable through declared uses.

        Rationale
        ---------
        Cycle detection and memoization keep the derived level finite and
        deterministic for every source interface.

        Pseudocode
        ----------
        - set direct_level = @.direct_security_level(source declaration)
        - for used_interface in source_uses:
          - set direct_level = maximum of direct_level and @level_for(used_interface)
        - return direct_level

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .direct_security_level:
          why:
            constructs: "Builds the direct level that is joined with each reachable callee level."
        """
        source_id = interface_sources.get(interface_id)
        if source_id is None:
            raise ValueError(f"{interface_id}: security level references an unknown interface")
        if source_id in levels:
            return levels[source_id]
        if source_id in active:
            raise ValueError(f"{interface_id}: security level use cycle")
        declaration = declarations.get(source_id)
        module_id = source_modules.get(source_id)
        if declaration is None or module_id is None:
            raise ValueError(f"{interface_id}: missing source security level")
        level = direct_security_level(
            declaration.get("contract") if isinstance(declaration, Mapping) else None,
            module_id=module_id,
            authority_claims=authority_claims,
        )
        for used_id, _version in interface_uses.get(source_id, ()):
            level = max(level, level_for(used_id, {*active, source_id}))
        levels[source_id] = level
        return level

    for interface_id in declarations:
        level_for(interface_id, set())
    return levels
