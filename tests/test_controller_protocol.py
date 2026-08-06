"""Behavioral tests for the controller wire-message foundation."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Mapping

import pytest


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from officina.common.controller import JsonValue
from officina.common.controller_protocol import (
    ControllerMessage,
    InvalidControllerMessageError,
    UnsupportedProtocolVersionError,
)


@dataclass(frozen=True)
class ExampleMessage(ControllerMessage):
    """Minimal concrete message used to exercise the shared JSON behavior."""

    message_type: ClassVar[str] = "example"

    protocol_version: int
    value: str

    def to_mapping(self) -> Mapping[str, JsonValue]:
        return {
            "message_type": self.message_type,
            "protocol_version": self.protocol_version,
            "value": self.value,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, JsonValue]) -> ExampleMessage:
        return cls(
            protocol_version=value["protocol_version"],  # type: ignore[arg-type]
            value=value["value"],  # type: ignore[arg-type]
        )


def test_message_json_is_canonical_and_round_trips_through_subclass() -> None:
    message = ExampleMessage(protocol_version=1, value="café")

    encoded = message.to_json()

    assert encoded == (
        '{"message_type":"example","protocol_version":1,"value":"café"}'
    )
    assert ExampleMessage.from_json(encoded) == message


@pytest.mark.parametrize(
    "encoded",
    [
        '{"message_type":"example","message_type":"example",'
        '"protocol_version":1,"value":"x"}',
        '[{"message_type":"example","protocol_version":1,"value":"x"}]',
        '{"message_type":"example","protocol_version":1,"value":NaN}',
    ],
)
def test_from_json_rejects_invalid_common_json_shapes(encoded: str) -> None:
    with pytest.raises(InvalidControllerMessageError):
        ExampleMessage.from_json(encoded)


def test_from_json_rejects_message_type_mismatch_before_payload_conversion() -> None:
    encoded = '{"message_type":"other","protocol_version":1,"value":"x"}'

    with pytest.raises(InvalidControllerMessageError, match="message_type"):
        ExampleMessage.from_json(encoded)


def test_from_json_rejects_unsupported_protocol_version() -> None:
    encoded = '{"message_type":"example","protocol_version":2,"value":"x"}'

    with pytest.raises(UnsupportedProtocolVersionError):
        ExampleMessage.from_json(encoded)
