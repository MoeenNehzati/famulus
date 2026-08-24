"""A process-safe public interface for operating authorized Voyages by ID."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import json
from pathlib import Path
import re
import sys
from typing import Mapping

from officina.runtime.python_machine_interface import PythonArgvMachineInterface

from .engine import Voyage
from .values import (
    EvolutionView,
    MachineInstruction,
    Message,
    RutterDefinitionError,
    ValidationReport,
    VoyageStatus,
)


_USAGE_GUIDANCE = (
    "Use one agent per Voyage.\n\n"
    "Workflow:\n"
    "  1. Invoke modes to inspect the available initialization modes and their "
    "explanations.\n"
    "  2. Invoke list, optionally with --run-prefix. If the selected run reports "
    "not-initialized, invoke initiate [mode] exactly once for that run; omit the "
    "mode to use the default and omit --run-prefix to use the selected mode as "
    "the prefix. Initiation returns that run's prefixed voyage_ids.\n"
    "  3. Assign exactly one agent to each Voyage.\n"
    "  4. Each agent uses only its assigned voyage_id with status, validate, "
    "and advance.\n"
    "  5. Keep that assignment until the Voyage becomes terminal or faulted; "
    "agents must not share or switch Voyage IDs.\n"
    "  6. After capturing a terminal result, invoke release unless there is an "
    "explicit reason to preserve that Voyage's working directory. Do not release "
    "a ready, faulted, or uncertain Voyage."
)


class UnknownVoyageError(ValueError):
    """The requested Voyage is outside a dispenser's advertised authority."""


class VoyagesNotInitializedError(ValueError):
    """The dispenser has no durable Voyages to operate."""


class VoyagesAlreadyInitializedError(ValueError):
    """The dispenser already owns durable Voyages."""


class UnknownVoyageModeError(ValueError):
    """The requested initialization mode is not declared by the dispenser."""


class InvalidVoyageModeArgumentsError(ValueError):
    """Initialization arguments do not match the selected mode."""


class VoyageNotTerminalError(ValueError):
    """A Voyage cannot be released before producing its terminal result."""


class _UsageError(ValueError):
    pass


class _InvalidResponse(ValueError):
    def __init__(self, report: ValidationReport) -> None:
        super().__init__("response validation failed")
        self.report = report


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError(message)


class VoyageDispenser(PythonArgvMachineInterface):
    """Enumerate and operate one authorized collection of durable Voyages."""

    prog = "voyage-dispenser"

    def __init__(
        self,
        *,
        modes: Mapping[str, Mapping[str, object]],
        initiate_voyages: Callable[..., None],
        get_voyage_ids: Callable[[str | None], Sequence[str]],
        open_voyage: Callable[[str], Voyage],
        release_voyage: Callable[[str], None],
    ) -> None:
        if not isinstance(modes, Mapping) or not modes:
            raise RutterDefinitionError("modes must be a non-empty mapping")
        normalized_modes: dict[str, dict[str, object]] = {}
        seen_arguments: dict[str, str] = {}
        for mode, config in modes.items():
            if (
                type(mode) is not str
                or not mode
                or mode != mode.strip()
                or not isinstance(config, Mapping)
                or set(config) != {"description", "arguments"}
            ):
                raise RutterDefinitionError(
                    "modes must map trimmed names to description and arguments"
                )
            description = config["description"]
            arguments = config["arguments"]
            if type(description) is not str or not description.strip():
                raise RutterDefinitionError(
                    "mode descriptions must be non-empty strings"
                )
            if not isinstance(arguments, Mapping):
                raise RutterDefinitionError(
                    "mode arguments must map names to descriptions"
                )
            normalized = dict(arguments)
            if any(
                type(name) is not str
                or not name.isidentifier()
                or type(description) is not str
                or not description.strip()
                for name, description in normalized.items()
            ):
                raise RutterDefinitionError(
                    "mode arguments must use identifier names and non-empty descriptions"
                )
            if "run_prefix" in normalized:
                raise RutterDefinitionError(
                    "run_prefix is reserved by the Voyage dispenser"
                )
            for name, argument_description in normalized.items():
                prior = seen_arguments.setdefault(name, argument_description)
                if prior != argument_description:
                    raise RutterDefinitionError(
                        f"mode argument {name!r} has conflicting descriptions"
                    )
            normalized_modes[mode] = {
                "description": description,
                "arguments": normalized,
            }
        if not callable(initiate_voyages):
            raise RutterDefinitionError("initiate_voyages must be callable")
        if not callable(get_voyage_ids):
            raise RutterDefinitionError("get_voyage_ids must be callable")
        if not callable(open_voyage):
            raise RutterDefinitionError("open_voyage must be callable")
        if not callable(release_voyage):
            raise RutterDefinitionError("release_voyage must be callable")
        self._modes = normalized_modes
        self._default_mode = next(iter(normalized_modes))
        self._initiate_voyages = initiate_voyages
        self._get_voyage_ids = get_voyage_ids
        self._open_voyage = open_voyage
        self._release_voyage = release_voyage

    @staticmethod
    def _validated_run_prefix(run_prefix: str) -> str:
        if (
            type(run_prefix) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_prefix) is None
        ):
            raise InvalidVoyageModeArgumentsError(
                "invalid Voyage run prefix: use letters, digits, dot, underscore, "
                "or hyphen, starting with a letter or digit"
            )
        return run_prefix

    def _validated_voyage_ids(
        self,
        run_prefix: str | None = None,
        *,
        allow_empty: bool,
    ) -> tuple[str, ...]:
        if run_prefix is not None:
            run_prefix = self._validated_run_prefix(run_prefix)
        values = self._get_voyage_ids(run_prefix)
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise RutterDefinitionError(
                "get_voyage_ids must return a sequence of Voyage IDs"
            )
        voyage_ids = tuple(values)
        if any(
            type(voyage_id) is not str
            or not voyage_id
            or voyage_id != voyage_id.strip()
            for voyage_id in voyage_ids
        ):
            raise RutterDefinitionError(
                "get_voyage_ids must return non-empty trimmed strings"
            )
        if len(set(voyage_ids)) != len(voyage_ids):
            raise RutterDefinitionError(
                "get_voyage_ids must not return duplicate Voyage IDs"
            )
        if run_prefix is not None and any(
            not voyage_id.startswith(f"{run_prefix}-") for voyage_id in voyage_ids
        ):
            raise RutterDefinitionError(
                "run-scoped Voyage IDs must be prefixed by their run prefix"
            )
        if not voyage_ids and not allow_empty:
            raise VoyagesNotInitializedError(
                "no initialized Voyages"
                + (
                    "; invoke initiate first"
                    if run_prefix is None
                    else f" for run prefix {run_prefix!r}; invoke initiate first"
                )
            )
        return voyage_ids

    def get_voyage_ids(self, run_prefix: str | None = None) -> tuple[str, ...]:
        """Return every authorized Voyage ID, optionally scoped to one run."""

        return self._validated_voyage_ids(run_prefix, allow_empty=False)

    def get_modes(self) -> dict[str, dict[str, object]]:
        """Return each initialization mode, explanation, and required arguments."""

        return {
            mode: {
                "description": config["description"],
                "arguments": dict(config["arguments"]),
            }
            for mode, config in self._modes.items()
        }

    def get_default_mode(self) -> str:
        """Return the first declared initialization mode."""

        return self._default_mode

    def initiate_voyages(
        self,
        mode: str | None = None,
        *,
        run_prefix: str | None = None,
        **mode_arguments: str,
    ) -> tuple[str, ...]:
        """Create this dispenser's durable Voyages in one declared mode."""

        if mode is None:
            mode = self.get_default_mode()
        if type(mode) is not str or mode not in self._modes:
            raise UnknownVoyageModeError(f"unknown Voyage mode {mode!r}")
        selected_prefix = self._validated_run_prefix(
            mode if run_prefix is None else run_prefix
        )
        required = set(self._modes[mode]["arguments"])
        provided = set(mode_arguments)
        if missing := sorted(required - provided):
            flags = ", ".join(f"--{name.replace('_', '-')}" for name in missing)
            raise InvalidVoyageModeArgumentsError(
                f"mode {mode!r} requires {flags}"
            )
        if unexpected := sorted(provided - required):
            flags = ", ".join(
                f"--{name.replace('_', '-')}" for name in unexpected
            )
            raise InvalidVoyageModeArgumentsError(
                f"mode {mode!r} does not accept {flags}"
            )
        if any(type(value) is not str or not value for value in mode_arguments.values()):
            raise InvalidVoyageModeArgumentsError(
                "mode argument values must be non-empty strings"
            )
        if self._validated_voyage_ids(selected_prefix, allow_empty=True):
            raise VoyagesAlreadyInitializedError(
                f"Voyage run prefix {selected_prefix!r} is already initialized"
            )
        self._initiate_voyages(
            mode,
            run_prefix=selected_prefix,
            **mode_arguments,
        )
        return self.get_voyage_ids(selected_prefix)

    def initiate(
        self,
        mode: str | None = None,
        *,
        run_prefix: str | None = None,
        **mode_arguments: str,
    ) -> tuple[str, ...]:
        """Alias the concise CLI operation to :meth:`initiate_voyages`."""

        return self.initiate_voyages(
            mode,
            run_prefix=run_prefix,
            **mode_arguments,
        )

    def help(self) -> str:
        """Explain how agents must divide and operate this dispenser's Voyages."""

        modes = "\n".join(
            f"  {mode}: {config['description']}"
            + (
                " Required arguments: "
                + ", ".join(
                    f"--{name.replace('_', '-')} ({argument_description})"
                    for name, argument_description in config["arguments"].items()
                )
                if config["arguments"]
                else " Required arguments: none."
            )
            for mode, config in self.get_modes().items()
        )
        return (
            f"{_USAGE_GUIDANCE}\n\n"
            f"Default mode: {self.get_default_mode()}\n"
            f"Initialization modes:\n{modes}"
        )

    def _resolve(self, voyage_id: str) -> Voyage:
        if (
            type(voyage_id) is not str
            or voyage_id not in self._validated_voyage_ids(allow_empty=True)
        ):
            raise UnknownVoyageError(f"unknown Voyage ID {voyage_id!r}")
        voyage = self._open_voyage(voyage_id)
        if not isinstance(voyage, Voyage):
            raise RutterDefinitionError("open_voyage must return a Voyage")
        return voyage

    def get_status(self, voyage_id: str) -> VoyageStatus:
        """Read the current public status of one authorized Voyage."""

        return self._resolve(voyage_id).get_status()

    def validate(
        self,
        voyage_id: str,
        response: object,
        *,
        responding_to: str | None = None,
    ) -> ValidationReport:
        """Validate a response against one authorized Voyage without mutation."""

        return self._resolve(voyage_id).validate(
            response,
            responding_to=responding_to,
        )

    def advance(
        self,
        voyage_id: str,
        response: object = None,
        *,
        responding_to: str | None = None,
        continue_: bool = True,
        dry_run: bool = False,
    ) -> EvolutionView:
        """Advance one authorized Voyage, leaving all other Voyages untouched."""

        voyage = self._resolve(voyage_id)
        if response is None:
            return voyage.advance(
                responding_to=responding_to,
                continue_=continue_,
                dry_run=dry_run,
            )
        return voyage.advance(
            response,
            responding_to=responding_to,
            continue_=continue_,
            dry_run=dry_run,
        )

    def release(self, voyage_id: str) -> None:
        """Release one terminal Voyage's durable working directory."""

        voyage = self._resolve(voyage_id)
        if voyage.get_status().terminal_result is None:
            raise VoyageNotTerminalError(
                f"Voyage {voyage_id!r} is not terminal and cannot be released"
            )
        self._release_voyage(voyage_id)
        if voyage_id in self._validated_voyage_ids(allow_empty=True):
            raise RutterDefinitionError(
                "release_voyage must remove the released Voyage ID"
            )

    def run(self, argv: list[str]) -> int:
        return voyage_dispenser_cli(self, argv)


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _instruction_json(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, Message):
        return {"kind": "message", **_plain_json(value.to_json())}
    if isinstance(value, MachineInstruction):
        return {
            "kind": "machine",
            "machine_id": value.machine_id,
            "mode": value.mode,
            "answer_format": _plain_json(value.answer_format),
        }
    raise TypeError("unsupported Rutter instruction")


def _status_json(voyage_id: str, status: VoyageStatus) -> dict[str, object]:
    evolution = status.current_evolution
    fault = status.fault
    return {
        "voyage_id": voyage_id,
        "evolution": {
            "rutter_id": evolution.rutter_id,
            "definition_version": evolution.definition_version,
            "evolution_id": evolution.evolution_id,
            "evolution_entry_id": evolution.evolution_entry_id,
            "depth": evolution.depth,
            "condition": evolution.condition,
        },
        "instruction": _instruction_json(status.instruction),
        "terminal_result": (
            None
            if status.terminal_result is None
            else _plain_json(status.terminal_result.to_json())
        ),
        "fault": (
            None
            if fault is None
            else {
                "category": fault.category,
                "evolution_id": fault.evolution_id,
                "evolution_entry_id": fault.evolution_entry_id,
                "target_evolution_id": fault.target_evolution_id,
                "transition_hook_ids": list(fault.transition_hook_ids),
            }
        ),
    }


def _parser(dispenser: VoyageDispenser) -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(
        prog=dispenser.prog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=_USAGE_GUIDANCE,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("help", help="Explain the multi-agent Voyage workflow.")
    commands.add_parser("modes", help="List initialization modes and explanations.")
    initiate = commands.add_parser(
        "initiate",
        help="Create every Voyage in one declared mode.",
    )
    initiate.add_argument(
        "mode",
        nargs="?",
        choices=tuple(dispenser.get_modes()),
    )
    initiate.add_argument(
        "--run-prefix",
        help="Isolate this run; defaults to the selected mode.",
    )
    argument_descriptions: dict[str, str] = {}
    for config in dispenser.get_modes().values():
        argument_descriptions.update(config["arguments"])
    for name, description in argument_descriptions.items():
        initiate.add_argument(
            f"--{name.replace('_', '-')}",
            dest=name,
            help=description,
        )
    list_command = commands.add_parser(
        "list", help="List every authorized voyage_id, optionally by run prefix."
    )
    list_command.add_argument("--run-prefix")
    status = commands.add_parser("status", help="Read one assigned Voyage.")
    status.add_argument("voyage_id")
    validate = commands.add_parser(
        "validate",
        help="Check a response for one assigned Voyage without mutation.",
    )
    validate.add_argument("voyage_id")
    validate.add_argument("--response-file", required=True)
    validate.add_argument("--responding-to")
    advance = commands.add_parser(
        "advance",
        help="Advance one assigned Voyage after validation.",
    )
    advance.add_argument("voyage_id")
    advance.add_argument("--response-file")
    advance.add_argument("--responding-to")
    release = commands.add_parser(
        "release",
        help="Delete one terminal Voyage working directory.",
    )
    release.add_argument("voyage_id")
    return parser


def _write_json(stream: object, value: object) -> None:
    json.dump(
        _plain_json(value),
        stream,
        sort_keys=True,
        separators=(",", ":"),
    )
    stream.write("\n")


def _read_json(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def voyage_dispenser_cli(
    dispenser: VoyageDispenser,
    argv: Sequence[str] | None = None,
) -> int:
    """Map the standard Voyage dispenser CLI to one configured dispenser."""

    try:
        arguments = _parser(dispenser).parse_args(
            list(argv) if argv is not None else None
        )
        if arguments.command == "help":
            payload: object = {"help": dispenser.help()}
        elif arguments.command == "modes":
            payload = {
                "default_mode": dispenser.get_default_mode(),
                "modes": dispenser.get_modes(),
            }
        elif arguments.command == "initiate":
            argument_names = {
                name
                for config in dispenser.get_modes().values()
                for name in config["arguments"]
            }
            supplied = {
                name: getattr(arguments, name)
                for name in argument_names
                if getattr(arguments, name) is not None
            }
            payload = {
                "voyage_ids": dispenser.initiate_voyages(
                    arguments.mode,
                    run_prefix=arguments.run_prefix,
                    **supplied,
                )
            }
        elif arguments.command == "list":
            payload = {
                "voyage_ids": dispenser.get_voyage_ids(arguments.run_prefix)
            }
            if arguments.run_prefix is not None:
                payload["run_prefix"] = arguments.run_prefix
        elif arguments.command == "status":
            payload = _status_json(
                arguments.voyage_id,
                dispenser.get_status(arguments.voyage_id),
            )
        elif arguments.command == "validate":
            report = dispenser.validate(
                arguments.voyage_id,
                _read_json(arguments.response_file),
                responding_to=arguments.responding_to,
            )
            payload = {
                "voyage_id": arguments.voyage_id,
                "validation": report.to_json(),
            }
        elif arguments.command == "release":
            dispenser.release(arguments.voyage_id)
            payload = {
                "voyage_id": arguments.voyage_id,
                "released": True,
            }
        else:
            if arguments.response_file is None:
                if arguments.responding_to is not None:
                    raise _UsageError(
                        "--responding-to requires --response-file"
                    )
                dispenser.advance(arguments.voyage_id)
            else:
                response = _read_json(arguments.response_file)
                report = dispenser.validate(
                    arguments.voyage_id,
                    response,
                    responding_to=arguments.responding_to,
                )
                if not report.valid:
                    raise _InvalidResponse(report)
                dispenser.advance(
                    arguments.voyage_id,
                    response,
                    responding_to=arguments.responding_to,
                )
            payload = _status_json(
                arguments.voyage_id,
                dispenser.get_status(arguments.voyage_id),
            )
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 0
    except _UsageError as error:
        payload = {"error": {"code": "usage-error", "message": str(error)}}
        exit_code = 2
    except UnknownVoyageError as error:
        payload = {"error": {"code": "unknown-voyage", "message": str(error)}}
        exit_code = 3
    except VoyagesNotInitializedError as error:
        payload = {"error": {"code": "not-initialized", "message": str(error)}}
        exit_code = 5
    except VoyagesAlreadyInitializedError as error:
        payload = {"error": {"code": "already-initialized", "message": str(error)}}
        exit_code = 5
    except UnknownVoyageModeError as error:
        payload = {"error": {"code": "unknown-mode", "message": str(error)}}
        exit_code = 2
    except InvalidVoyageModeArgumentsError as error:
        payload = {"error": {"code": "usage-error", "message": str(error)}}
        exit_code = 2
    except VoyageNotTerminalError as error:
        payload = {"error": {"code": "not-terminal", "message": str(error)}}
        exit_code = 5
    except _InvalidResponse as error:
        payload = {
            "error": {
                "code": "invalid-response",
                "message": str(error),
                "report": error.report.to_json(),
            }
        }
        exit_code = 4
    except json.JSONDecodeError:
        payload = {
            "error": {"code": "input-error", "message": "input is not valid JSON"}
        }
        exit_code = 3
    except OSError:
        payload = {
            "error": {
                "code": "input-error",
                "message": "filesystem input could not be read",
            }
        }
        exit_code = 3
    except Exception as error:
        payload = {
            "error": {
                "code": "internal-error",
                "message": f"unexpected {type(error).__name__}",
            }
        }
        exit_code = 1
    else:
        exit_code = 0
    _write_json(sys.stdout, payload)
    if exit_code:
        _write_json(sys.stderr, payload)
    return exit_code


__all__ = (
    "UnknownVoyageError",
    "UnknownVoyageModeError",
    "InvalidVoyageModeArgumentsError",
    "VoyageNotTerminalError",
    "VoyageDispenser",
    "VoyagesAlreadyInitializedError",
    "VoyagesNotInitializedError",
    "voyage_dispenser_cli",
)
