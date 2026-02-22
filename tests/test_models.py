"""Tests for AVE DominaPlus data models."""

from pyavedominaplus.models import (
    DominaArea,
    DominaDevice,
    DominaMapCommand,
    DominaThermostat,
    ThermoSeason,
)
from pyavedominaplus.const import (
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_DIMMER,
    DEVICE_TYPE_SHUTTER,
    DEVICE_TYPE_THERMOSTAT,
    DEVICE_TYPE_SCENARIO,
    DEVICE_TYPE_ENERGY,
    DEVICE_TYPE_LIGHT_22,
    DEVICE_TYPE_SHUTTER_16,
    DEVICE_TYPE_SHUTTER_19,
    APP_TYPE_LIGHT,
    APP_TYPE_SHUTTER,
    APP_TYPE_THERMOSTAT,
    APP_TYPE_SCENARIO,
)


class TestDominaDevice:
    """Tests for the DominaDevice model."""

    def test_light_device(self):
        dev = DominaDevice(
            id="100", name="Ceiling Light", device_type=DEVICE_TYPE_LIGHT
        )
        assert dev.is_light
        assert not dev.is_dimmer
        assert not dev.is_shutter
        assert not dev.is_thermostat
        assert dev.app_type == APP_TYPE_LIGHT

    def test_light_22_variant(self):
        dev = DominaDevice(
            id="106", name="Kitchen Light", device_type=DEVICE_TYPE_LIGHT_22
        )
        assert dev.is_light
        assert dev.app_type == APP_TYPE_LIGHT

    def test_dimmer_device(self):
        dev = DominaDevice(id="101", name="Floor Lamp", device_type=DEVICE_TYPE_DIMMER)
        assert dev.is_dimmer
        assert not dev.is_light
        assert dev.app_type == APP_TYPE_LIGHT  # Dimmers map to light app type

    def test_shutter_device(self):
        dev = DominaDevice(
            id="102", name="Window Blind", device_type=DEVICE_TYPE_SHUTTER
        )
        assert dev.is_shutter
        assert dev.app_type == APP_TYPE_SHUTTER

    def test_shutter_16_variant(self):
        dev = DominaDevice(id="107", name="Roller", device_type=DEVICE_TYPE_SHUTTER_16)
        assert dev.is_shutter
        assert dev.app_type == APP_TYPE_SHUTTER

    def test_shutter_19_variant(self):
        dev = DominaDevice(id="108", name="Blind", device_type=DEVICE_TYPE_SHUTTER_19)
        assert dev.is_shutter
        assert dev.app_type == APP_TYPE_SHUTTER

    def test_thermostat_device(self):
        dev = DominaDevice(
            id="103", name="Thermostat", device_type=DEVICE_TYPE_THERMOSTAT
        )
        assert dev.is_thermostat
        assert dev.app_type == APP_TYPE_THERMOSTAT

    def test_scenario_device(self):
        dev = DominaDevice(id="104", name="Night", device_type=DEVICE_TYPE_SCENARIO)
        assert dev.is_scenario
        assert dev.app_type == APP_TYPE_SCENARIO

    def test_energy_device(self):
        dev = DominaDevice(id="108", name="Meter", device_type=DEVICE_TYPE_ENERGY)
        assert dev.is_energy

    def test_is_on(self):
        dev = DominaDevice(
            id="100", name="Light", device_type=DEVICE_TYPE_LIGHT, current_value=0
        )
        assert not dev.is_on
        dev.update_status(1)
        assert dev.is_on

    def test_brightness_dimmer(self):
        dev = DominaDevice(
            id="101", name="Dimmer", device_type=DEVICE_TYPE_DIMMER, current_value=31
        )
        assert dev.brightness == 31

    def test_brightness_light(self):
        dev = DominaDevice(
            id="100", name="Light", device_type=DEVICE_TYPE_LIGHT, current_value=1
        )
        assert dev.brightness == 254  # Full brightness when on

    def test_brightness_light_off(self):
        dev = DominaDevice(
            id="100", name="Light", device_type=DEVICE_TYPE_LIGHT, current_value=0
        )
        assert dev.brightness == 0

    def test_update_status(self):
        dev = DominaDevice(id="100", name="Light", device_type=DEVICE_TYPE_LIGHT)
        assert dev.current_value == 0
        dev.update_status(1)
        assert dev.current_value == 1


class TestDominaThermostat:
    """Tests for the DominaThermostat model."""

    def test_default_values(self):
        thermo = DominaThermostat(id="103", name="Living Room")
        assert thermo.temperature == 0.0
        assert thermo.set_point == 0.0
        assert thermo.season == 0
        assert not thermo.is_off

    def test_heating_cooling(self):
        thermo = DominaThermostat(id="103", name="LR", season=ThermoSeason.WINTER)
        assert thermo.is_heating
        assert not thermo.is_cooling

        thermo.season = ThermoSeason.SUMMER
        assert thermo.is_cooling
        assert not thermo.is_heating

    def test_is_off(self):
        thermo = DominaThermostat(id="103", name="LR", local_off=1)
        assert thermo.is_off

    def test_update_temperature(self):
        thermo = DominaThermostat(id="103", name="LR")
        thermo.update_temperature("215")
        assert thermo.temperature == 21.5

    def test_update_set_point(self):
        thermo = DominaThermostat(id="103", name="LR")
        thermo.update_set_point("210")
        assert thermo.set_point == 21.0

    def test_update_offset(self):
        thermo = DominaThermostat(id="103", name="LR")
        thermo.update_offset("5")
        assert thermo.offset == 0.5

    def test_update_from_wts(self):
        """Test full WTS response parsing."""
        thermo = DominaThermostat(id="103", name="LR")
        records = [["1", "2", "6", "5", "1", "215", "1", "210", "0", "0"]]
        thermo.update_from_wts(records)
        assert thermo.temperature == 21.5
        assert thermo.set_point == 21.0
        assert thermo.offset == 0.5
        assert thermo.fan_level == 130  # 2 + 128 (winter)
        assert thermo.season == 1  # Winter
        assert thermo.mode == 1
        assert thermo.local_off == 0
        assert thermo.configuration == 6
        assert thermo.antifreeze == 0

    def test_update_from_wts_antifreeze(self):
        """Test WTS with antifreeze mode active."""
        thermo = DominaThermostat(id="103", name="LR")
        records = [["1", "2", "6", "5", "1", "215", "1", "210", "1", "0"]]
        thermo.update_from_wts(records)
        assert thermo.mode == 0x1F  # Antifreeze mode

    def test_update_from_wts_empty_records(self):
        """Test WTS with empty records doesn't crash."""
        thermo = DominaThermostat(id="103", name="LR")
        thermo.update_from_wts([])
        assert thermo.temperature == 0.0

    def test_update_from_wts_short_records(self):
        """Test WTS with too-short records doesn't crash."""
        thermo = DominaThermostat(id="103", name="LR")
        thermo.update_from_wts([["1", "2"]])
        assert thermo.temperature == 0.0


class TestDominaArea:
    """Tests for the DominaArea model."""

    def test_defaults(self):
        area = DominaArea(id="1", name="Living Room")
        assert area.is_visible
        assert area.map_commands == []
        assert area.order == "0"

    def test_with_commands(self):
        cmd = DominaMapCommand(
            command_id="1",
            command_name="Light",
            command_type=1,
            device_id="100",
        )
        area = DominaArea(id="1", name="Living Room", map_commands=[cmd])
        assert len(area.map_commands) == 1
        assert area.map_commands[0].device_id == "100"


class TestDominaMapCommand:
    """Tests for the DominaMapCommand model."""

    def test_creation(self):
        mc = DominaMapCommand(
            command_id="1",
            command_name="Ceiling Light",
            command_type=1,
            device_id="100",
            device_family=1,
            x="50",
            y="60",
        )
        assert mc.command_id == "1"
        assert mc.device_id == "100"
        assert mc.device_family == 1

    def test_defaults(self):
        mc = DominaMapCommand(
            command_id="1",
            command_name="Test",
            command_type=1,
        )
        assert mc.device_id == ""
        assert mc.device_family == 0
        assert mc.x == "0"
        assert mc.y == "0"
        assert mc.icon_default == ""
        assert mc.icon_current == ""


class TestDominaDeviceExtended:
    """Extended tests for DominaDevice."""

    def test_device_maps_field(self):
        """Test device maps string."""
        dev = DominaDevice(
            id="102", name="Blind", device_type=DEVICE_TYPE_SHUTTER, maps="1;2"
        )
        assert dev.maps == "1;2"

    def test_device_avebus_address(self):
        """Test avebus address field."""
        dev = DominaDevice(id="100", name="Light", device_type=DEVICE_TYPE_LIGHT)
        assert dev.avebus_address is None
        dev.avebus_address = 10
        assert dev.avebus_address == 10

    def test_device_commands_field(self):
        """Test commands list default."""
        dev = DominaDevice(id="100", name="Light", device_type=DEVICE_TYPE_LIGHT)
        assert dev.commands == []

    def test_brightness_dimmer_zero(self):
        """Test dimmer brightness when value is 0."""
        dev = DominaDevice(
            id="101", name="Dimmer", device_type=DEVICE_TYPE_DIMMER, current_value=0
        )
        assert dev.brightness == 0
        assert not dev.is_on

    def test_brightness_dimmer_max(self):
        """Test dimmer brightness at max."""
        dev = DominaDevice(
            id="101", name="Dimmer", device_type=DEVICE_TYPE_DIMMER, current_value=254
        )
        assert dev.brightness == 254

    def test_unknown_device_type_app_type(self):
        """Test app_type for an unknown device type returns the type itself."""
        dev = DominaDevice(id="200", name="Unknown", device_type=99)
        assert dev.app_type == 99
        assert not dev.is_light
        assert not dev.is_dimmer
        assert not dev.is_shutter
        assert not dev.is_thermostat
        assert not dev.is_scenario
        assert not dev.is_energy

    def test_energy_app_type(self):
        """Test energy device app_type mapping."""
        dev = DominaDevice(id="108", name="Meter", device_type=DEVICE_TYPE_ENERGY)
        from pyavedominaplus.const import APP_TYPE_ENERGY

        assert dev.app_type == APP_TYPE_ENERGY

    def test_update_status_multiple_times(self):
        """Test updating status multiple times."""
        dev = DominaDevice(id="100", name="Light", device_type=DEVICE_TYPE_LIGHT)
        dev.update_status(1)
        assert dev.is_on
        dev.update_status(0)
        assert not dev.is_on
        dev.update_status(254)
        assert dev.is_on


class TestDominaThermostatExtended:
    """Extended tests for DominaThermostat."""

    def test_vmc_daikin_flag(self):
        """Test VMC Daikin flag."""
        thermo = DominaThermostat(id="103", name="Daikin", is_vmc_daikin=True)
        assert thermo.is_vmc_daikin

    def test_humidity_fields(self):
        """Test humidity fields."""
        thermo = DominaThermostat(id="103", name="LR")
        assert thermo.humidity_value == 0
        assert thermo.humidity_threshold_l == 0
        assert thermo.humidity_threshold_m == 0
        assert thermo.humidity_threshold_h == 0
        thermo.humidity_value = 65
        thermo.humidity_threshold_l = 30
        thermo.humidity_threshold_m = 50
        thermo.humidity_threshold_h = 70
        assert thermo.humidity_value == 65

    def test_keyboard_lock(self):
        """Test keyboard lock field."""
        thermo = DominaThermostat(id="103", name="LR")
        assert thermo.keyboard_lock == 0
        thermo.keyboard_lock = 1
        assert thermo.keyboard_lock == 1

    def test_window_state(self):
        """Test window state field."""
        thermo = DominaThermostat(id="103", name="LR")
        assert thermo.window_state == 0
        thermo.window_state = 1
        assert thermo.window_state == 1

    def test_update_from_wts_summer(self):
        """Test WTS response parsing with summer season."""
        thermo = DominaThermostat(id="103", name="LR")
        records = [["1", "3", "6", "10", "0", "250", "2", "230", "0", "0"]]
        thermo.update_from_wts(records)
        assert thermo.season == 0  # Summer
        assert thermo.temperature == 25.0
        assert thermo.set_point == 23.0
        assert thermo.offset == 1.0
        assert thermo.fan_level == 3  # No +128 for summer
        assert thermo.mode == 2

    def test_update_from_wts_fan_off(self):
        """Test WTS response with fan_on=0 sets fan_level to 0."""
        thermo = DominaThermostat(id="103", name="LR")
        records = [["0", "3", "6", "5", "0", "215", "1", "210", "0", "0"]]
        thermo.update_from_wts(records)
        assert thermo.fan_level == 0  # fan_on=0 resets fan_level

    def test_update_from_wts_keyboard_lock_bit(self):
        """Test WTS extracts keyboard lock from configuration."""
        thermo = DominaThermostat(id="103", name="LR")
        # configuration=0x46 (0b01000110): keyboard_lock bit 6 is set
        records = [["1", "2", "70", "5", "1", "215", "1", "210", "0", "0"]]
        thermo.update_from_wts(records)
        assert thermo.keyboard_lock == 1
        assert thermo.configuration == 70

    def test_update_from_wts_window_visibility_bit(self):
        """Test WTS extracts window visibility from configuration."""
        thermo = DominaThermostat(id="103", name="LR")
        # configuration=0x86 (134): window_visibility bit 7 is set
        records = [["1", "2", "134", "5", "1", "215", "1", "210", "0", "0"]]
        thermo.update_from_wts(records)
        assert thermo.window_visibility == 1

    def test_update_from_wts_antifreeze_config(self):
        """Test WTS antifreeze derived from configuration."""
        thermo = DominaThermostat(id="103", name="LR")
        # configuration=7 (odd -> antifreeze=1)
        records = [["1", "2", "7", "5", "1", "215", "1", "210", "0", "0"]]
        thermo.update_from_wts(records)
        assert thermo.antifreeze == 1

    def test_update_from_wts_local_off(self):
        """Test WTS with local_off set."""
        thermo = DominaThermostat(id="103", name="LR")
        records = [["1", "2", "6", "5", "1", "215", "1", "210", "0", "1"]]
        thermo.update_from_wts(records)
        assert thermo.local_off == 1
        assert thermo.is_off

    def test_update_from_wts_none_records(self):
        """Test WTS with None records list."""
        thermo = DominaThermostat(id="103", name="LR")
        thermo.update_from_wts(None)
        assert thermo.temperature == 0.0

    def test_update_from_wts_empty_inner_list(self):
        """Test WTS with empty inner list."""
        thermo = DominaThermostat(id="103", name="LR")
        thermo.update_from_wts([[]])
        assert thermo.temperature == 0.0

    def test_update_temperature_negative(self):
        """Test negative raw temperature value."""
        thermo = DominaThermostat(id="103", name="LR")
        thermo.update_temperature("-50")
        assert thermo.temperature == -5.0

    def test_update_set_point_high(self):
        """Test high set point value."""
        thermo = DominaThermostat(id="103", name="LR")
        thermo.update_set_point("400")
        assert thermo.set_point == 40.0


class TestEnums:
    """Tests for enum types."""

    def test_device_family_values(self):
        from pyavedominaplus.models import DeviceFamily

        assert DeviceFamily.LIGHT == 1
        assert DeviceFamily.DIMMER == 2
        assert DeviceFamily.SHUTTER == 3
        assert DeviceFamily.THERMOSTAT == 4
        assert DeviceFamily.SCENARIO == 6
        assert DeviceFamily.ENERGY == 9
        assert DeviceFamily.SHUTTER_16 == 16
        assert DeviceFamily.SHUTTER_19 == 19
        assert DeviceFamily.LIGHT_22 == 22
        assert DeviceFamily.AUDIO == 14
        assert DeviceFamily.ABANO == 17
        assert DeviceFamily.P3000_AREA == 12
        assert DeviceFamily.P3000_SENSOR == 13

    def test_thermo_season_values(self):
        assert ThermoSeason.SUMMER == 0
        assert ThermoSeason.WINTER == 1
