"""Measure shutter travel times against real hardware.

Drives a shutter through a full close, then times a full open and a full
close using the status transitions the server pushes (opening -> open,
closing -> closed). The results are the travel times needed by
ShutterTravelEstimator.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
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

_LOGGER = logging.getLogger(__name__)

DEFAULT_PHASE_TIMEOUT = 180.0

#: Statuses a shutter can report once the server has told us about it.
#: current_value 0 means no status ever arrived, which makes it unsafe to
#: decide whether a reference close is needed.
KNOWN_SHUTTER_STATUSES = frozenset(
    {
        SHUTTER_STATUS_OPEN,
        SHUTTER_STATUS_OPENING,
        SHUTTER_STATUS_CLOSED,
        SHUTTER_STATUS_CLOSING,
        SHUTTER_STATUS_STOPPED,
    }
)

#: Called with a short human-readable description of each measurement phase.
ProgressCallback = Callable[[str], None]


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
    progress: ProgressCallback | None = None,
) -> ShutterTravelMeasurement:
    """Measure a shutter's full open and close travel times.

    The shutter is physically driven: first fully closed (if it is not
    already), then fully opened, then fully closed again. Each phase must
    complete within phase_timeout seconds or TimeoutError is raised; the
    shutter is stopped before the error propagates so it is not left
    running against its limit.

    progress, if given, is called with a description of each phase as it
    starts, which is the only feedback during runs that can take minutes.

    Raises ValueError if device_id is not a shutter or if its current
    status is unknown (no status has ever been received for it).
    """
    device = client.devices.get(device_id)
    if device is None or not device.is_shutter:
        raise ValueError(f"Device {device_id} is not a shutter")
    if device.current_value not in KNOWN_SHUTTER_STATUSES:
        raise ValueError(
            f"Shutter {device_id} has no known status "
            f"(current_value={device.current_value}); the server never "
            "reported it, so it cannot be measured"
        )

    statuses: asyncio.Queue[int] = asyncio.Queue()

    def _report(message: str) -> None:
        if progress is not None:
            progress(message)

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
        try:
            # Let any movement in progress settle first
            if device.is_opening:
                _report("waiting for the shutter to finish opening")
                await _wait_for(
                    SHUTTER_STATUS_OPEN, SHUTTER_STATUS_STOPPED, SHUTTER_STATUS_CLOSED
                )
            elif device.is_closing:
                _report("waiting for the shutter to finish closing")
                await _wait_for(
                    SHUTTER_STATUS_CLOSED, SHUTTER_STATUS_STOPPED, SHUTTER_STATUS_OPEN
                )

            # Start from a known reference: fully closed
            if not device.is_closed:
                _report("closing to the reference position")
                await client.close_shutter(device_id)
                await _wait_for(SHUTTER_STATUS_CLOSED)

            # Time a full open: opening push -> open push
            _report("timing a full open")
            await client.open_shutter(device_id)
            opening_started = await _wait_for(SHUTTER_STATUS_OPENING)
            open_time = await _wait_for(SHUTTER_STATUS_OPEN) - opening_started

            # Time a full close: closing push -> closed push
            _report("timing a full close")
            await client.close_shutter(device_id)
            closing_started = await _wait_for(SHUTTER_STATUS_CLOSING)
            close_time = await _wait_for(SHUTTER_STATUS_CLOSED) - closing_started
        except TimeoutError:
            # Do not leave the motor running when giving up on a phase.
            # Nothing may escape here: it would mask the TimeoutError that
            # tells the caller which phase actually stalled.
            try:
                await client.stop_shutter(device_id)
            except Exception:
                _LOGGER.warning(
                    "Could not stop shutter %s after a phase timeout; it may "
                    "still be moving",
                    device_id,
                    exc_info=True,
                )
            raise
    finally:
        unregister()

    return ShutterTravelMeasurement(
        device_id=device_id,
        name=device.name,
        open_time=open_time,
        close_time=close_time,
    )
