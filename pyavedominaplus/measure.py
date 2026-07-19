"""Measure shutter travel times against real hardware.

Drives a shutter through a full close, then times a full open and a full
close using the status transitions the server pushes (opening -> open,
closing -> closed). The results are the travel times needed by
ShutterTravelEstimator.
"""

import asyncio
from dataclasses import dataclass
import time
from typing import Any

from .client import AVEDominaClient
from .const import (
    EVENT_DEVICE_STATUS,
    SHUTTER_STATUS_CLOSED,
    SHUTTER_STATUS_CLOSING,
    SHUTTER_STATUS_OPEN,
    SHUTTER_STATUS_OPENING,
    SHUTTER_STATUS_STOPPED,
)

DEFAULT_PHASE_TIMEOUT = 180.0


@dataclass
class ShutterTravelMeasurement:
    """Measured full-travel times for one shutter."""

    device_id: str
    name: str
    open_time: float
    close_time: float


async def measure_shutter_travel_times(
    client: AVEDominaClient,
    device_id: str,
    phase_timeout: float = DEFAULT_PHASE_TIMEOUT,
) -> ShutterTravelMeasurement:
    """Measure a shutter's full open and close travel times.

    The shutter is physically driven: first fully closed (if it is not
    already), then fully opened, then fully closed again. Each phase must
    complete within phase_timeout seconds or TimeoutError is raised.
    """
    device = client.devices.get(device_id)
    if device is None or not device.is_shutter:
        raise ValueError(f"Device {device_id} is not a shutter")

    statuses: asyncio.Queue[int] = asyncio.Queue()

    def _on_update(event_type: str, data: dict[str, Any]) -> None:
        if event_type == EVENT_DEVICE_STATUS and data.get("device_id") == device_id:
            statuses.put_nowait(int(data["status"]))

    async def _wait_for(*expected: int) -> float:
        """Wait for one of the expected statuses; return its arrival time."""
        async with asyncio.timeout(phase_timeout):
            while True:
                status = await statuses.get()
                if status in expected:
                    return time.monotonic()

    unregister = client.register_update_callback(_on_update)
    try:
        # Let any movement in progress settle first
        if device.is_opening:
            await _wait_for(
                SHUTTER_STATUS_OPEN, SHUTTER_STATUS_STOPPED, SHUTTER_STATUS_CLOSED
            )
        elif device.is_closing:
            await _wait_for(
                SHUTTER_STATUS_CLOSED, SHUTTER_STATUS_STOPPED, SHUTTER_STATUS_OPEN
            )

        # Start from a known reference: fully closed
        if not device.is_closed:
            await client.close_shutter(device_id)
            await _wait_for(SHUTTER_STATUS_CLOSED)

        # Time a full open: opening push -> open push
        await client.open_shutter(device_id)
        opening_started = await _wait_for(SHUTTER_STATUS_OPENING)
        open_time = await _wait_for(SHUTTER_STATUS_OPEN) - opening_started

        # Time a full close: closing push -> closed push
        await client.close_shutter(device_id)
        closing_started = await _wait_for(SHUTTER_STATUS_CLOSING)
        close_time = await _wait_for(SHUTTER_STATUS_CLOSED) - closing_started
    finally:
        unregister()

    return ShutterTravelMeasurement(
        device_id=device_id,
        name=device.name,
        open_time=open_time,
        close_time=close_time,
    )
