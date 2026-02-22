"""Tests for AVE DominaPlus async client using mock server."""

import asyncio

import pytest
import pytest_asyncio

from pyavedominaplus.client import AVEDominaClient
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
    c = AVEDominaClient(host="127.0.0.1", port=mock_server.port)
    await c.connect()
    yield c
    await c.disconnect()


class TestClientConnection:
    """Tests for client connection management."""

    async def test_connect_disconnect(self, mock_server):
        """Test basic connect and disconnect."""
        client = AVEDominaClient(host="127.0.0.1", port=mock_server.port)
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
        client = AVEDominaClient(host="127.0.0.1", port=mock_server.port)
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
        assert len(area.map_commands) == 3


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
        """Test that dimmer level is clamped to 0-254."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.set_dimmer_level("101", 300)
        await asyncio.sleep(0.2)
        assert mock_server.device_statuses["101"] == 31

    async def test_open_shutter(self, client, mock_server):
        """Test opening a shutter."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.open_shutter("102")
        await asyncio.sleep(0.2)
        assert mock_server.device_statuses["102"] == 1

    async def test_close_shutter(self, client, mock_server):
        """Test closing a shutter."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.close_shutter("102")
        await asyncio.sleep(0.2)
        assert mock_server.device_statuses["102"] == 0

    async def test_stop_shutter(self, client, mock_server):
        """Test stopping a shutter."""
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.stop_shutter("102")
        await asyncio.sleep(0.2)
        assert mock_server.device_statuses["102"] == 2

    async def test_activate_scenario(self, client, mock_server):
        """Test activating a scenario."""
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
        client = AVEDominaClient(host="127.0.0.1", port=mock_server.port)
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
        client = AVEDominaClient(host="127.0.0.1", port=mock_server.port)
        await client.connect()
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        # Device "999" doesn't exist
        await client.set_thermostat_set_point("999", 22.0)
        # No error, just silently returns
        await client.disconnect()

    async def test_set_thermostat_season_nonexistent(self, mock_server):
        """Test setting thermostat season for non-existent thermostat is a no-op."""
        client = AVEDominaClient(host="127.0.0.1", port=mock_server.port)
        await client.connect()
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.set_thermostat_season("999", 0)
        await client.disconnect()

    async def test_dimmer_level_clamped_negative(self, mock_server):
        """Test that negative dimmer level is clamped to 0."""
        client = AVEDominaClient(host="127.0.0.1", port=mock_server.port)
        await client.connect()
        await client.initialize()
        await client.wait_for_initialization(timeout=5.0)

        await client.set_dimmer_level("101", -10)
        await asyncio.sleep(0.2)
        assert mock_server.device_statuses["101"] == 0
        await client.disconnect()

    async def test_wait_for_initialization_timeout(self, mock_server):
        """Test that wait_for_initialization returns False on timeout."""
        client = AVEDominaClient(host="127.0.0.1", port=mock_server.port)
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
                host="127.0.0.1", port=mock_server.port, session=session
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
        client = AVEDominaClient(host="127.0.0.1", port=mock_server.port)
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
        c = AVEDominaClient(host="127.0.0.1", port=mock_server.port)
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
