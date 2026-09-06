"""AVE DominaPlus Python SDK.

A Python library for communicating with AVE DominaPlus home automation systems
via WebSocket.
"""

from .client import AVEDominaClient, UpdateCallback
from .const import (
    CONN_STATUS_CLOSE,
    CONN_STATUS_ERROR,
    CONN_STATUS_OPEN,
    DEFAULT_WS_PORT,
    DEVICE_TYPE_DIMMER,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_SCENARIO,
    DEVICE_TYPE_SHUTTER,
    DEVICE_TYPE_THERMOSTAT,
    EVENT_DEVICE_STATUS,
    EVENT_HUMIDITY,
    EVENT_LDI_LOADED,
    EVENT_LM_LOADED,
    EVENT_LMC_LOADED,
    EVENT_RGB,
    EVENT_THERMOSTAT_FAN_LEVEL,
    EVENT_THERMOSTAT_FULL_STATUS,
    EVENT_THERMOSTAT_FUNCTION,
    EVENT_THERMOSTAT_KEYBOARD_LOCK,
    EVENT_THERMOSTAT_LOCAL_OFF,
    EVENT_THERMOSTAT_MODE,
    EVENT_THERMOSTAT_OFFSET,
    EVENT_THERMOSTAT_REQUEST,
    EVENT_THERMOSTAT_SEASON,
    EVENT_THERMOSTAT_SETPOINT,
    EVENT_THERMOSTAT_TEMPERATURE,
    EVENT_THERMOSTAT_WINDOW,
    SEASON_SUMMER,
    SEASON_WINTER,
    THERMOSTAT_MODE_AUTO,
    THERMOSTAT_MODE_MANUAL,
)
from .exceptions import (
    AVEDominaConnectionError,
    AVEDominaError,
    AVEDominaTimeoutError,
)
from .measure import (
    DEFAULT_PHASE_TIMEOUT,
    ShutterTravelMeasurement,
    measure_shutter_travel_times,
)
from .models import (
    DominaArea,
    DominaDevice,
    DominaMapCommand,
    DominaThermostat,
)
from .protocol import (
    ProtocolDecoder,
    build_crc,
    decode_message,
    encode_message,
)
from .travel import ShutterTravelEstimator

__all__ = [
    "CONN_STATUS_CLOSE",
    "CONN_STATUS_ERROR",
    "CONN_STATUS_OPEN",
    "DEFAULT_PHASE_TIMEOUT",
    "DEFAULT_WS_PORT",
    "DEVICE_TYPE_DIMMER",
    "DEVICE_TYPE_LIGHT",
    "DEVICE_TYPE_SCENARIO",
    "DEVICE_TYPE_SHUTTER",
    "DEVICE_TYPE_THERMOSTAT",
    "EVENT_DEVICE_STATUS",
    "EVENT_HUMIDITY",
    "EVENT_LDI_LOADED",
    "EVENT_LMC_LOADED",
    "EVENT_LM_LOADED",
    "EVENT_RGB",
    "EVENT_THERMOSTAT_FAN_LEVEL",
    "EVENT_THERMOSTAT_FULL_STATUS",
    "EVENT_THERMOSTAT_FUNCTION",
    "EVENT_THERMOSTAT_KEYBOARD_LOCK",
    "EVENT_THERMOSTAT_LOCAL_OFF",
    "EVENT_THERMOSTAT_MODE",
    "EVENT_THERMOSTAT_OFFSET",
    "EVENT_THERMOSTAT_REQUEST",
    "EVENT_THERMOSTAT_SEASON",
    "EVENT_THERMOSTAT_SETPOINT",
    "EVENT_THERMOSTAT_TEMPERATURE",
    "EVENT_THERMOSTAT_WINDOW",
    "SEASON_SUMMER",
    "SEASON_WINTER",
    "THERMOSTAT_MODE_AUTO",
    "THERMOSTAT_MODE_MANUAL",
    "AVEDominaClient",
    "AVEDominaConnectionError",
    "AVEDominaError",
    "AVEDominaTimeoutError",
    "DominaArea",
    "DominaDevice",
    "DominaMapCommand",
    "DominaThermostat",
    "ProtocolDecoder",
    "ShutterTravelEstimator",
    "ShutterTravelMeasurement",
    "UpdateCallback",
    "build_crc",
    "decode_message",
    "encode_message",
    "measure_shutter_travel_times",
]

__version__ = "0.2.1"
