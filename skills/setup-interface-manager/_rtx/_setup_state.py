"""Private, fail-closed persistence for setup lifecycle state.

The manager supplies both the getter-selected path and its declared atomic-file
adapter.  This module deliberately exposes no dispatch or public CLI surface.
"""
from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, ContextManager, Literal, Mapping, Protocol


_SCHEMA_VERSION = 2
_LEDGER_MODE = 0o600
_MAX_COMPARE_RETRIES = 8


class LedgerError(RuntimeError):
    """Base class for a ledger which cannot safely be used."""


class LedgerFormatError(LedgerError):
    """The stored JSON is malformed, unsupported, or noncanonical."""


class LedgerPathError(LedgerError):
    """The getter-selected path cannot be safely traversed."""


class LedgerConflict(LedgerError):
    """Another writer changed the exact predecessor before publication."""


class FlowConflict(LedgerError):
    """A caller attempted to begin a second managed flow."""


class AtomicFileAdapter(Protocol):
    """The manager's restricted atomic-file capability.

    ``allowed_root`` is the absolute filesystem-volume anchor of the
    getter-selected path.  Read, compare-and-replace, and locking must wrap
    ``common.source.atomic-files.interface.python-api`` with that same anchor.
    ``ensure_private_parent`` is the sole extra boundary: it must create only
    missing parent components with fixed mode 0700 or an equivalent restrictive
    access-control list, reject link/reparse and
    non-directory components, and fail closed.  The later confined operations
    remain responsible for catching swaps after initialization.
    """

    def ensure_private_parent(self, path: Path, *, allowed_root: Path) -> None: ...

    def exclusive_file_lock(
        self, path: Path, *, allowed_root: Path, mode: int
    ) -> ContextManager[None]: ...

    def read_regular_file_bytes(self, path: Path, *, allowed_root: Path) -> bytes: ...

    def atomic_compare_and_replace_bytes(
        self,
        path: Path,
        data: bytes,
        *,
        expected_previous_bytes: bytes | None,
        expected_previous_mode: int | None,
        mode: int,
        allowed_root: Path,
    ) -> None: ...


_CONFIGURED_ATOMIC_FILES: ContextVar[AtomicFileAdapter | None] = ContextVar(
    "setup_interface_manager_atomic_files", default=None
)


def _require_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise LedgerFormatError(f"{field} must be a non-empty string")
    return value


def _require_version(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LedgerFormatError(f"{field} must be a positive integer")
    return value


@dataclass(frozen=True)
class SetupReceipt:
    """One verified setup interface and the roots which currently claim it."""

    version: int
    required_by: frozenset[str]

    def __post_init__(self) -> None:
        _require_version(self.version, "receipt.version")
        if not isinstance(self.required_by, frozenset):
            object.__setattr__(self, "required_by", frozenset(self.required_by))
        for root in self.required_by:
            _require_identifier(root, "receipt.required_by entry")


@dataclass(frozen=True)
class ContinuationIdentity:
    """The nonsensitive caller identity needed to resume one original request."""

    caller: str
    interface: str
    version: int

    def __post_init__(self) -> None:
        _require_identifier(self.caller, "continuation.caller")
        _require_identifier(self.interface, "continuation.interface")
        _require_version(self.version, "continuation.version")


@dataclass(frozen=True)
class ActiveFlow:
    """The sole in-progress managed action, without request payload data."""

    flow_id: str
    operation: Literal["setup", "teardown", "teardown-all"]
    root: str | None
    current_step: str
    verified_steps: tuple[str, ...]
    continuation: ContinuationIdentity | None

    def __post_init__(self) -> None:
        _require_identifier(self.flow_id, "active_flow.flow_id")
        if not isinstance(self.operation, str) or self.operation not in {"setup", "teardown", "teardown-all"}:
            raise LedgerFormatError("active_flow.operation is unsupported")
        _require_identifier(self.current_step, "active_flow.current_step")
        if not isinstance(self.verified_steps, tuple):
            object.__setattr__(self, "verified_steps", tuple(self.verified_steps))
        if len(set(self.verified_steps)) != len(self.verified_steps):
            raise LedgerFormatError("active_flow.verified_steps must not repeat steps")
        for step in self.verified_steps:
            _require_identifier(step, "active_flow.verified_steps entry")
        if self.operation == "teardown-all":
            if self.root is not None or self.continuation is not None or self.verified_steps:
                raise LedgerFormatError("teardown-all flow must not carry ordinary context")
        elif (
            not isinstance(self.root, str)
            or not isinstance(self.continuation, ContinuationIdentity)
        ):
            raise LedgerFormatError("ordinary flow requires root and continuation")
        else:
            _require_identifier(self.root, "active_flow.root")


@dataclass(frozen=True)
class SetupLedger:
    """Setup receipts plus at most one active lifecycle flow."""

    interfaces: Mapping[str, SetupReceipt]
    active_flow: ActiveFlow | None
    schema_version: Literal[1, 2] = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        copied: dict[str, SetupReceipt] = {}
        for interface, receipt in self.interfaces.items():
            copied[_require_identifier(interface, "interface key")] = receipt
            if not isinstance(receipt, SetupReceipt):
                raise LedgerFormatError("interface receipt is invalid")
        object.__setattr__(self, "interfaces", MappingProxyType(copied))
        if self.active_flow is not None and not isinstance(self.active_flow, ActiveFlow):
            raise LedgerFormatError("active_flow is invalid")
        if type(self.schema_version) is not int or self.schema_version not in {1, 2}:
            raise LedgerFormatError("unsupported ledger schema version")
        if self.schema_version == 1 and self.active_flow is not None:
            if self.active_flow.operation == "teardown-all":
                raise LedgerFormatError("schema-v1 cannot store teardown-all")

    @classmethod
    def empty(cls) -> SetupLedger:
        return cls(interfaces={}, active_flow=None)


def _json_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LedgerFormatError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _require_exact_keys(value: object, keys: set[str], field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise LedgerFormatError(f"{field} has unsupported fields")
    return value


def _decode_receipt(value: object) -> SetupReceipt:
    raw = _require_exact_keys(value, {"version", "required_by"}, "receipt")
    version = _require_version(raw["version"], "receipt.version")
    required_by = raw["required_by"]
    if not isinstance(required_by, list):
        raise LedgerFormatError("receipt.required_by must be a list")
    roots = tuple(_require_identifier(root, "receipt.required_by entry") for root in required_by)
    if len(set(roots)) != len(roots):
        raise LedgerFormatError("receipt.required_by must not repeat roots")
    return SetupReceipt(version=version, required_by=frozenset(roots))


def _decode_flow(value: object, schema_version: int) -> ActiveFlow:
    raw = _require_exact_keys(
        value,
        {"flow_id", "operation", "root", "current_step", "verified_steps", "continuation"},
        "active_flow",
    )
    continuation_value = raw["continuation"]
    continuation_raw = None if continuation_value is None else _require_exact_keys(
        continuation_value, {"caller", "interface", "version"}, "continuation"
    )
    verified_steps = raw["verified_steps"]
    if not isinstance(verified_steps, list):
        raise LedgerFormatError("active_flow.verified_steps must be a list")
    return ActiveFlow(
        flow_id=_require_identifier(raw["flow_id"], "active_flow.flow_id"),
        operation=raw["operation"],  # type: ignore[arg-type]
        root=None if raw["root"] is None else _require_identifier(raw["root"], "active_flow.root"),
        current_step=_require_identifier(raw["current_step"], "active_flow.current_step"),
        verified_steps=tuple(
            _require_identifier(step, "active_flow.verified_steps entry")
            for step in verified_steps
        ),
        continuation=None if continuation_raw is None else ContinuationIdentity(
            caller=_require_identifier(continuation_raw["caller"], "continuation.caller"),
            interface=_require_identifier(
                continuation_raw["interface"], "continuation.interface"
            ),
            version=_require_version(continuation_raw["version"], "continuation.version"),
        ),
    )


def parse_ledger(raw: bytes) -> SetupLedger:
    """Parse canonical schema-v1/v2 bytes, failing closed on every deviation."""
    if not isinstance(raw, bytes):
        raise LedgerFormatError("ledger must contain bytes")
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=_json_object_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                LedgerFormatError(f"unsupported JSON constant: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LedgerFormatError("ledger is not valid UTF-8 JSON") from exc
    root = _require_exact_keys(value, {"schema_version", "interfaces", "active_flow"}, "ledger")
    schema_version = root["schema_version"]
    if schema_version not in {1, 2}:
        raise LedgerFormatError("unsupported ledger schema version")
    interfaces_raw = root["interfaces"]
    if not isinstance(interfaces_raw, Mapping):
        raise LedgerFormatError("ledger.interfaces must be an object")
    ledger = SetupLedger(
        interfaces={
            _require_identifier(interface, "interface key"): _decode_receipt(receipt)
            for interface, receipt in interfaces_raw.items()
        },
        active_flow=None if root["active_flow"] is None else _decode_flow(root["active_flow"], schema_version),
        schema_version=schema_version,
    )
    if encode_ledger(ledger) != raw:
        raise LedgerFormatError("ledger bytes are not canonical")
    return ledger


def encode_ledger(ledger: SetupLedger) -> bytes:
    """Return the deterministic representation for the ledger's schema."""
    if not isinstance(ledger, SetupLedger):
        raise LedgerFormatError("ledger is invalid")
    active_flow: dict[str, object] | None
    if ledger.active_flow is None:
        active_flow = None
    else:
        flow = ledger.active_flow
        active_flow = {
            "flow_id": flow.flow_id,
            "operation": flow.operation,
            "root": flow.root,
            "current_step": flow.current_step,
            "verified_steps": list(flow.verified_steps),
            "continuation": None if flow.continuation is None else {
                "caller": flow.continuation.caller,
                "interface": flow.continuation.interface,
                "version": flow.continuation.version,
            },
        }
    value = {
        "schema_version": ledger.schema_version,
        "interfaces": {
            interface: {
                "version": receipt.version,
                "required_by": sorted(receipt.required_by),
            }
            for interface, receipt in sorted(ledger.interfaces.items())
        },
        "active_flow": active_flow,
    }
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def claim_receipts(
    ledger: SetupLedger, root: str, setup_interfaces: tuple[str, ...] | list[str]
) -> SetupLedger:
    """Add *root* to existing receipts, without deciding any graph semantics."""
    root = _require_identifier(root, "claim root")
    requested = tuple(_require_identifier(interface, "claim interface") for interface in setup_interfaces)
    if len(set(requested)) != len(requested):
        raise LedgerFormatError("claim interfaces must not repeat")
    interfaces = dict(ledger.interfaces)
    for interface in requested:
        receipt = interfaces.get(interface)
        if receipt is None:
            raise LedgerFormatError(f"cannot claim missing receipt: {interface}")
        interfaces[interface] = SetupReceipt(
            version=receipt.version, required_by=receipt.required_by | {root}
        )
    return SetupLedger(interfaces=interfaces, active_flow=ledger.active_flow)


def begin_flow(ledger: SetupLedger, flow: ActiveFlow) -> SetupLedger:
    """Install exactly one active flow; controller code owns all transitions."""
    if ledger.active_flow is not None:
        raise FlowConflict("a managed setup flow is already active")
    return SetupLedger(interfaces=ledger.interfaces, active_flow=flow)


def clear_flow(ledger: SetupLedger, flow_id: str) -> SetupLedger:
    """Clear the named active flow without guessing any interrupted side effect."""
    if ledger.active_flow is None or ledger.active_flow.flow_id != flow_id:
        raise FlowConflict("active flow does not match")
    return SetupLedger(interfaces=ledger.interfaces, active_flow=None)


class LedgerStore:
    """Confined ledger reader/CAS writer constructed only by the manager."""

    def __init__(self, resolved_getter_path: Path) -> None:
        path = Path(resolved_getter_path)
        if not path.is_absolute() or path.name in {"", ".", ".."}:
            raise LedgerPathError("ledger path must be an absolute file path")
        files = _CONFIGURED_ATOMIC_FILES.get()
        if files is None:
            raise LedgerError("ledger store requires the manager atomic-file capability")
        self._path = path
        self._files = files

    @classmethod
    def _from_atomic_files(
        cls, resolved_getter_path: Path, files: AtomicFileAdapter
    ) -> LedgerStore:
        """Private construction seam for the manager's restricted capability."""
        token = _CONFIGURED_ATOMIC_FILES.set(files)
        try:
            return cls(resolved_getter_path)
        finally:
            _CONFIGURED_ATOMIC_FILES.reset(token)

    def _ensure_safe_parent(self) -> None:
        self._files.ensure_private_parent(self._path, allowed_root=self._trusted_root)

    @property
    def _trusted_root(self) -> Path:
        return Path(self._path.anchor)

    @property
    def _lock_path(self) -> Path:
        return self._path.with_name(f".{self._path.name}.lock")

    def _read_existing_bytes(self) -> bytes | None:
        try:
            return self._files.read_regular_file_bytes(
                self._path, allowed_root=self._trusted_root
            )
        except FileNotFoundError:
            return None

    def _replace_and_verify(self, previous: bytes | None, next_: SetupLedger) -> None:
        expected = encode_ledger(next_)
        self._files.atomic_compare_and_replace_bytes(
            self._path,
            expected,
            expected_previous_bytes=previous,
            expected_previous_mode=None if previous is None else _LEDGER_MODE,
            mode=_LEDGER_MODE,
            allowed_root=self._trusted_root,
        )
        observed = self._read_existing_bytes()
        if observed != expected:
            raise LedgerConflict("ledger post-write state is uncertain")

    def read(self) -> SetupLedger:
        """Read strict state, creating the canonical empty ledger through CAS."""
        self._ensure_safe_parent()
        with self._files.exclusive_file_lock(
            self._lock_path, allowed_root=self._trusted_root, mode=_LEDGER_MODE
        ):
            raw = self._read_existing_bytes()
            if raw is not None:
                return parse_ledger(raw)
            empty = SetupLedger.empty()
            try:
                self._replace_and_verify(None, empty)
            except LedgerConflict:
                raise
            return empty

    def compare_and_update(self, previous: SetupLedger, next_: SetupLedger) -> None:
        """Publish *next_* only if the exact persisted predecessor is *previous*."""
        if not isinstance(previous, SetupLedger) or not isinstance(next_, SetupLedger):
            raise LedgerFormatError("compare-and-update requires setup ledgers")
        self._ensure_safe_parent()
        with self._files.exclusive_file_lock(
            self._lock_path, allowed_root=self._trusted_root, mode=_LEDGER_MODE
        ):
            raw = self._read_existing_bytes()
            if raw is None:
                if previous != SetupLedger.empty():
                    raise LedgerConflict("ledger is absent but predecessor was not empty")
            elif parse_ledger(raw) != previous:
                raise LedgerConflict("ledger predecessor changed")
            if previous != next_:
                self._replace_and_verify(raw, next_)

    def update(self, transform: Callable[[SetupLedger], SetupLedger]) -> SetupLedger:
        """Apply a pure ledger transform with bounded retry after a CAS loss."""
        for _ in range(_MAX_COMPARE_RETRIES):
            previous = self.read()
            next_ = transform(previous)
            if not isinstance(next_, SetupLedger):
                raise LedgerFormatError("ledger update transform returned an invalid value")
            try:
                self.compare_and_update(previous, next_)
            except LedgerConflict:
                continue
            return next_
        raise LedgerConflict("ledger changed repeatedly while updating")
