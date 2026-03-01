"""Data models for AVE DominaPlus devices."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from .const import (
    DEVICE_TYPE_DIMMER,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_LIGHT_22,
    DEVICE_TYPE_SHUTTER,
    DEVICE_TYPE_SHUTTER_16,
    DEVICE_TYPE_SHUTTER_19,
    DEVICE_TYPE_THERMOSTAT,
    DEVICE_TYPE_SCENARIO,
    DEVICE_TYPE_ENERGY,
    DEVICE_TYPE_TO_APP_TYPE,
    SHUTTER_STATUS_OPEN,
    SHUTTER_STATUS_OPENING,
    SHUTTER_STATUS_CLOSED,
    SHUTTER_STATUS_CLOSING,
    THERMOSTAT_MODE_AUTO,
    THERMOSTAT_MODE_ANTIFREEZE,
    THERMOSTAT_MODE_MANUAL,
)


class DeviceFamily(IntEnum):
    """Device family types."""

    LIGHT = 1
    DIMMER = 2
    SHUTTER = 3
    THERMOSTAT = 4
    ECONOMIZER = 5
    SCENARIO = 6
    ENERGY = 9
    P3000_AREA = 12
    P3000_SENSOR = 13
    AUDIO = 14
    SHUTTER_16 = 16
    ABANO = 17
    SHUTTER_19 = 19
    LIGHT_22 = 22


class ThermoSeason(IntEnum):
    """Thermostat season values."""

    SUMMER = 0
    WINTER = 1


@dataclass
class DominaDevice:
    """Represents a device in the DominaPlus system."""

    id: str
    name: str
    device_type: int
    maps: str = ""
    current_value: int = 0
    avebus_address: int | None = None
    commands: list[int] = field(default_factory=list)

    @property
    def app_type(self) -> int:
        """Return the normalized application type."""
        return DEVICE_TYPE_TO_APP_TYPE.get(self.device_type, self.device_type)

    @property
    def is_light(self) -> bool:
        return self.device_type in (
            DEVICE_TYPE_LIGHT,
            DEVICE_TYPE_LIGHT_22,
        )

    @property
    def is_dimmer(self) -> bool:
        return self.device_type == DEVICE_TYPE_DIMMER

    @property
    def is_shutter(self) -> bool:
        return self.device_type in (
            DEVICE_TYPE_SHUTTER,
            DEVICE_TYPE_SHUTTER_16,
            DEVICE_TYPE_SHUTTER_19,
        )

    @property
    def is_thermostat(self) -> bool:
        return self.device_type == DEVICE_TYPE_THERMOSTAT

    @property
    def is_scenario(self) -> bool:
        return self.device_type == DEVICE_TYPE_SCENARIO

    @property
    def is_energy(self) -> bool:
        return self.device_type == DEVICE_TYPE_ENERGY

    @property
    def is_on(self) -> bool:
        """Return True if the device is on (for lights/switches)."""
        return self.current_value > 0

    @property
    def is_opening(self) -> bool:
        """Return True if shutter is opening (status 2)."""
        return self.is_shutter and self.current_value == SHUTTER_STATUS_OPENING

    @property
    def is_open(self) -> bool:
        """Return True if shutter is open (status 1)."""
        return self.is_shutter and self.current_value == SHUTTER_STATUS_OPEN

    @property
    def is_closing(self) -> bool:
        """Return True if shutter is closing (status 4)."""
        return self.is_shutter and self.current_value == SHUTTER_STATUS_CLOSING

    @property
    def is_closed(self) -> bool:
        """Return True if shutter is closed (status 3)."""
        return self.is_shutter and self.current_value == SHUTTER_STATUS_CLOSED

    @property
    def brightness(self) -> int:
        """Return the brightness level (0-31) for dimmers."""
        return self.current_value if self.is_dimmer else (31 if self.is_on else 0)

    def update_status(self, value: int) -> None:
        """Update the device status value."""
        self.current_value = value


@dataclass
class DominaThermostat:
    """Represents a thermostat device with extended properties."""

    id: str
    name: str
    temperature: float = 0.0
    set_point: float = 0.0
    offset: float = 0.0
    fan_level: int = 0
    season: int = 0
    mode: int = 0
    local_off: int = 0
    window_visibility: int = 0
    window_state: int = 0
    keyboard_lock: int = 0
    configuration: int = 0
    antifreeze: int = 0
    is_vmc_daikin: bool = False
    # Humidity probe fields
    humidity_enabled: bool = False
    humidity_value: int = 0
    humidity_threshold_l: int = 0
    humidity_threshold_m: int = 0
    humidity_threshold_h: int = 0

    @property
    def is_heating(self) -> bool:
        return self.season == ThermoSeason.WINTER

    @property
    def is_cooling(self) -> bool:
        return self.season == ThermoSeason.SUMMER

    @property
    def is_off(self) -> bool:
        return self.local_off == 1

    @property
    def is_auto_mode(self) -> bool:
        return self.mode == THERMOSTAT_MODE_AUTO

    @property
    def is_manual_mode(self) -> bool:
        return self.mode == THERMOSTAT_MODE_MANUAL

    @property
    def is_antifreeze_mode(self) -> bool:
        return self.mode == THERMOSTAT_MODE_ANTIFREEZE

    def update_temperature(self, raw_value: str) -> None:
        """Update temperature from raw protocol value (tenths of degree)."""
        self.temperature = int(raw_value) / 10.0

    def update_set_point(self, raw_value: str) -> None:
        """Update set point from raw protocol value (tenths of degree)."""
        self.set_point = int(raw_value) / 10.0

    def update_offset(self, raw_value: str) -> None:
        """Update offset from raw protocol value (tenths of degree)."""
        self.offset = int(raw_value) / 10.0

    def update_from_wts(self, records: list[list[str]]) -> None:
        """Update all fields from a WTS response record.

        Record format: [fan_on, fan_level, configuration, offset,
                        season_bits, temperature, mode, set_point,
                        antifreeze, local_off]

        Configuration bits:
            bit 0     : antifreeze capability
            bits 1-3  : thermostat type (0=ABTM03B, 1=ABTM_SO, 2=ABTMH_SO,
                        3=44CRTW, 4=ABTM03C, 5=ABCRT)
            bit 5     : IoT style thermostat
            bit 6     : keyboard lock
            bit 7     : window sensor visibility

        Season bits:
            bit 0     : season (0=summer, 1=winter)
            bit 4     : humidity probe enabled
        """
        if not records or not records[0] or len(records[0]) < 10:
            return

        r = records[0]
        fan_on = int(r[0])
        self.fan_level = int(r[1])
        if fan_on == 0:
            self.fan_level = 0
        self.configuration = int(r[2])
        self.offset = int(r[3]) / 10.0
        season_bits = int(r[4])
        self.season = season_bits & 0x01
        self.humidity_enabled = bool(season_bits & 0x10)
        if self.season == 1:
            self.fan_level += 128
        self.temperature = int(r[5]) / 10.0
        self.mode = int(r[6])
        self.set_point = int(r[7]) / 10.0
        if int(r[8]) == 1:
            self.mode = 0x1F  # Antifreeze mode
        self.local_off = int(r[9])
        self.antifreeze = self.configuration % 2
        # Extract thermostat type from configuration bits 1-3
        # Types 2 (ABTMH_SO), 3 (44CRTW), 5 (ABCRT) support humidity probes
        thermostat_type = (self.configuration & 0x0E) >> 1
        if thermostat_type == 2:
            self.humidity_enabled = True
        elif thermostat_type in (3, 5) and self.humidity_enabled:
            pass  # Already set from season_bits
        self.keyboard_lock = (self.configuration & 0x40) >> 6
        self.window_visibility = (self.configuration & 0x80) >> 7


@dataclass
class DominaArea:
    """Represents an area/map in the DominaPlus system."""

    id: str
    name: str
    order: str = "0"
    is_visible: bool = True
    map_commands: list[DominaMapCommand] = field(default_factory=list)


@dataclass
class DominaMapCommand:
    """Represents a command on a map (e.g., a button/icon for a device)."""

    command_id: str
    command_name: str
    command_type: int
    device_id: str = ""
    device_family: int = 0
    x: str = "0"
    y: str = "0"
    icon_default: str = ""
    icon_current: str = ""
