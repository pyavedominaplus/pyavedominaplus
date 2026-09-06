"""Tests for AVE DominaPlus async client using mock server."""

import asyncio

import pytest
import pytest_asyncio

from pyavedominaplus.client import AVEDominaClient
from pyavedominaplus.const import THERMOSTAT_MODE_AUTO, THERMOSTAT_MODE_MANUAL
from tests.mock_server import MockDominaServer


@pytest_asyncio.fixture
async def mock_server():
    """Start a mock server and yield it, stopping on cleanup."""
    server = MockDominaServer()
    await server.start()
    yield server
    await server.stop()


@pytest_asyncio.fixture
async def client(mock_server):
    """Create and connect a client to the mock server."""
    c = AVEDominaClient(host="127.0.0.1", port=mock_server.port, command_delay=0)
    await c.connect()
    yield c
    await c.disconnect()


class TestClientConnection:
    """Tests for client connection management."""

    async def test_connect_disconnect(self, mock_server):
        """Test basic connect and disconnect."""
        client = AVEDominaClient(
            host="127.0.0.1", port=mock_server.port, command_delay=0
        )
        await client.connect()
        assert client.connected
        await client.disconnect()
        assert not client.connected

    async def test_url_property(self, mock_server):
        """Test URL construction."""
        client = AVEDominaClient(host="192.168.1.100", port=14001)
        assert client.url == "ws://192.168.1.100:14001"

    async def test_connection_callback(self, mock_server):
        """Test connection status callback."""
        statuses = []
        client = AVEDominaClient(
            host="127.0.0.1", port=mock_server.port, command_delay=0
        )
        client.register_connection_callback(lambda s: statuses.append(s))
        await client.connect()
        assert "OPEN" in statuses
        await client.disconnect()
        assert "CLOSE" in statuses


class TestClientInitialization:
    """Tests for client data initialization."""

    async def test_initialize_loads_areas(self, client):
        """Test that initialization loads area data."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        assert len(client.areas) == 3
        assert "1" in client.areas
        assert client.areas["1"].name == "Living Room"

    async def test_initialize_loads_devices(self, client):
        """Test that initialization loads device data."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        assert len(client.devices) > 0
        assert "100" in client.devices
        assert client.devices["100"].name == "Ceiling Light"
        assert client.devices["100"].device_type == 1

    async def test_initialize_loads_thermostats(self, client):
        """Test that initialization creates thermostat objects."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        assert "103" in client.thermostats
        thermo = client.thermostats["103"]
        assert thermo.name == "Thermostat LR"
        assert thermo.temperature == 21.5
        assert thermo.set_point == 21.0

    async def test_initialize_loads_map_commands(self, client):
        """Test that initialization loads map commands."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        area = client.areas.get("1")
        assert area is not None
        assert len(area.map_commands) == 4

    async def test_device_statuses_populated_after_init(self, client, mock_server):
        """Test that device statuses are populated when wait_for_initialization returns."""
        mock_server.device_statuses["100"] = 1  # light on
        mock_server.device_statuses["102"] = 3  # shutter closed
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        assert client.devices["100"].current_value == 1
        assert client.devices["102"].current_value == 3


class TestClientDeviceControl:
    """Tests for device control commands."""

    async def test_turn_on_light(self, client, mock_server):
        """Test turning on a light."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await client.turn_on_light("100")
        await asyncio.sleep(0.2)

        assert mock_server.device_statuses["100"] == 1
        # Check that we got a status update
        status_events = [
            e for e in events if e[0] == "device_status" and e[1]["device_id"] == "100"
        ]
        assert len(status_events) > 0
        assert status_events[-1][1]["status"] == 1

    async def test_turn_off_light(self, client, mock_server):
        """Test turning off a light."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        mock_server.device_statuses["105"] = 1
        await client.turn_off_light("105")
        await asyncio.sleep(0.2)

        assert mock_server.device_statuses["105"] == 0

    async def test_set_dimmer_level(self, client, mock_server):
        """Test setting dimmer level."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.set_dimmer_level("101", 31)
        await asyncio.sleep(0.2)

        assert mock_server.device_statuses["101"] == 31

    async def test_dimmer_level_clamped(self, client, mock_server):
        """Test that dimmer level is clamped to the AVE 0-31 range."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.set_dimmer_level("101", 300)
        await asyncio.sleep(0.2)
        assert mock_server.device_statuses["101"] == 31

    async def test_open_shutter(self, client, mock_server):
        """Test opening a shutter (EAI command, status OPENING=2)."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.open_shutter("102")
        await asyncio.sleep(0.2)
        assert mock_server.device_statuses["102"] == 2  # OPENING

    async def test_close_shutter(self, client, mock_server):
        """Test closing a shutter (EAI command, status CLOSING=4)."""
        mock_server.set_shutter_position("102", 1.0)  # start open
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.close_shutter("102")
        await asyncio.sleep(0.2)
        assert mock_server.device_statuses["102"] == 4  # CLOSING

    async def test_stop_shutter_while_opening(self, client, mock_server):
        """Stop shutter while opening re-sends open command, status becomes 5."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.open_shutter("102")
        await asyncio.sleep(0.2)
        assert mock_server.device_statuses["102"] == 2  # OPENING

        await client.stop_shutter("102")
        await asyncio.sleep(0.2)
        assert mock_server.device_statuses["102"] == 5  # STOPPED
        device = client.devices["102"]
        assert device.is_stopped

    async def test_stop_shutter_while_closing(self, client, mock_server):
        """Stop shutter while closing re-sends close command, status becomes 5."""
        mock_server.set_shutter_position("102", 1.0)  # start open
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.close_shutter("102")
        await asyncio.sleep(0.2)
        assert mock_server.device_statuses["102"] == 4  # CLOSING

        await client.stop_shutter("102")
        await asyncio.sleep(0.2)
        assert mock_server.device_statuses["102"] == 5  # STOPPED
        device = client.devices["102"]
        assert device.is_stopped

    async def test_stop_shutter_when_not_moving(self, client, mock_server):
        """Stop shutter when not moving does nothing."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        await asyncio.sleep(0.5)

        cmds_before = len(mock_server.received_commands)
        await client.stop_shutter("102")
        await asyncio.sleep(0.2)
        # No EAI command should have been sent
        eai_cmds = [
            c
            for c in mock_server.received_commands[cmds_before:]
            if c["command"] == "EAI"
        ]
        assert len(eai_cmds) == 0

    async def test_shutter_transitions_to_open(self):
        """Shutter transitions from OPENING to OPEN after transition time."""
        server = MockDominaServer(shutter_transition_time=0.3)
        await server.start()
        try:
            client = AVEDominaClient(
                host="127.0.0.1",
                port=server.port,
                auto_reconnect=False,
                command_delay=0,
            )
            await client.connect()
            await client.initialize()
            await client.wait_for_initialization(timeout=5.0)

            await client.open_shutter("102")
            await asyncio.sleep(0.1)
            assert server.device_statuses["102"] == 2  # OPENING

            # Wait for transition
            await asyncio.sleep(0.5)
            assert server.device_statuses["102"] == 1  # OPEN
            assert client.devices["102"].is_open

            await client.disconnect()
        finally:
            await server.stop()

    async def test_shutter_transitions_to_closed(self):
        """Shutter transitions from CLOSING to CLOSED after transition time."""
        server = MockDominaServer(shutter_transition_time=0.3)
        server.set_shutter_position("102", 1.0)  # start open
        await server.start()
        try:
            client = AVEDominaClient(
                host="127.0.0.1",
                port=server.port,
                auto_reconnect=False,
                command_delay=0,
            )
            await client.connect()
            await client.initialize()
            await client.wait_for_initialization(timeout=5.0)

            await client.close_shutter("102")
            await asyncio.sleep(0.1)
            assert server.device_statuses["102"] == 4  # CLOSING

            await asyncio.sleep(0.5)
            assert server.device_statuses["102"] == 3  # CLOSED
            assert client.devices["102"].is_closed

            await client.disconnect()
        finally:
            await server.stop()

    async def test_shutter_stop_cancels_transition(self):
        """Stopping a shutter cancels the pending transition to final state."""
        server = MockDominaServer(shutter_transition_time=0.5)
        await server.start()
        try:
            client = AVEDominaClient(
                host="127.0.0.1",
                port=server.port,
                auto_reconnect=False,
                command_delay=0,
            )
            await client.connect()
            await client.initialize()
            await client.wait_for_initialization(timeout=5.0)

            await client.open_shutter("102")
            await asyncio.sleep(0.1)
            assert server.device_statuses["102"] == 2  # OPENING

            await client.stop_shutter("102")
            await asyncio.sleep(0.1)
            assert server.device_statuses["102"] == 5  # STOPPED

            # Wait past transition time — should stay stopped
            await asyncio.sleep(0.7)
            assert server.device_statuses["102"] == 5  # Still STOPPED

            await client.disconnect()
        finally:
            await server.stop()

    async def test_activate_scenario(self, client, mock_server):
        """Test activating a scenario via ES command using map command lookup."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.activate_scenario("104")
        await asyncio.sleep(0.2)
        assert mock_server.device_statuses["104"] == 1


class TestClientThermostatControl:
    """Tests for thermostat control."""

    async def test_set_thermostat_set_point(self, client, mock_server):
        """Test setting thermostat target temperature."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await client.set_thermostat_set_point("103", 22.5)
        await asyncio.sleep(0.2)

        # Check that STS command was sent
        sts_cmds = [c for c in mock_server.received_commands if c["command"] == "STS"]
        assert len(sts_cmds) > 0
        assert sts_cmds[-1]["parameters"] == ["103"]
        assert sts_cmds[-1]["records"][0][2] == "225"  # 22.5 * 10

    async def test_set_thermostat_season(self, client, mock_server):
        """Test setting thermostat season."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.set_thermostat_season("103", 0)  # Summer
        await asyncio.sleep(0.2)

        sts_cmds = [c for c in mock_server.received_commands if c["command"] == "STS"]
        assert len(sts_cmds) > 0
        # Season should be "0" (summer)
        last_cmd = sts_cmds[-1]
        assert last_cmd["records"][0][0] == "0"


class TestClientUpdates:
    """Tests for real-time update handling."""

    async def test_device_status_update(self, client, mock_server):
        """Test receiving a device status update."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        # Allow any remaining WSF responses to flush
        await asyncio.sleep(0.5)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        # Simulate server pushing an update
        await mock_server.send_update("upd", ["WS", "1", "100", "1"])
        await asyncio.sleep(0.2)

        status_events = [
            e for e in events if e[0] == "device_status" and e[1]["device_id"] == "100"
        ]
        assert len(status_events) > 0
        assert status_events[-1][1]["status"] == 1

    async def test_thermostat_temperature_update(self, client, mock_server):
        """Test receiving a thermostat temperature update."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await mock_server.send_update("upd", ["WT", "T", "103", "225"])
        await asyncio.sleep(0.2)

        temp_events = [e for e in events if e[0] == "thermostat_temperature"]
        assert len(temp_events) > 0
        assert client.thermostats["103"].temperature == 22.5

    async def test_thermostat_season_update(self, client, mock_server):
        """Test receiving a thermostat season update."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await mock_server.send_update("upd", ["WT", "S", "103", "0"])
        await asyncio.sleep(0.2)

        assert client.thermostats["103"].season == 0  # Summer

    async def test_ping_pong(self, client, mock_server):
        """Test that client responds to server ping with pong."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # Clear received commands
        mock_server.received_commands.clear()

        await mock_server.send_update("ping")
        await asyncio.sleep(0.2)

        pong_cmds = [c for c in mock_server.received_commands if c["command"] == "PONG"]
        assert len(pong_cmds) > 0

    async def test_unregister_callback(self, client, mock_server):
        """Test unregistering an update callback."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        unregister = client.register_update_callback(lambda t, d: events.append((t, d)))

        await mock_server.send_update("upd", ["WS", "1", "100", "1"])
        await asyncio.sleep(0.2)
        count_before = len(events)
        assert count_before > 0

        unregister()

        await mock_server.send_update("upd", ["WS", "1", "100", "0"])
        await asyncio.sleep(0.2)
        assert len(events) == count_before  # No new events

    async def test_thermostat_offset_update(self, client, mock_server):
        """Test receiving a thermostat offset update."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await mock_server.send_update("upd", ["WT", "O", "103", "10"])
        await asyncio.sleep(0.2)

        offset_events = [e for e in events if e[0] == "thermostat_offset"]
        assert len(offset_events) > 0
        assert client.thermostats["103"].offset == 1.0

    async def test_thermostat_fan_level_update(self, client, mock_server):
        """Test receiving a thermostat fan level update."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await mock_server.send_update("upd", ["WT", "L", "103", "3"])
        await asyncio.sleep(0.2)

        fan_events = [e for e in events if e[0] == "thermostat_fan_level"]
        assert len(fan_events) > 0
        assert client.thermostats["103"].fan_level == 3

    async def test_thermostat_local_off_update(self, client, mock_server):
        """Test receiving a thermostat local off update."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await mock_server.send_update("upd", ["WT", "Z", "103", "1"])
        await asyncio.sleep(0.2)

        off_events = [e for e in events if e[0] == "thermostat_local_off"]
        assert len(off_events) > 0
        assert client.thermostats["103"].local_off == 1

    async def test_thermostat_setpoint_update(self, client, mock_server):
        """Test receiving a thermostat set point update via UPD TP."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await mock_server.send_update("upd", ["TP", "103", "225"])
        await asyncio.sleep(0.2)

        sp_events = [e for e in events if e[0] == "thermostat_setpoint"]
        assert len(sp_events) > 0
        assert client.thermostats["103"].set_point == 22.5

    async def test_thermostat_mode_update(self, client, mock_server):
        """Test receiving a thermostat mode update via UPD TM."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await mock_server.send_update("upd", ["TM", "103", "2"])
        await asyncio.sleep(0.2)

        mode_events = [e for e in events if e[0] == "thermostat_mode"]
        assert len(mode_events) > 0
        assert client.thermostats["103"].mode == 2

    async def test_thermostat_keyboard_lock_update(self, client, mock_server):
        """Test receiving a thermostat keyboard lock update via UPD TK."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await mock_server.send_update("upd", ["TK", "103", "1"])
        await asyncio.sleep(0.2)

        assert client.thermostats["103"].keyboard_lock == 1

    async def test_thermostat_window_update(self, client, mock_server):
        """Test receiving a thermostat window state update via UPD TW."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await mock_server.send_update("upd", ["TW", "103", "1"])
        await asyncio.sleep(0.2)

        assert client.thermostats["103"].window_state == 1

    async def test_humidity_update(self, client, mock_server):
        """Test receiving a humidity update via UPD UMI."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        # UMI needs at least 11 parameters
        await mock_server.send_update(
            "upd", ["UMI", "103", "65", "30", "50", "70", "0", "0", "0", "0", "0"]
        )
        await asyncio.sleep(0.2)

        hum_events = [e for e in events if e[0] == "humidity"]
        assert len(hum_events) > 0
        assert hum_events[0][1]["humidity"] == 65
        assert client.thermostats["103"].humidity_value == 65
        assert client.thermostats["103"].humidity_enabled is True

    async def test_humidity_update_sets_enabled(self, client, mock_server):
        """Test that receiving UMI update sets humidity_enabled flag."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        assert client.thermostats["103"].humidity_enabled is False

        # UMI with just 6 parameters (minimum)
        await mock_server.send_update("upd", ["UMI", "103", "55", "20", "40", "60"])
        await asyncio.sleep(0.2)

        assert client.thermostats["103"].humidity_enabled is True
        assert client.thermostats["103"].humidity_value == 55

    async def test_map_based_local_off_update(self, client, mock_server):
        """Test TLO (thermostat local off from map) with inverted value.

        Map command "8" -> device "103" (thermostat).
        TLO value is inverted: server sends 0 means OFF (local_off=1).
        """
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        # TLO with value 0 -> inverted to local_off=1 (OFF)
        await mock_server.send_update("upd", ["TLO", "8", "0"])
        await asyncio.sleep(0.2)

        off_events = [e for e in events if e[0] == "thermostat_local_off"]
        assert len(off_events) > 0
        assert client.thermostats["103"].local_off == 1

    async def test_map_based_local_off_update_inverted(self, client, mock_server):
        """Test TLO value inversion: server sends 1 means ON (local_off=0)."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        client.thermostats["103"].local_off = 1  # Start off

        await mock_server.send_update("upd", ["TLO", "8", "1"])
        await asyncio.sleep(0.2)

        assert client.thermostats["103"].local_off == 0

    async def test_map_based_local_off_unknown_command(self, client, mock_server):
        """TLO with unknown map command ID is silently ignored."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await mock_server.send_update("upd", ["TLO", "999", "0"])
        await asyncio.sleep(0.2)

        off_events = [e for e in events if e[0] == "thermostat_local_off"]
        assert len(off_events) == 0

    async def test_map_based_season_update(self, client, mock_server):
        """Test TS (thermostat season from map) update."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # Map command "8" -> device "103"
        await mock_server.send_update("upd", ["TS", "8", "0"])
        await asyncio.sleep(0.2)
        assert client.thermostats["103"].season == 0

    async def test_map_based_temperature_update(self, client, mock_server):
        """Test TT (thermostat temperature from map) update."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await mock_server.send_update("upd", ["TT", "8", "220"])
        await asyncio.sleep(0.2)
        assert client.thermostats["103"].temperature == 22.0

    async def test_map_based_offset_update(self, client, mock_server):
        """Test TO (thermostat offset from map) update."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await mock_server.send_update("upd", ["TO", "8", "15"])
        await asyncio.sleep(0.2)
        assert client.thermostats["103"].offset == 1.5

    async def test_map_based_fanlevel_update(self, client, mock_server):
        """Test TL (thermostat fan level from map) update."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await mock_server.send_update("upd", ["TL", "8", "3"])
        await asyncio.sleep(0.2)
        assert client.thermostats["103"].fan_level == 3

    async def test_rgb_update(self, client, mock_server):
        """Test receiving an RGB update via UPD RGB."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await mock_server.send_update("upd", ["RGB", "100", "255", "128", "0"])
        await asyncio.sleep(0.2)

        rgb_events = [e for e in events if e[0] == "rgb"]
        assert len(rgb_events) > 0

    async def test_multiple_callbacks(self, client, mock_server):
        """Test multiple registered callbacks all receive events."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        await asyncio.sleep(0.5)

        events1 = []
        events2 = []
        client.register_update_callback(lambda t, d: events1.append((t, d)))
        client.register_update_callback(lambda t, d: events2.append((t, d)))

        await mock_server.send_update("upd", ["WS", "1", "100", "1"])
        await asyncio.sleep(0.2)

        assert (
            len(
                [
                    e
                    for e in events1
                    if e[0] == "device_status" and e[1]["device_id"] == "100"
                ]
            )
            > 0
        )
        assert (
            len(
                [
                    e
                    for e in events2
                    if e[0] == "device_status" and e[1]["device_id"] == "100"
                ]
            )
            > 0
        )

    async def test_unregister_connection_callback(self, mock_server):
        """Test unregistering a connection callback."""
        statuses = []
        client = AVEDominaClient(
            host="127.0.0.1", port=mock_server.port, command_delay=0
        )
        unregister = client.register_connection_callback(lambda s: statuses.append(s))

        await client.connect()
        assert "OPEN" in statuses

        unregister()
        statuses.clear()

        await client.disconnect()
        # After unregister, should NOT receive CLOSE
        assert "CLOSE" not in statuses


class TestClientErrorHandling:
    """Tests for client error handling."""

    async def test_send_when_not_connected(self):
        """Test that sending when not connected raises ConnectionError."""
        client = AVEDominaClient(host="127.0.0.1", port=14001)
        with pytest.raises(ConnectionError, match="Not connected"):
            await client.send_command("LM")

    async def test_turn_on_when_not_connected(self):
        """Test that turn_on_light when not connected raises."""
        client = AVEDominaClient(host="127.0.0.1", port=14001)
        with pytest.raises(ConnectionError):
            await client.turn_on_light("100")

    async def test_set_thermostat_nonexistent(self, mock_server):
        """Test setting thermostat set point for non-existent thermostat is a no-op."""
        client = AVEDominaClient(
            host="127.0.0.1", port=mock_server.port, command_delay=0
        )
        await client.connect()
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # Device "999" doesn't exist
        await client.set_thermostat_set_point("999", 22.0)
        # No error, just silently returns
        await client.disconnect()

    async def test_set_thermostat_season_nonexistent(self, mock_server):
        """Test setting thermostat season for non-existent thermostat is a no-op."""
        client = AVEDominaClient(
            host="127.0.0.1", port=mock_server.port, command_delay=0
        )
        await client.connect()
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.set_thermostat_season("999", 0)
        await client.disconnect()

    async def test_dimmer_level_clamped_negative(self, mock_server):
        """Test that negative dimmer level is clamped to 0."""
        client = AVEDominaClient(
            host="127.0.0.1", port=mock_server.port, command_delay=0
        )
        await client.connect()
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.set_dimmer_level("101", -10)
        await asyncio.sleep(0.2)
        assert mock_server.device_statuses["101"] == 0
        await client.disconnect()

    async def test_wait_for_initialization_timeout(self, mock_server):
        """Test that wait_for_initialization returns False on timeout."""
        client = AVEDominaClient(
            host="127.0.0.1", port=mock_server.port, command_delay=0
        )
        await client.connect()
        # Don't call initialize - just wait for timeout
        result = await client.wait_for_initialization(timeout=0.1)
        assert result is False
        await client.disconnect()


class TestClientSessionManagement:
    """Tests for external session injection."""

    async def test_external_session(self, mock_server):
        """Test using an externally provided aiohttp session."""
        import aiohttp

        session = aiohttp.ClientSession()
        try:
            client = AVEDominaClient(
                host="127.0.0.1",
                port=mock_server.port,
                session=session,
                command_delay=0,
            )
            assert not client._owns_session
            await client.connect()
            assert client.connected
            await client.disconnect()
            # External session should NOT be closed by client
            assert not session.closed
        finally:
            await session.close()

    async def test_owned_session_closed_on_disconnect(self, mock_server):
        """Test that an internally created session is closed on disconnect."""
        client = AVEDominaClient(
            host="127.0.0.1", port=mock_server.port, command_delay=0
        )
        assert client._owns_session
        await client.connect()
        assert client.connected
        session = client._session
        await client.disconnect()
        assert session.closed

    async def test_disconnect_when_not_connected(self):
        """Test that disconnecting when not connected doesn't error."""
        client = AVEDominaClient(host="127.0.0.1", port=14001)
        await client.disconnect()  # Should not raise

    async def test_connected_property_false_by_default(self):
        """Test that connected is False before connecting."""
        client = AVEDominaClient(host="127.0.0.1")
        assert not client.connected

    async def test_default_port(self):
        """Test that default port is 14001."""
        client = AVEDominaClient(host="192.168.1.1")
        assert client.port == 14001
        assert client.url == "ws://192.168.1.1:14001"


class TestClientDeviceAddresses:
    """Tests for device address loading."""

    async def test_li2_loads_addresses(self, client, mock_server):
        """Test that LI2 response populates avebus_address on devices."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        await asyncio.sleep(0.3)

        # Device 100 should have address 10 from mock data
        assert client.devices["100"].avebus_address == 10
        assert client.devices["101"].avebus_address == 11


class TestClientCallbackExceptions:
    """Tests for error handling in callbacks."""

    async def test_update_callback_exception_does_not_crash(self, client, mock_server):
        """Test that an exception in an update callback doesn't crash the client."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        await asyncio.sleep(0.5)

        def bad_callback(event_type, data):
            raise ValueError("Callback error")

        good_events = []
        client.register_update_callback(bad_callback)
        client.register_update_callback(lambda t, d: good_events.append((t, d)))

        await mock_server.send_update("upd", ["WS", "1", "100", "1"])
        await asyncio.sleep(0.2)

        # The good callback should still have been called despite the bad one raising
        status_events = [e for e in good_events if e[0] == "device_status"]
        assert len(status_events) > 0

    async def test_connection_callback_exception_does_not_crash(self, mock_server):
        """Test that an exception in a connection callback doesn't crash."""

        def bad_callback(status):
            raise RuntimeError("Connection callback error")

        statuses = []
        c = AVEDominaClient(host="127.0.0.1", port=mock_server.port, command_delay=0)
        c.register_connection_callback(bad_callback)
        c.register_connection_callback(lambda s: statuses.append(s))

        await c.connect()
        # Good callback should still be called despite bad one raising
        assert "OPEN" in statuses
        await c.disconnect()


class TestClientSpecialDevices:
    """Tests for special device handling (RGBW, DALI, VMC Daikin)."""

    async def test_rgbw_device_name_stripped(self, client, mock_server):
        """Test that RGBW prefix ($) is stripped from device names."""
        # Directly call the handler with RGBW device data
        await client._handle_ldi([], [["200", "$RGB Light", "1", "1"]])
        assert "200" in client.devices
        assert client.devices["200"].name == "RGB Light"

    async def test_dali_device_name_stripped(self, client, mock_server):
        """Test that DALI suffix ($) is stripped from device names."""
        await client._handle_ldi([], [["201", "DALI Fixture$", "2", "1"]])
        assert "201" in client.devices
        assert client.devices["201"].name == "DALI Fixture"

    async def test_vmc_daikin_thermostat_id_adjusted(self, client, mock_server):
        """Test that VMC Daikin thermostat IDs are adjusted by -10000000."""
        await client._handle_ldi([], [["10000500", "VMC Daikin", "4", "1"]])
        assert "500" in client.devices
        assert "500" in client.thermostats
        assert client.thermostats["500"].is_vmc_daikin

    async def test_short_ldi_record_skipped(self, client, mock_server):
        """Test that LDI records with fewer than 3 fields are skipped."""
        await client._handle_ldi(
            [],
            [
                ["300", "Short"],  # Too short - should be skipped
                ["301", "OK Device", "1"],  # Valid
            ],
        )
        assert "300" not in client.devices
        assert "301" in client.devices


class TestClientDuplicateInit:
    """Tests for duplicate LM/LDI handling."""

    async def test_duplicate_lm_ignored(self, client, mock_server):
        """Test that second LM response is ignored."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        original_areas = dict(client.areas)
        assert len(original_areas) > 0

        # Sending LM again should be a no-op
        await client._handle_lm([], [["99", "New Area", "9"]])
        assert client.areas == original_areas

    async def test_duplicate_ldi_ignored(self, client, mock_server):
        """Test that second LDI response is ignored."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        original_devices = dict(client.devices)
        assert len(original_devices) > 0

        # Sending LDI again should be a no-op
        await client._handle_ldi([], [["999", "New Device", "1", "1"]])
        assert client.devices == original_devices


class TestClientLI2EdgeCases:
    """Tests for LI2 (device address) edge cases."""

    async def test_li2_short_record_skipped(self, client, mock_server):
        """Test that LI2 records with fewer than 4 fields are skipped."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # Short record should not crash
        await client._handle_li2([], [["100", "Light", "1"]])  # Missing address field

    async def test_li2_invalid_address_handled(self, client, mock_server):
        """Test that invalid avebus_address (non-integer) is handled gracefully."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # Invalid address value should not crash
        await client._handle_li2([], [["100", "Light", "1", "notanumber"]])
        # avebus_address should be whatever it was before (0 or from the mock init)

    async def test_li2_unknown_device_skipped(self, client, mock_server):
        """Test that LI2 records for unknown devices are skipped."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # Device 999 doesn't exist - should not crash
        await client._handle_li2([], [["999", "Unknown", "1", "50"]])


class TestClientLMCEdgeCases:
    """Tests for LMC (map commands) edge cases."""

    async def test_lmc_short_record_skipped(self, client, mock_server):
        """Test that LMC records with fewer than 16 fields are skipped."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # Create a fresh area with no commands
        from pyavedominaplus.models import DominaArea

        client._areas["99"] = DominaArea(id="99", name="Test", order="0")

        # Short record (only 5 fields) should be skipped
        await client._handle_lmc(["99"], [["1", "Short", "1", "50", "60"]])
        assert len(client._areas["99"].map_commands) == 0


class TestClientWTSEdgeCases:
    """Tests for WTS (thermostat status) edge cases."""

    async def test_wts_empty_parameters(self, client, mock_server):
        """Test WTS with empty parameters is handled gracefully."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # Should not crash
        await client._handle_wts([], [])

    async def test_wts_unknown_thermostat(self, client, mock_server):
        """Test WTS for unknown thermostat is handled gracefully."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # Thermostat 999 doesn't exist - should not crash
        await client._handle_wts(
            ["999"], [["1", "2", "6", "5", "1", "215", "1", "210", "0", "0"]]
        )


class TestClientThermoUPDEdgeCases:
    """Tests for thermostat update edge cases."""

    async def test_thermo_upd_short_parameters(self, client, mock_server):
        """Test WT update with too few parameters is ignored."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        # Only 3 params instead of 4
        await client._handle_upd(["WT", "T", "103"], [])
        await asyncio.sleep(0.1)

        # Should not crash, no events generated
        temp_events = [e for e in events if e[0] == "thermostat_temperature"]
        assert len(temp_events) == 0


class TestClientGSFHandler:
    """Tests for GSF (sensor family) handler."""

    async def test_gsf_handler_no_op(self, client, mock_server):
        """Test that GSF handler is a no-op."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # Should not crash
        await client._handle_gsf([], [])


class TestClientUPDEdgeCases:
    """Tests for UPD event edge cases."""

    async def test_upd_empty_parameters(self, client, mock_server):
        """Test UPD with empty parameters is silently ignored."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        await asyncio.sleep(0.5)  # Flush pending WSF responses

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        # Send UPD with no type
        await mock_server.send_update("upd")
        await asyncio.sleep(0.2)
        # Should not crash, no events generated for empty upd
        status_events = [e for e in events if e[0] == "device_status"]
        assert len(status_events) == 0

    async def test_upd_device_status_unknown_device(self, client, mock_server):
        """Test UPD WS for a device not in the device list."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        await asyncio.sleep(0.5)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        # Device 999 doesn't exist
        await mock_server.send_update("upd", ["WS", "1", "999", "1"])
        await asyncio.sleep(0.2)

        # Event is still emitted even for unknown device
        status_events = [
            e for e in events if e[0] == "device_status" and e[1]["device_id"] == "999"
        ]
        assert len(status_events) > 0

    async def test_upd_thermostat_unknown_device(self, client, mock_server):
        """Test UPD WT for an unknown thermostat."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        # Thermostat 999 doesn't exist
        await mock_server.send_update("upd", ["WT", "T", "999", "225"])
        await asyncio.sleep(0.2)

        # Event is still emitted
        temp_events = [e for e in events if e[0] == "thermostat_temperature"]
        assert len(temp_events) > 0

    async def test_upd_ws_short_parameters(self, client, mock_server):
        """Test UPD WS with too few parameters is ignored."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        await asyncio.sleep(0.5)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        # Only 2 params instead of 4
        await mock_server.send_update("upd", ["WS", "1"])
        await asyncio.sleep(0.2)

        status_events = [e for e in events if e[0] == "device_status"]
        assert len(status_events) == 0


class TestClientNewControlMethods:
    """Tests for toggle_light, step_dimmer, activate_scenario fallback,
    toggle_thermostat_local_off, and toggle_thermostat_keyboard_lock."""

    async def test_toggle_light(self, client, mock_server):
        """Test toggling a light on (EBI/10 sub-command)."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        mock_server.device_statuses["100"] = 0

        await client.toggle_light("100")
        await asyncio.sleep(0.2)

        assert mock_server.device_statuses["100"] == 1

    async def test_turn_on_dimmer(self, client, mock_server):
        """Test turn_on_dimmer sends EBI with sub-command 3."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        mock_server.device_statuses["101"] = 0

        await client.turn_on_dimmer("101")
        await asyncio.sleep(0.2)

        assert mock_server.device_statuses["101"] == 1
        ebi_cmds = [
            c
            for c in mock_server.received_commands
            if c["command"] == "EBI" and c["parameters"][0] == "101"
        ]
        assert ebi_cmds[-1]["parameters"] == ["101", "3"]

    async def test_turn_off_dimmer(self, client, mock_server):
        """Test turn_off_dimmer sends EBI with sub-command 4."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        mock_server.device_statuses["101"] = 15

        await client.turn_off_dimmer("101")
        await asyncio.sleep(0.2)

        assert mock_server.device_statuses["101"] == 0
        ebi_cmds = [
            c
            for c in mock_server.received_commands
            if c["command"] == "EBI" and c["parameters"][0] == "101"
        ]
        assert ebi_cmds[-1]["parameters"] == ["101", "4"]

    async def test_step_dimmer(self, client, mock_server):
        """Test step_dimmer sends EBI with sub-command 2."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        mock_server.device_statuses["101"] = 0

        await client.step_dimmer("101")
        await asyncio.sleep(0.2)

        assert mock_server.device_statuses["101"] == 1

    async def test_activate_scenario_fallback(self, client, mock_server):
        """activate_scenario falls back to device_id when no scenario map command found."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        mock_server.received_commands.clear()

        # "100" is a light, not a scenario — hits the fallback path
        await client.activate_scenario("100")
        await asyncio.sleep(0.2)

        es_cmds = [c for c in mock_server.received_commands if c["command"] == "ES"]
        assert len(es_cmds) > 0
        assert es_cmds[-1]["parameters"] == ["100"]

    async def test_toggle_thermostat_local_off_nonexistent(self, client, mock_server):
        """toggle_thermostat_local_off is a no-op for unknown device."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        # Should not raise
        await client.toggle_thermostat_local_off("999")

    async def test_toggle_thermostat_local_off_standard(self, client, mock_server):
        """toggle_thermostat_local_off sends TOO for a standard thermostat."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        mock_server.received_commands.clear()

        await client.toggle_thermostat_local_off("103")
        await asyncio.sleep(0.2)

        too_cmds = [c for c in mock_server.received_commands if c["command"] == "TOO"]
        assert len(too_cmds) > 0
        assert too_cmds[-1]["parameters"][0] == "103"

    async def test_toggle_thermostat_local_off_vmc_daikin(self, mock_server):
        """toggle_thermostat_local_off sends TUU for a VMC Daikin thermostat."""
        c = AVEDominaClient(host="127.0.0.1", port=mock_server.port, command_delay=0)
        await c.connect()
        try:
            # Inject a VMC Daikin thermostat (id offset by 10,000,000)
            await c._handle_ldi([], [["10000500", "VMC Daikin", "4", "1"]])
            assert "500" in c.thermostats
            assert c.thermostats["500"].is_vmc_daikin

            mock_server.received_commands.clear()
            await c.toggle_thermostat_local_off("500")
            await asyncio.sleep(0.2)

            tuu_cmds = [
                cmd for cmd in mock_server.received_commands if cmd["command"] == "TUU"
            ]
            assert len(tuu_cmds) > 0
            assert tuu_cmds[-1]["parameters"][0] == "500"
        finally:
            await c.disconnect()

    async def test_toggle_thermostat_keyboard_lock(self, client, mock_server):
        """toggle_thermostat_keyboard_lock sends TTK command."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        mock_server.received_commands.clear()

        await client.toggle_thermostat_keyboard_lock("103")
        await asyncio.sleep(0.2)

        ttk_cmds = [c for c in mock_server.received_commands if c["command"] == "TTK"]
        assert len(ttk_cmds) > 0
        assert ttk_cmds[-1]["parameters"] == ["103"]

    async def test_turn_off_thermostat(self, client, mock_server):
        """turn_off_thermostat sends TOO with 0 (server inverts to 1=OFF)."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        assert client.thermostats["103"].local_off == 0
        mock_server.received_commands.clear()

        await client.turn_off_thermostat("103")
        await asyncio.sleep(0.3)

        too_cmds = [c for c in mock_server.received_commands if c["command"] == "TOO"]
        assert len(too_cmds) > 0
        assert too_cmds[-1]["parameters"] == ["103", "0"]
        assert client.thermostats["103"].local_off == 1

    async def test_turn_off_thermostat_already_off(self, client, mock_server):
        """turn_off_thermostat is idempotent — sends TOO even when already off."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        client.thermostats["103"].local_off = 1
        mock_server.received_commands.clear()

        await client.turn_off_thermostat("103")
        await asyncio.sleep(0.3)

        too_cmds = [c for c in mock_server.received_commands if c["command"] == "TOO"]
        assert len(too_cmds) > 0
        # Always sends "0"; server inverts to 1 (OFF)
        assert too_cmds[-1]["parameters"] == ["103", "0"]

    async def test_turn_on_thermostat(self, client, mock_server):
        """turn_on_thermostat sends TOO with 1 (server inverts to 0=ON)."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        client.thermostats["103"].local_off = 1
        mock_server.received_commands.clear()

        await client.turn_on_thermostat("103")
        await asyncio.sleep(0.3)

        too_cmds = [c for c in mock_server.received_commands if c["command"] == "TOO"]
        assert len(too_cmds) > 0
        assert too_cmds[-1]["parameters"] == ["103", "1"]
        assert client.thermostats["103"].local_off == 0

    async def test_turn_on_thermostat_already_on(self, client, mock_server):
        """turn_on_thermostat is idempotent — sends TOO even when already on."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        assert client.thermostats["103"].local_off == 0
        mock_server.received_commands.clear()

        await client.turn_on_thermostat("103")
        await asyncio.sleep(0.3)

        too_cmds = [c for c in mock_server.received_commands if c["command"] == "TOO"]
        assert len(too_cmds) > 0
        # Always sends "1"; server inverts to 0 (ON)
        assert too_cmds[-1]["parameters"] == ["103", "1"]

    async def test_turn_on_thermostat_nonexistent(self, client, mock_server):
        """turn_on_thermostat sends command even for unknown device."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        await client.turn_on_thermostat("999")

    async def test_turn_off_thermostat_nonexistent(self, client, mock_server):
        """turn_off_thermostat sends command even for unknown device."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        await client.turn_off_thermostat("999")

    async def test_set_thermostat_mode_auto(self, client, mock_server):
        """set_thermostat_mode sends STS with mode=0 (auto)."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        mock_server.received_commands.clear()

        await client.set_thermostat_mode("103", 0)
        await asyncio.sleep(0.3)

        sts_cmds = [c for c in mock_server.received_commands if c["command"] == "STS"]
        assert len(sts_cmds) > 0
        # Record should contain [season, mode, set_point]
        assert sts_cmds[-1]["records"][0][1] == "0"
        # Client should receive TM update with mode='A' -> 0
        assert client.thermostats["103"].mode == 0

    async def test_set_thermostat_mode_manual(self, client, mock_server):
        """set_thermostat_mode sends STS with mode=1 (manual)."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        # Set to auto first
        client.thermostats["103"].mode = 0
        mock_server.received_commands.clear()

        await client.set_thermostat_mode("103", 1)
        await asyncio.sleep(0.3)

        sts_cmds = [c for c in mock_server.received_commands if c["command"] == "STS"]
        assert len(sts_cmds) > 0
        assert sts_cmds[-1]["records"][0][1] == "1"
        # Client should receive TM update with mode='M' -> 1
        assert client.thermostats["103"].mode == 1

    async def test_set_thermostat_mode_nonexistent(self, client, mock_server):
        """set_thermostat_mode is a no-op for unknown device."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        await client.set_thermostat_mode("999", 0)

    async def test_thermostat_mode_update_letter_m(self, client, mock_server):
        """TM update with letter 'M' sets mode to manual (1)."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await mock_server.send_update("upd", ["TM", "103", "M"])
        await asyncio.sleep(0.2)

        mode_events = [e for e in events if e[0] == "thermostat_mode"]
        assert len(mode_events) > 0
        assert client.thermostats["103"].mode == 1

    async def test_thermostat_mode_update_letter_a(self, client, mock_server):
        """TM update with letter 'A' sets mode to auto (0)."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        client.thermostats["103"].mode = 1  # Start in manual

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await mock_server.send_update("upd", ["TM", "103", "A"])
        await asyncio.sleep(0.2)

        mode_events = [e for e in events if e[0] == "thermostat_mode"]
        assert len(mode_events) > 0
        assert client.thermostats["103"].mode == 0


class TestClientListenLoopEdgeCases:
    """Tests for _listen_loop and _handle_message edge cases."""

    async def test_handle_unknown_command(self, client, mock_server):
        """_handle_message with an unrecognised command logs and does nothing."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        # Should not raise
        await client._handle_message(
            {"command": "unknown_xyz", "parameters": [], "records": []}
        )

    async def test_handle_lml_no_op(self, client, mock_server):
        """_handle_lml is a no-op."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        await client._handle_lml([], [])

    async def test_handle_net_no_op(self, client, mock_server):
        """_handle_net is a no-op."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        await client._handle_net([], [])

    async def test_server_stop_triggers_error_callback(self, mock_server):
        """When the server closes the connection, the client emits an ERROR status."""
        statuses = []
        c = AVEDominaClient(
            host="127.0.0.1",
            port=mock_server.port,
            auto_reconnect=False,
            command_delay=0,
        )
        c.register_connection_callback(lambda s: statuses.append(s))
        await c.connect()
        assert "OPEN" in statuses
        try:
            # Close the server side — client receives CLOSED/CLOSING frame
            await mock_server.stop()
            await asyncio.sleep(0.3)
            assert "ERROR" in statuses
        finally:
            await c.disconnect()

    async def test_listen_loop_text_message(self, client, mock_server):
        """Client processes text-framed WebSocket messages correctly."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)
        await asyncio.sleep(0.5)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        # Send the encoded message as a text frame (not binary)
        await mock_server.send_text_update("upd", ["WS", "1", "100", "1"])
        await asyncio.sleep(0.2)

        status_events = [
            e for e in events if e[0] == "device_status" and e[1]["device_id"] == "100"
        ]
        assert len(status_events) > 0

    async def test_listen_loop_decode_exception(self, client, mock_server):
        """Exception during message decode is caught and the client stays connected."""
        from unittest.mock import patch

        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        with patch(
            "pyavedominaplus.protocol.ProtocolDecoder.feed",
            side_effect=ValueError("bad data"),
        ):
            await mock_server.send_update("upd", ["WS", "1", "100", "1"])
            await asyncio.sleep(0.2)

        # Client should still be connected after the caught exception
        assert client.connected

    async def test_listen_loop_closed_message(self, client, mock_server):
        """Client handles CLOSED/CLOSING/ERROR WebSocket message types gracefully."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # Stopping the server will cause the WebSocket to close,
        # triggering the CLOSED/CLOSING branch in the listen loop
        statuses = []
        client.register_connection_callback(lambda s: statuses.append(s))
        await mock_server.stop()
        await asyncio.sleep(0.5)

        assert "ERROR" in statuses

    async def test_listen_loop_not_running_break(self, client, mock_server):
        """Listen loop breaks when _running is set to False."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # Disconnect sets _running = False, which should cause the loop to break
        await client.disconnect()
        await asyncio.sleep(0.2)
        assert not client.connected


class TestThermostatFunctionAndRequest:
    """Tests for TF and TR update handlers."""

    async def test_thermostat_function_update(self, client, mock_server):
        """TF update emits thermostat_function event with parameters."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await mock_server.send_update("upd", ["TF", "103", "5", "10"])
        await asyncio.sleep(0.2)

        tf_events = [e for e in events if e[0] == "thermostat_function"]
        assert len(tf_events) == 1
        assert tf_events[0][1]["device_id"] == "103"
        assert tf_events[0][1]["parameters"] == ["5", "10"]

    async def test_thermostat_function_short_params(self, client, mock_server):
        """TF update with too few parameters is ignored."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await mock_server.send_update("upd", ["TF", "103"])
        await asyncio.sleep(0.2)

        tf_events = [e for e in events if e[0] == "thermostat_function"]
        assert len(tf_events) == 0

    async def test_thermostat_request_update(self, client, mock_server):
        """TR update emits thermostat_request event with value."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await mock_server.send_update("upd", ["TR", "103", "42"])
        await asyncio.sleep(0.2)

        tr_events = [e for e in events if e[0] == "thermostat_request"]
        assert len(tr_events) == 1
        assert tr_events[0][1]["device_id"] == "103"
        assert tr_events[0][1]["value"] == "42"

    async def test_thermostat_request_short_params(self, client, mock_server):
        """TR update with too few parameters is ignored."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await mock_server.send_update("upd", ["TR", "103"])
        await asyncio.sleep(0.2)

        tr_events = [e for e in events if e[0] == "thermostat_request"]
        assert len(tr_events) == 0


class TestResolveDeviceId:
    """Tests for _resolve_device_id with direct device IDs vs map command IDs."""

    async def test_resolve_by_thermostat_id(self, client, mock_server):
        """_resolve_device_id returns the ID when it matches a thermostat."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # "103" is a thermostat, should resolve directly
        result = client._resolve_device_id("103")
        assert result == "103"

    async def test_resolve_by_device_id(self, client, mock_server):
        """_resolve_device_id returns the ID when it matches a non-thermostat device."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # "100" is a light (not a thermostat), should resolve via _devices
        result = client._resolve_device_id("100")
        assert result == "100"

    async def test_resolve_by_map_command_id(self, client, mock_server):
        """_resolve_device_id resolves via map command lookup."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # "8" is the map command ID for thermostat 103 (see mock_server.py)
        result = client._resolve_device_id("8")
        assert result == "103"

    async def test_resolve_unknown_returns_none(self, client, mock_server):
        """_resolve_device_id returns None for unknown IDs."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        result = client._resolve_device_id("99999")
        assert result is None

    async def test_tlo_update_with_direct_device_id(self, client, mock_server):
        """TLO update using a direct device ID (not map command) is handled."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        # Send TLO with direct device ID "103" instead of map command ID
        await mock_server.send_update("upd", ["TLO", "103", "1"])
        await asyncio.sleep(0.2)

        off_events = [e for e in events if e[0] == "thermostat_local_off"]
        assert len(off_events) == 1
        assert off_events[0][1]["device_id"] == "103"


class TestListenLoopWSMsgTypes:
    """Tests for _listen_loop handling of different WebSocket message types."""

    async def test_listen_loop_ws_closed_msg(self, mock_server):
        """Listen loop breaks on WSMsgType.CLOSED and emits ERROR status."""
        from unittest.mock import AsyncMock, MagicMock

        import aiohttp

        client = AVEDominaClient(
            host="127.0.0.1",
            port=mock_server.port,
            auto_reconnect=False,
            command_delay=0,
        )
        await client.connect()
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        statuses = []
        client.register_connection_callback(lambda s: statuses.append(s))

        # Replace _ws with a mock that yields a CLOSED message
        closed_msg = MagicMock()
        closed_msg.type = aiohttp.WSMsgType.CLOSED

        async def mock_ws_iter():
            yield closed_msg

        # Cancel the existing listen task and run our own
        if client._listen_task:
            client._listen_task.cancel()
            try:
                await client._listen_task
            except asyncio.CancelledError:
                pass

        original_ws = client._ws
        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: mock_ws_iter()
        client._ws = mock_ws
        client._running = True

        await client._listen_loop()

        assert "ERROR" in statuses
        client._ws = original_ws
        await client.disconnect()

    async def test_listen_loop_ws_error_msg(self, mock_server):
        """Listen loop breaks on WSMsgType.ERROR."""
        from unittest.mock import AsyncMock, MagicMock

        import aiohttp

        client = AVEDominaClient(
            host="127.0.0.1",
            port=mock_server.port,
            auto_reconnect=False,
            command_delay=0,
        )
        await client.connect()

        statuses = []
        client.register_connection_callback(lambda s: statuses.append(s))

        error_msg = MagicMock()
        error_msg.type = aiohttp.WSMsgType.ERROR

        async def mock_ws_iter():
            yield error_msg

        if client._listen_task:
            client._listen_task.cancel()
            try:
                await client._listen_task
            except asyncio.CancelledError:
                pass

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: mock_ws_iter()
        original_ws = client._ws
        client._ws = mock_ws
        client._running = True

        await client._listen_loop()

        assert "ERROR" in statuses
        client._ws = original_ws
        await client.disconnect()

    async def test_listen_loop_unknown_msg_type(self, mock_server):
        """Listen loop ignores unknown message types and continues."""
        from unittest.mock import AsyncMock, MagicMock

        import aiohttp

        from pyavedominaplus.protocol import encode_message

        client = AVEDominaClient(
            host="127.0.0.1",
            port=mock_server.port,
            auto_reconnect=False,
            command_delay=0,
        )
        await client.connect()
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        # First yield an unknown type (PING=0xa), then a valid binary, then stop
        unknown_msg = MagicMock()
        unknown_msg.type = aiohttp.WSMsgType.PING

        valid_msg = MagicMock()
        valid_msg.type = aiohttp.WSMsgType.BINARY
        valid_msg.data = encode_message("upd", ["WS", "1", "100", "1"])

        closed_msg = MagicMock()
        closed_msg.type = aiohttp.WSMsgType.CLOSED

        async def mock_ws_iter():
            yield unknown_msg
            yield valid_msg
            yield closed_msg

        if client._listen_task:
            client._listen_task.cancel()
            try:
                await client._listen_task
            except asyncio.CancelledError:
                pass

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: mock_ws_iter()
        original_ws = client._ws
        client._ws = mock_ws
        client._running = True

        await client._listen_loop()

        # The valid binary message should have been processed after the unknown one
        status_events = [e for e in events if e[0] == "device_status"]
        assert len(status_events) > 0
        client._ws = original_ws
        await client.disconnect()

    async def test_listen_loop_not_running(self, mock_server):
        """Listen loop breaks immediately when _running is False."""
        from unittest.mock import AsyncMock, MagicMock

        import aiohttp

        from pyavedominaplus.protocol import encode_message

        client = AVEDominaClient(
            host="127.0.0.1", port=mock_server.port, command_delay=0
        )
        await client.connect()

        valid_msg = MagicMock()
        valid_msg.type = aiohttp.WSMsgType.BINARY
        valid_msg.data = encode_message("upd", ["WS", "1", "100", "1"])

        async def mock_ws_iter():
            yield valid_msg

        if client._listen_task:
            client._listen_task.cancel()
            try:
                await client._listen_task
            except asyncio.CancelledError:
                pass

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: mock_ws_iter()
        original_ws = client._ws
        client._ws = mock_ws
        client._running = False  # Not running — should break immediately

        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))

        await client._listen_loop()

        # Message should NOT have been processed
        assert len(events) == 0
        client._ws = original_ws
        await client.disconnect()

    async def test_listen_loop_exception(self, mock_server):
        """Listen loop catches generic exceptions and emits ERROR status."""

        client = AVEDominaClient(
            host="127.0.0.1",
            port=mock_server.port,
            auto_reconnect=False,
            command_delay=0,
        )
        await client.connect()

        statuses = []
        client.register_connection_callback(lambda s: statuses.append(s))

        class RaisingAsyncIter:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise RuntimeError("connection lost")

        if client._listen_task:
            client._listen_task.cancel()
            try:
                await client._listen_task
            except asyncio.CancelledError:
                pass

        original_ws = client._ws
        client._ws = RaisingAsyncIter()
        client._running = True

        await client._listen_loop()

        assert "ERROR" in statuses
        client._ws = original_ws
        await client.disconnect()


class TestAutoReconnect:
    """Tests for automatic reconnection with backoff."""

    async def test_reconnect_after_server_drop(self, mock_server):
        """Client reconnects automatically after server stops and restarts."""
        port = mock_server.port
        client = AVEDominaClient(
            host="127.0.0.1",
            port=port,
            reconnect_interval=0.3,
            max_reconnect_interval=1.0,
            command_delay=0,
        )
        await client.connect()
        await client.initialize()
        ok = await client.wait_for_initialization(timeout=5.0)
        assert ok

        statuses = []
        client.register_connection_callback(lambda s: statuses.append(s))

        # Stop server — triggers ERROR + reconnect loop
        await mock_server.stop()
        await asyncio.sleep(0.5)
        assert "ERROR" in statuses

        # Restart server on the same port
        server2 = MockDominaServer(port=port)
        await server2.start()
        try:
            # Wait for reconnect
            await asyncio.sleep(2.0)
            assert "OPEN" in statuses
            assert client.connected

            # Verify re-initialization happened
            ok = await client.wait_for_initialization(timeout=5.0)
            assert ok
        finally:
            await client.disconnect()
            await server2.stop()

    async def test_reconnect_disabled(self, mock_server):
        """No reconnect attempt when auto_reconnect=False."""
        client = AVEDominaClient(
            host="127.0.0.1",
            port=mock_server.port,
            auto_reconnect=False,
            command_delay=0,
        )
        await client.connect()
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        statuses = []
        client.register_connection_callback(lambda s: statuses.append(s))

        await mock_server.stop()
        await asyncio.sleep(0.5)

        assert "ERROR" in statuses
        assert client._reconnect_task is None
        assert statuses.count("OPEN") == 0
        await client.disconnect()

    async def test_disconnect_stops_reconnect(self, mock_server):
        """Calling disconnect() cancels any pending reconnect."""
        client = AVEDominaClient(
            host="127.0.0.1",
            port=mock_server.port,
            reconnect_interval=5.0,
            command_delay=0,
        )
        await client.connect()
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await mock_server.stop()
        await asyncio.sleep(0.5)

        # Reconnect task should be running (sleeping for 5s)
        assert client._reconnect_task is not None
        assert not client._reconnect_task.done()

        # Disconnect should cancel it
        await client.disconnect()
        assert not client._running
        assert client._reconnect_task is None

    async def test_backoff_increases(self, mock_server):
        """Reconnect delay doubles on each failure up to max."""
        import unittest.mock

        port = mock_server.port
        client = AVEDominaClient(
            host="127.0.0.1",
            port=port,
            reconnect_interval=0.1,
            max_reconnect_interval=0.4,
            command_delay=0,
        )
        await client.connect()
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        reconnect_attempts = []
        original_reconnect = client._reconnect_loop

        async def tracking_reconnect():
            """Wrap reconnect loop to count attempts via logging."""
            await original_reconnect()

        # Track reconnect warnings via log capture
        with unittest.mock.patch("pyavedominaplus.client._LOGGER") as mock_logger:
            mock_logger.info = unittest.mock.MagicMock(
                side_effect=lambda msg, *args: (
                    reconnect_attempts.append(args[0])
                    if "Reconnecting in" in str(msg)
                    else None
                )
            )
            mock_logger.debug = unittest.mock.MagicMock()
            mock_logger.warning = unittest.mock.MagicMock()
            mock_logger.exception = unittest.mock.MagicMock()

            # Stop server — reconnect attempts will fail
            await mock_server.stop()
            # Wait long enough for several retries: 0.1 + 0.2 + 0.4 ~= 0.7s
            await asyncio.sleep(2.0)

        # Should have multiple reconnect attempts with increasing delays
        assert len(reconnect_attempts) >= 3
        # Verify backoff: delays should increase
        assert reconnect_attempts[0] < reconnect_attempts[1]
        # Max should be capped
        assert reconnect_attempts[-1] <= 0.4

        await client.disconnect()

    async def test_reconnect_resets_init_state(self, mock_server):
        """Reconnect resets _lm_loaded, _ldi_loaded, _initialized."""
        port = mock_server.port
        client = AVEDominaClient(
            host="127.0.0.1",
            port=port,
            reconnect_interval=0.3,
            command_delay=0,
        )
        await client.connect()
        await client.initialize()
        ok = await client.wait_for_initialization(timeout=5.0)
        assert ok
        assert client._lm_loaded
        assert client._ldi_loaded
        assert client._initialized.is_set()

        # Stop server — triggers reconnect which calls _reset_init_state
        await mock_server.stop()
        await asyncio.sleep(0.2)

        # Start server back up on same port
        server2 = MockDominaServer(port=port)
        await server2.start()
        try:
            await asyncio.sleep(1.5)
            # After successful reconnect, state should be re-initialized
            ok = await client.wait_for_initialization(timeout=5.0)
            assert ok
            assert client._lm_loaded
            assert client._ldi_loaded
        finally:
            await client.disconnect()
            await server2.stop()

    async def test_send_command_waits_for_reconnect(self, mock_server):
        """send_command waits for reconnection instead of raising immediately."""
        port = mock_server.port
        client = AVEDominaClient(
            host="127.0.0.1",
            port=port,
            reconnect_interval=0.3,
            command_delay=0,
        )
        await client.connect()
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # Stop server — connection drops
        await mock_server.stop()
        await asyncio.sleep(0.3)
        assert not client.connected

        # Restart server — reconnect will happen
        server2 = MockDominaServer(port=port)
        await server2.start()
        try:
            # send_command should wait for reconnect, not raise immediately
            await client.send_command("PING")
            assert client.connected
        finally:
            await client.disconnect()
            await server2.stop()

    async def test_connection_error_breaks_listen_loop(self, mock_server):
        """ConnectionError during message handling breaks the listen loop."""
        from unittest.mock import AsyncMock, MagicMock

        import aiohttp

        from pyavedominaplus.protocol import encode_message

        port = mock_server.port
        client = AVEDominaClient(
            host="127.0.0.1",
            port=port,
            auto_reconnect=False,
            command_delay=0,
        )
        await client.connect()
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        statuses = []
        client.register_connection_callback(lambda s: statuses.append(s))

        # Create a ping message that will trigger a ConnectionError during PONG
        valid_msg = MagicMock()
        valid_msg.type = aiohttp.WSMsgType.BINARY
        valid_msg.data = encode_message("ping")

        async def mock_ws_iter():
            yield valid_msg

        if client._listen_task:
            client._listen_task.cancel()
            try:
                await client._listen_task
            except asyncio.CancelledError:
                pass

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: mock_ws_iter()
        mock_ws.send_bytes = AsyncMock(
            side_effect=ConnectionResetError("connection lost")
        )
        mock_ws.closed = False
        original_ws = client._ws
        client._ws = mock_ws
        client._running = True

        await client._listen_loop()

        # Should have broken out and triggered ERROR
        assert "ERROR" in statuses
        client._ws = original_ws
        await client.disconnect()


class TestClientWSF:
    """Tests for WSF record-response handling."""

    async def test_wsf_records_populate_statuses(self, client, mock_server):
        """Default mock mode answers WSF with UPD WS messages; statuses load."""
        mock_server.device_statuses["100"] = 1
        mock_server.device_statuses["102"] = 3
        await client.initialize()
        assert await client.wait_for_initialization(timeout=5.0)
        assert client.devices["100"].current_value == 1
        assert client.devices["102"].current_value == 3

    async def test_wsf_records_fire_device_status_events(self, client, mock_server):
        """Each reported status fires a device_status update callback."""
        events = []
        client.register_update_callback(lambda t, d: events.append((t, d)))
        await client.initialize()
        assert await client.wait_for_initialization(timeout=5.0)
        status_events = [e for e in events if e[0] == "device_status"]
        assert {e[1]["device_id"] for e in status_events} >= {"100", "101", "102"}

    async def test_wsf_short_or_bad_records_skipped(self, client, mock_server):
        """Records with missing fields or non-numeric status are skipped."""
        await client.initialize()
        assert await client.wait_for_initialization(timeout=5.0)
        before = client.devices["100"].current_value
        await client._handle_wsf(["1"], [["100"], ["100", "abc"], []])
        assert client.devices["100"].current_value == before

    async def test_wsf_record_mode_still_initializes(self):
        """Servers answering WSF with a wsf record message still work."""
        server = MockDominaServer(wsf_records=True)
        await server.start()
        try:
            c = AVEDominaClient(host="127.0.0.1", port=server.port, command_delay=0)
            await c.connect()
            await c.initialize()
            assert await c.wait_for_initialization(timeout=5.0)
            await c.disconnect()
        finally:
            await server.stop()


class TestClientLMCDuplicates:
    """Duplicate/unmatched lmc responses must not re-trigger status requests."""

    async def test_duplicate_lmc_does_not_rerequest_statuses(self, client, mock_server):
        await client.initialize()
        assert await client.wait_for_initialization(timeout=5.0)
        wsf_count = sum(
            1 for cmd in mock_server.received_commands if cmd["command"] == "WSF"
        )
        # Replay a duplicate and an unmatched lmc response
        await client._handle_lmc(["1"], [])
        await client._handle_lmc(["does-not-exist"], [])
        await asyncio.sleep(0.2)
        wsf_count_after = sum(
            1 for cmd in mock_server.received_commands if cmd["command"] == "WSF"
        )
        assert wsf_count_after == wsf_count

    async def test_statuses_requested_only_once(self, client, mock_server):
        await client.initialize()
        assert await client.wait_for_initialization(timeout=5.0)
        # 8 families requested exactly once each
        wsf_families = [
            cmd["parameters"][0]
            for cmd in mock_server.received_commands
            if cmd["command"] == "WSF"
        ]
        assert len(wsf_families) == len(set(wsf_families)) == 8


class TestClientConnectTimeout:
    """Tests for connect() timeout and error mapping."""

    async def test_connect_refused_raises_connection_error(self):
        from pyavedominaplus.exceptions import AVEDominaConnectionError

        client = AVEDominaClient(host="127.0.0.1", port=1, connect_timeout=5.0)
        with pytest.raises(AVEDominaConnectionError):
            await client.connect()

    async def test_connect_timeout_raises_timeout_error(self):
        from pyavedominaplus.exceptions import AVEDominaTimeoutError

        # RFC 5737 TEST-NET address: guaranteed unroutable, connect hangs
        client = AVEDominaClient(host="192.0.2.1", port=14001, connect_timeout=0.1)
        with pytest.raises(AVEDominaTimeoutError):
            await client.connect()

    async def test_connect_errors_subclass_connectionerror(self):
        client = AVEDominaClient(host="127.0.0.1", port=1, connect_timeout=5.0)
        with pytest.raises(ConnectionError):
            await client.connect()

    async def test_connect_twice_is_noop(self, mock_server):
        client = AVEDominaClient(
            host="127.0.0.1", port=mock_server.port, command_delay=0
        )
        await client.connect()
        first_ws = client._ws
        await client.connect()
        assert client._ws is first_ws
        await client.disconnect()


class TestClientSTSNoEcho:
    """Thermostat sets must converge even when the server does not echo."""

    async def test_set_point_without_echo(self):
        server = MockDominaServer(sts_echo=False)
        await server.start()
        try:
            c = AVEDominaClient(host="127.0.0.1", port=server.port, command_delay=0)
            await c.connect()
            await c.initialize()
            assert await c.wait_for_initialization(timeout=5.0)
            await c.set_thermostat_set_point("103", 24.5)
            await asyncio.sleep(0.3)
            # Optimistic update + WTS re-read both give the new value
            assert c.thermostats["103"].set_point == 24.5
            await c.disconnect()
        finally:
            await server.stop()

    async def test_set_season_without_echo(self):
        server = MockDominaServer(sts_echo=False)
        await server.start()
        try:
            c = AVEDominaClient(host="127.0.0.1", port=server.port, command_delay=0)
            await c.connect()
            await c.initialize()
            assert await c.wait_for_initialization(timeout=5.0)
            await c.set_thermostat_season("103", 0)
            await asyncio.sleep(0.3)
            assert c.thermostats["103"].season == 0
            await c.disconnect()
        finally:
            await server.stop()

    async def test_set_mode_without_echo(self):
        server = MockDominaServer(sts_echo=False)
        await server.start()
        try:
            c = AVEDominaClient(host="127.0.0.1", port=server.port, command_delay=0)
            await c.connect()
            await c.initialize()
            assert await c.wait_for_initialization(timeout=5.0)
            await c.set_thermostat_mode("103", 0)
            await asyncio.sleep(0.3)
            assert c.thermostats["103"].mode == 0
            await c.disconnect()
        finally:
            await server.stop()


class TestClientContextManager:
    """Tests for async context manager support."""

    async def test_async_with_connects_and_disconnects(self, mock_server):
        async with AVEDominaClient(
            host="127.0.0.1", port=mock_server.port, command_delay=0
        ) as c:
            assert c.connected
        assert not c.connected


class TestInitializationWatchdog:
    """Initialization must not wedge on devices that never report a status."""

    @staticmethod
    def _silence_family(server: MockDominaServer, family: str) -> None:
        """Make the mock server ignore WSF for one device family."""
        original = server._respond_wsf

        async def patched(ws, parameters):
            if parameters and parameters[0] == family:
                return
            await original(ws, parameters)

        server._respond_wsf = patched

    async def test_init_completes_when_a_device_never_reports(self, mock_server):
        """A silent device no longer blocks initialization forever."""
        self._silence_family(mock_server, "16")  # device 107, a shutter
        c = AVEDominaClient(
            host="127.0.0.1",
            port=mock_server.port,
            command_delay=0,
            status_settle_timeout=0.5,
        )
        await c.connect()
        try:
            await c.initialize()
            assert await c.wait_for_initialization(timeout=5.0)
            # The devices that did answer are populated...
            assert c.devices["102"].current_value == 3
            # ...and the silent one is left at its unknown value.
            assert c.devices["107"].current_value == 0
        finally:
            await c.disconnect()

    async def test_watchdog_waits_while_statuses_keep_arriving(self, mock_server):
        """The settle timer only fires after status traffic actually stops."""
        self._silence_family(mock_server, "16")
        c = AVEDominaClient(
            host="127.0.0.1",
            port=mock_server.port,
            command_delay=0,
            status_settle_timeout=0.3,
        )
        await c.connect()
        try:
            await c.initialize()
            assert await c.wait_for_initialization(timeout=5.0)
            assert c._status_watchdog_task is None or c._status_watchdog_task.done()
        finally:
            await c.disconnect()

    async def test_watchdog_cancelled_once_all_statuses_arrive(self, client):
        """A fully reporting system completes without the watchdog firing."""
        await client.initialize()
        assert await client.wait_for_initialization(timeout=5.0)
        assert not client._pending_devices
        assert client._status_watchdog_task is None

    async def test_watchdog_disabled_by_zero_timeout(self, mock_server):
        """status_settle_timeout=0 restores the strict wait-for-everything mode."""
        self._silence_family(mock_server, "16")
        c = AVEDominaClient(
            host="127.0.0.1",
            port=mock_server.port,
            command_delay=0,
            status_settle_timeout=0,
        )
        await c.connect()
        try:
            await c.initialize()
            assert not await c.wait_for_initialization(timeout=1.0)
        finally:
            await c.disconnect()


class TestDeviceIdentityAcrossReinit:
    """Device objects must survive a re-read of the device list."""

    async def test_devices_are_updated_in_place(self, client):
        """Re-running LDI keeps the same DominaDevice objects."""
        await client.initialize()
        assert await client.wait_for_initialization(timeout=5.0)
        before = client.devices["102"]
        thermo_before = client.thermostats["103"]

        client._ldi_loaded = False
        await client._handle_ldi([], [["102", "Window Blind", "3", "1;2"]])

        assert client.devices["102"] is before
        # Devices no longer listed are dropped.
        assert "100" not in client.devices
        assert "103" not in client.thermostats
        assert thermo_before is not None

    async def test_attached_travel_estimator_survives_reinit(self, client):
        """A travel estimator attached by a consumer is not thrown away."""
        await client.initialize()
        assert await client.wait_for_initialization(timeout=5.0)
        device = client.devices["102"]
        estimator = device.attach_travel_estimator(20.0, 20.0)

        client._ldi_loaded = False
        await client._handle_ldi([], [["102", "Window Blind", "3", "1;2"]])

        assert client.devices["102"].travel_estimator is estimator

    async def test_reinit_refreshes_names_and_types(self, client):
        """Renamed or retyped devices are updated rather than duplicated."""
        await client.initialize()
        assert await client.wait_for_initialization(timeout=5.0)
        before = client.devices["102"]

        client._ldi_loaded = False
        await client._handle_ldi([], [["102", "Renamed Blind", "16", "3"]])

        assert client.devices["102"] is before
        assert before.name == "Renamed Blind"
        assert before.device_type == 16


class TestReconnectTaskOwnership:
    """The reconnect task reference must stay cancellable by disconnect()."""

    async def test_reconnect_does_not_clobber_a_newer_task(self, mock_server):
        """A socket that dies during initialize() registers a new task.

        The finishing reconnect must not null out that newer reference, or
        disconnect() loses its handle on a task that will re-open the
        connection after shutdown.
        """
        client = AVEDominaClient(
            host="127.0.0.1",
            port=mock_server.port,
            command_delay=0,
            reconnect_interval=0.01,
        )
        await client.connect()
        newer = asyncio.ensure_future(asyncio.sleep(3600))
        original_initialize = client.initialize

        async def initialize_then_lose_the_socket():
            # what _listen_loop's finally block does on a second drop
            client._reconnect_task = newer
            await original_initialize()

        client.initialize = initialize_then_lose_the_socket
        try:
            await client._reconnect_loop()
            assert client._reconnect_task is newer
        finally:
            client.initialize = original_initialize
            await client.disconnect()
        # disconnect() could reach it, so it is not left running
        assert newer.cancelled() or newer.done()

    async def test_own_task_reference_is_cleared_on_success(self, mock_server):
        """A clean reconnect still clears its own reference.

        Set up the real precondition: the listen loop has already exited
        (that is what spawns a reconnect), so nothing else registers a
        task while this one runs.
        """
        client = AVEDominaClient(
            host="127.0.0.1",
            port=mock_server.port,
            command_delay=0,
            reconnect_interval=0.01,
        )
        await client.connect()
        assert client._listen_task is not None
        client._listen_task.cancel()
        try:
            await client._listen_task
        except asyncio.CancelledError:
            pass
        client._listen_task = None
        if client._ws and not client._ws.closed:
            await client._ws.close()
        client._ws = None
        try:
            task = asyncio.ensure_future(client._reconnect_loop())
            client._reconnect_task = task
            await task
            assert client._reconnect_task is None
        finally:
            await client.disconnect()


class TestThermostatSetPointMode:
    """set_thermostat_set_point must not leave a thermostat on its schedule.

    A set point sent while the thermostat is in auto mode is accepted and
    then overwritten at the next scheduled change point, which looks to a
    user like the value silently reverting.
    """

    @staticmethod
    def _last_sts(mock_server) -> list[str]:
        sts = [c for c in mock_server.received_commands if c["command"] == "STS"]
        assert sts, "no STS command was sent"
        return sts[-1]["records"][0]

    async def test_switches_a_scheduled_thermostat_to_manual(self, client, mock_server):
        """The default sends manual in the same STS as the set point."""
        await client.initialize()
        assert await client.wait_for_initialization(timeout=5.0)
        thermo = client.thermostats["103"]
        thermo.mode = THERMOSTAT_MODE_AUTO

        await client.set_thermostat_set_point("103", 22.5)
        await asyncio.sleep(0.2)

        season, mode, raw_sp = self._last_sts(mock_server)
        assert mode == str(THERMOSTAT_MODE_MANUAL)
        assert raw_sp == "225"
        assert season == str(thermo.season)

    async def test_updates_cached_mode_and_manual_set_point(self, client, mock_server):
        """The optimistic cache update follows the mode that was sent."""
        await client.initialize()
        assert await client.wait_for_initialization(timeout=5.0)
        thermo = client.thermostats["103"]
        thermo.mode = THERMOSTAT_MODE_AUTO
        thermo.manual_set_point = 18.0

        await client.set_thermostat_set_point("103", 22.5)
        await asyncio.sleep(0.2)

        assert thermo.mode == THERMOSTAT_MODE_MANUAL
        assert thermo.set_point == 22.5
        assert thermo.manual_set_point == 22.5

    async def test_only_one_sts_and_one_wts(self, client, mock_server):
        """Mode and set point go in a single command, not two.

        Driving the mode separately would send two STS commands and briefly
        land the thermostat on the previously saved manual set point.
        """
        await client.initialize()
        assert await client.wait_for_initialization(timeout=5.0)
        client.thermostats["103"].mode = THERMOSTAT_MODE_AUTO
        before_sts = len(
            [c for c in mock_server.received_commands if c["command"] == "STS"]
        )
        before_wts = len(
            [c for c in mock_server.received_commands if c["command"] == "WTS"]
        )

        await client.set_thermostat_set_point("103", 22.5)
        await asyncio.sleep(0.3)

        sts = [c for c in mock_server.received_commands if c["command"] == "STS"]
        wts = [c for c in mock_server.received_commands if c["command"] == "WTS"]
        assert len(sts) - before_sts == 1
        assert len(wts) - before_wts == 1

    async def test_opt_out_preserves_the_current_mode(self, client, mock_server):
        """switch_to_manual=False sends the set point without touching mode."""
        await client.initialize()
        assert await client.wait_for_initialization(timeout=5.0)
        thermo = client.thermostats["103"]
        thermo.mode = THERMOSTAT_MODE_AUTO
        thermo.manual_set_point = 18.0

        await client.set_thermostat_set_point("103", 22.5, switch_to_manual=False)
        await asyncio.sleep(0.2)

        _, mode, raw_sp = self._last_sts(mock_server)
        assert mode == str(THERMOSTAT_MODE_AUTO)
        assert raw_sp == "225"
        assert thermo.mode == THERMOSTAT_MODE_AUTO
        # the saved manual set point is for manual mode only
        assert thermo.manual_set_point == 18.0

    async def test_already_manual_is_unchanged(self, client, mock_server):
        """A thermostat already in manual behaves as it always did."""
        await client.initialize()
        assert await client.wait_for_initialization(timeout=5.0)
        thermo = client.thermostats["103"]
        thermo.mode = THERMOSTAT_MODE_MANUAL

        await client.set_thermostat_set_point("103", 21.0)
        await asyncio.sleep(0.2)

        _, mode, raw_sp = self._last_sts(mock_server)
        assert mode == str(THERMOSTAT_MODE_MANUAL)
        assert raw_sp == "210"
        assert thermo.manual_set_point == 21.0

    async def test_unknown_thermostat_is_a_no_op(self, client, mock_server):
        """An unknown device id still returns without sending anything."""
        await client.initialize()
        assert await client.wait_for_initialization(timeout=5.0)
        before = len(mock_server.received_commands)
        await client.set_thermostat_set_point("does-not-exist", 22.0)
        await asyncio.sleep(0.1)
        assert len(mock_server.received_commands) == before
