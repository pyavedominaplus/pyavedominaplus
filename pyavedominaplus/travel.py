"""Time-based shutter position estimation.

The AVE DominaPlus protocol only reports discrete shutter states (open,
opening, closed, closing, stopped). Given how long a shutter takes to
travel its full range in each direction, its position can be estimated
by interpolating over the time spent moving. Terminal states (fully
open/closed) re-synchronize the estimate, so drift self-corrects on
every complete run.
"""

import time
from typing import Callable

from .const import (
    SHUTTER_STATUS_CLOSED,
    SHUTTER_STATUS_CLOSING,
    SHUTTER_STATUS_OPEN,
    SHUTTER_STATUS_OPENING,
    SHUTTER_STATUS_STOPPED,
)

POSITION_CLOSED = 0.0
POSITION_OPEN = 100.0


class ShutterTravelEstimator:
    """Estimate a shutter's position (0=closed, 100=open) from travel times.

    Feed it the status transitions the server pushes (via
    ``update_from_status``); read ``position``. The position is None until
    the first terminal state (fully open/closed) synchronizes it.
    """

    def __init__(
        self,
        open_time: float,
        close_time: float,
        time_func: Callable[[], float] | None = None,
    ) -> None:
        """Initialize with full-travel times (seconds) per direction.

        time_func defaults to time.monotonic, resolved at call time so
        test clocks that patch the time module keep working.
        """
        if open_time <= 0 or close_time <= 0:
            raise ValueError("open_time and close_time must be positive")
        self._open_time = open_time
        self._close_time = close_time
        self._time_func = time_func
        self._position: float | None = None
        # +1 while opening, -1 while closing, 0 while idle
        self._direction = 0
        self._start_position: float | None = None
        self._started_at = 0.0

    def _time(self) -> float:
        if self._time_func is not None:
            return self._time_func()
        return time.monotonic()

    @property
    def is_traveling(self) -> bool:
        """Return True while the shutter is moving."""
        return self._direction != 0

    @property
    def position(self) -> float | None:
        """Return the current position estimate (0-100), if known."""
        if self._direction == 0:
            return self._position
        if self._start_position is None:
            # Moving from an unknown position stays unknown until a
            # terminal state synchronizes the estimate.
            return None
        travel_time = self._open_time if self._direction > 0 else self._close_time
        elapsed = self._time() - self._started_at
        delta = POSITION_OPEN * elapsed / travel_time
        return min(
            POSITION_OPEN,
            max(POSITION_CLOSED, self._start_position + self._direction * delta),
        )

    def travel_time_to(self, target: float) -> float | None:
        """Return seconds of travel needed to reach target, if computable."""
        current = self.position
        if current is None:
            return None
        delta = target - current
        travel_time = self._open_time if delta > 0 else self._close_time
        return abs(delta) / POSITION_OPEN * travel_time

    def start_opening(self) -> None:
        """Record that the shutter started opening."""
        self._begin(1)

    def start_closing(self) -> None:
        """Record that the shutter started closing."""
        self._begin(-1)

    def _begin(self, direction: int) -> None:
        self._start_position = self.position
        self._direction = direction
        self._started_at = self._time()

    def stop(self) -> None:
        """Record that the shutter stopped mid-travel."""
        self._position = self.position
        self._direction = 0
        self._start_position = None

    def set_fully_open(self) -> None:
        """Synchronize to the fully-open terminal state."""
        self._position = POSITION_OPEN
        self._direction = 0
        self._start_position = None

    def set_fully_closed(self) -> None:
        """Synchronize to the fully-closed terminal state."""
        self._position = POSITION_CLOSED
        self._direction = 0
        self._start_position = None

    def update_from_status(self, status: int) -> None:
        """Update the estimate from a shutter status transition."""
        if status == SHUTTER_STATUS_OPENING:
            self.start_opening()
        elif status == SHUTTER_STATUS_CLOSING:
            self.start_closing()
        elif status == SHUTTER_STATUS_STOPPED:
            self.stop()
        elif status == SHUTTER_STATUS_OPEN:
            self.set_fully_open()
        elif status == SHUTTER_STATUS_CLOSED:
            self.set_fully_closed()
