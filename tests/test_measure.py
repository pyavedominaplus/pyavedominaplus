"""Tests for shutter travel time measurement."""

import asyncio

import pytest
import pytest_asyncio

from pyavedominaplus import AVEDominaClient, measure_shutter_travel_times
from tests.mock_server import MockDominaServer


@pytest_asyncio.fixture
async def fast_server():
    """Mock server whose shutters take 0.3s to travel."""
    server = MockDominaServer(shutter_transition_time=0.3)
    await server.start()
    yield server
    await server.stop()


@pytest_asyncio.fixture
async def client(fast_server):
    """Connected and initialized client."""
    c = AVEDominaClient(host="127.0.0.1", port=fast_server.port, command_delay=0)
    await c.connect()
    await c.initialize()
    assert await c.wait_for_initialization(timeout=5.0)
    yield c
    await c.disconnect()


async def test_measure_travel_times(client: AVEDominaClient) -> None:
    """Both directions are measured close to the mock's transition time."""
    m = await measure_shutter_travel_times(client, "102", phase_timeout=5.0)
    assert m.device_id == "102"
    assert m.name == "Window Blind"
    assert 0.1 < m.open_time < 1.5
    assert 0.1 < m.close_time < 1.5


async def test_measure_starts_from_closed_reference(
    client: AVEDominaClient, fast_server: MockDominaServer
) -> None:
    """A shutter that is currently open is closed before measuring."""
    fast_server.device_statuses["102"] = 1  # fully open
    client.devices["102"].update_status(1)
    m = await measure_shutter_travel_times(client, "102", phase_timeout=5.0)
    assert 0.1 < m.open_time < 1.5


async def test_measure_rejects_non_shutter(client: AVEDominaClient) -> None:
    """Lights and unknown devices are rejected."""
    with pytest.raises(ValueError):
        await measure_shutter_travel_times(client, "100")
    with pytest.raises(ValueError):
        await measure_shutter_travel_times(client, "does-not-exist")


async def test_measure_times_out(fast_server: MockDominaServer) -> None:
    """A shutter that never reaches its end state raises TimeoutError."""
    fast_server.shutter_transition_time = 60.0
    c = AVEDominaClient(host="127.0.0.1", port=fast_server.port, command_delay=0)
    await c.connect()
    try:
        await c.initialize()
        assert await c.wait_for_initialization(timeout=5.0)
        with pytest.raises(TimeoutError):
            await measure_shutter_travel_times(c, "102", phase_timeout=0.5)
    finally:
        await c.disconnect()


async def test_measure_rejects_unknown_status(client: AVEDominaClient) -> None:
    """A shutter whose status never arrived is rejected instead of hanging."""
    client.devices["102"].update_status(0)
    with pytest.raises(ValueError, match="no known status"):
        await measure_shutter_travel_times(client, "102", phase_timeout=0.5)


async def test_measure_reports_progress(client: AVEDominaClient) -> None:
    """Each phase is announced through the progress callback."""
    seen: list[str] = []
    await measure_shutter_travel_times(
        client, "102", phase_timeout=5.0, progress=seen.append
    )
    assert any("full open" in m for m in seen)
    assert any("full close" in m for m in seen)


async def test_measure_stops_shutter_on_timeout(
    fast_server: MockDominaServer,
) -> None:
    """A timed-out phase does not leave the motor running."""
    fast_server.shutter_transition_time = 60.0
    c = AVEDominaClient(host="127.0.0.1", port=fast_server.port, command_delay=0)
    await c.connect()
    try:
        await c.initialize()
        assert await c.wait_for_initialization(timeout=5.0)
        with pytest.raises(TimeoutError):
            await measure_shutter_travel_times(c, "102", phase_timeout=0.5)
        await asyncio.sleep(0.2)
        assert fast_server.device_statuses["102"] == 5  # STOPPED
    finally:
        await c.disconnect()
