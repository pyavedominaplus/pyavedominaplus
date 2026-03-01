"""AVE DominaPlus Python SDK.

A Python library for communicating with AVE DominaPlus home automation systems
via WebSocket.
"""

from .client import AVEDominaClient
from .const import (
    DEFAULT_WS_PORT,
    DEVICE_TYPE_DIMMER,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_SCENARIO,
    DEVICE_TYPE_SHUTTER,
    DEVICE_TYPE_THERMOSTAT,
)
from .models import (
    DominaArea,
    DominaDevice,
    DominaMapCommand,
    DominaThermostat,
)
from .protocol import (
    build_crc,
    decode_message,
    encode_message,
)

__all__ = [
    "AVEDominaClient",
    "DominaArea",
    "DominaDevice",
    "DominaMapCommand",
    "DominaThermostat",
    "build_crc",
    "decode_message",
    "encode_message",
    "DEFAULT_WS_PORT",
    "DEVICE_TYPE_DIMMER",
    "DEVICE_TYPE_LIGHT",
    "DEVICE_TYPE_SCENARIO",
    "DEVICE_TYPE_SHUTTER",
    "DEVICE_TYPE_THERMOSTAT",
]

__version__ = "0.1.5"
