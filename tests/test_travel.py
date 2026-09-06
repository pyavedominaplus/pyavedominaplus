"""Tests for time-based shutter position estimation."""

import pytest

from pyavedominaplus.const import (
    SHUTTER_STATUS_CLOSED,
    SHUTTER_STATUS_CLOSING,
    SHUTTER_STATUS_OPEN,
    SHUTTER_STATUS_OPENING,
    SHUTTER_STATUS_STOPPED,
)
from pyavedominaplus.models import DominaDevice
from pyavedominaplus.travel import ShutterTravelEstimator


class FakeClock:
    """Controllable monotonic clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


class TestShutterTravelEstimator:
    """Tests for the estimator state machine."""

    def test_invalid_travel_times_rejected(self) -> None:
        with pytest.raises(ValueError):
            ShutterTravelEstimator(0, 10)
        with pytest.raises(ValueError):
            ShutterTravelEstimator(10, -1)

    def test_position_unknown_until_terminal_state(self, clock: FakeClock) -> None:
        est = ShutterTravelEstimator(20, 20, time_func=clock)
        assert est.position is None
        est.start_opening()
        clock.tick(5)
        assert est.position is None
        est.stop()
        assert est.position is None
        est.set_fully_open()
        assert est.position == 100

    def test_opening_interpolates(self, clock: FakeClock) -> None:
        est = ShutterTravelEstimator(20, 10, time_func=clock)
        est.set_fully_closed()
        est.start_opening()
        clock.tick(5)
        assert est.position == 25
        clock.tick(5)
        assert est.position == 50
        est.stop()
        assert est.position == 50
        assert not est.is_traveling

    def test_closing_uses_close_time(self, clock: FakeClock) -> None:
        est = ShutterTravelEstimator(20, 10, time_func=clock)
        est.set_fully_open()
        est.start_closing()
        clock.tick(5)
        assert est.position == 50
        est.stop()
        assert est.position == 50

    def test_position_clamped_at_limits(self, clock: FakeClock) -> None:
        est = ShutterTravelEstimator(10, 10, time_func=clock)
        est.set_fully_closed()
        est.start_opening()
        clock.tick(60)
        assert est.position == 100
        est.start_closing()
        clock.tick(60)
        assert est.position == 0

    def test_terminal_state_resyncs_drift(self, clock: FakeClock) -> None:
        est = ShutterTravelEstimator(10, 10, time_func=clock)
        est.set_fully_closed()
        est.start_opening()
        clock.tick(3)
        est.set_fully_open()
        assert est.position == 100

    def test_direction_reversal_freezes_baseline(self, clock: FakeClock) -> None:
        est = ShutterTravelEstimator(20, 20, time_func=clock)
        est.set_fully_closed()
        est.start_opening()
        clock.tick(10)  # at 50
        est.start_closing()
        clock.tick(5)  # 50 - 25
        assert est.position == 25

    def test_travel_time_to(self, clock: FakeClock) -> None:
        est = ShutterTravelEstimator(20, 10, time_func=clock)
        assert est.travel_time_to(50) is None
        est.set_fully_closed()
        assert est.travel_time_to(50) == 10  # opening: half of 20s
        est.set_fully_open()
        assert est.travel_time_to(50) == 5  # closing: half of 10s
        assert est.travel_time_to(100) == 0

    def test_update_from_status(self, clock: FakeClock) -> None:
        est = ShutterTravelEstimator(20, 20, time_func=clock)
        est.update_from_status(SHUTTER_STATUS_CLOSED)
        assert est.position == 0
        est.update_from_status(SHUTTER_STATUS_OPENING)
        assert est.is_traveling
        clock.tick(10)
        assert est.position == 50
        est.update_from_status(SHUTTER_STATUS_STOPPED)
        assert est.position == 50
        est.update_from_status(SHUTTER_STATUS_CLOSING)
        clock.tick(5)
        est.update_from_status(SHUTTER_STATUS_OPEN)
        assert est.position == 100


class TestDeviceIntegration:
    """Tests for the estimator attached to a DominaDevice."""

    def test_attach_syncs_current_terminal_status(self) -> None:
        device = DominaDevice(
            id="201", name="Shutter", device_type=3, current_value=SHUTTER_STATUS_CLOSED
        )
        device.attach_travel_estimator(20, 20)
        assert device.estimated_position == 0

    def test_update_status_feeds_estimator(self, clock: FakeClock) -> None:
        device = DominaDevice(id="201", name="Shutter", device_type=3)
        device.travel_estimator = ShutterTravelEstimator(20, 20, time_func=clock)
        device.update_status(SHUTTER_STATUS_CLOSED)
        device.update_status(SHUTTER_STATUS_OPENING)
        clock.tick(10)
        assert device.estimated_position == 50
        device.update_status(SHUTTER_STATUS_STOPPED)
        assert device.estimated_position == 50

    def test_no_estimator_returns_none(self) -> None:
        device = DominaDevice(id="201", name="Shutter", device_type=3)
        assert device.estimated_position is None


class TestTravelSelfTermination:
    """The estimate settles even when the terminal status push is lost."""

    def test_opening_settles_at_open_without_terminal_push(
        self, clock: FakeClock
    ) -> None:
        est = ShutterTravelEstimator(20, 20, time_func=clock)
        est.update_from_status(SHUTTER_STATUS_CLOSED)
        est.update_from_status(SHUTTER_STATUS_OPENING)
        clock.tick(19)
        assert est.is_traveling
        assert est.position == pytest.approx(95)
        clock.tick(2)  # past the full travel time, no OPEN push arrived
        assert not est.is_traveling
        assert est.position == 100

    def test_closing_settles_at_closed_without_terminal_push(
        self, clock: FakeClock
    ) -> None:
        est = ShutterTravelEstimator(20, 10, time_func=clock)
        est.update_from_status(SHUTTER_STATUS_OPEN)
        est.update_from_status(SHUTTER_STATUS_CLOSING)
        clock.tick(11)
        assert not est.is_traveling
        assert est.position == 0

    def test_partial_run_only_needs_remaining_distance(self, clock: FakeClock) -> None:
        """Opening from 50% takes half the full travel time, not all of it."""
        est = ShutterTravelEstimator(20, 20, time_func=clock)
        est.update_from_status(SHUTTER_STATUS_CLOSED)
        est.update_from_status(SHUTTER_STATUS_OPENING)
        clock.tick(10)
        est.update_from_status(SHUTTER_STATUS_STOPPED)
        assert est.position == 50
        est.update_from_status(SHUTTER_STATUS_OPENING)
        clock.tick(9)
        assert est.is_traveling
        clock.tick(2)
        assert not est.is_traveling
        assert est.position == 100

    def test_unknown_start_resolves_after_a_full_travel(self, clock: FakeClock) -> None:
        """A full travel time reaches the limit whatever it started from."""
        est = ShutterTravelEstimator(20, 20, time_func=clock)
        est.update_from_status(SHUTTER_STATUS_OPENING)
        clock.tick(10)
        assert est.position is None  # still unknown mid-run
        clock.tick(11)
        assert est.position == 100
        assert not est.is_traveling

    def test_stop_after_settling_keeps_terminal_position(
        self, clock: FakeClock
    ) -> None:
        est = ShutterTravelEstimator(20, 20, time_func=clock)
        est.update_from_status(SHUTTER_STATUS_CLOSED)
        est.update_from_status(SHUTTER_STATUS_OPENING)
        clock.tick(30)
        est.update_from_status(SHUTTER_STATUS_STOPPED)
        assert est.position == 100

    def test_reversing_after_settling_starts_from_the_limit(
        self, clock: FakeClock
    ) -> None:
        est = ShutterTravelEstimator(20, 20, time_func=clock)
        est.update_from_status(SHUTTER_STATUS_CLOSED)
        est.update_from_status(SHUTTER_STATUS_OPENING)
        clock.tick(30)  # settled at 100 without an OPEN push
        est.update_from_status(SHUTTER_STATUS_CLOSING)
        clock.tick(10)
        assert est.position == pytest.approx(50)


class TestPhysicalSwitchTracking:
    """Moves started at the wall switch must be tracked like commanded ones.

    The client feeds every pushed status into the estimator, so it makes no
    difference whether the move came from us or from someone on the wall.
    """

    def test_full_open_from_the_wall_is_tracked(self, clock: FakeClock) -> None:
        device = DominaDevice(id="71", name="Shutter", device_type=3)
        device.travel_estimator = ShutterTravelEstimator(20, 20, time_func=clock)
        device.update_status(SHUTTER_STATUS_CLOSED)
        assert device.estimated_position == 0
        # someone presses "up" on the wall
        device.update_status(SHUTTER_STATUS_OPENING)
        clock.tick(20)
        device.update_status(SHUTTER_STATUS_OPEN)
        assert device.estimated_position == 100

    def test_partial_move_from_the_wall_is_tracked(self, clock: FakeClock) -> None:
        device = DominaDevice(id="71", name="Shutter", device_type=3)
        device.travel_estimator = ShutterTravelEstimator(20, 20, time_func=clock)
        device.update_status(SHUTTER_STATUS_CLOSED)
        device.update_status(SHUTTER_STATUS_OPENING)
        clock.tick(5)
        device.update_status(SHUTTER_STATUS_STOPPED)
        assert device.estimated_position == pytest.approx(25)

    def test_wall_switch_reversal_is_tracked(self, clock: FakeClock) -> None:
        """Reversing direction re-anchors from the position reached so far."""
        device = DominaDevice(id="71", name="Shutter", device_type=3)
        device.travel_estimator = ShutterTravelEstimator(20, 20, time_func=clock)
        device.update_status(SHUTTER_STATUS_CLOSED)
        device.update_status(SHUTTER_STATUS_OPENING)
        clock.tick(16)  # 80%
        device.update_status(SHUTTER_STATUS_CLOSING)  # wall switch reverses it
        clock.tick(4)  # 20% of travel back down
        device.update_status(SHUTTER_STATUS_STOPPED)
        assert device.estimated_position == pytest.approx(60)

    def test_wall_move_while_position_unknown_stays_unknown(
        self, clock: FakeClock
    ) -> None:
        """Without a reference, a partial wall move cannot be located."""
        device = DominaDevice(id="71", name="Shutter", device_type=3)
        device.travel_estimator = ShutterTravelEstimator(20, 20, time_func=clock)
        device.update_status(SHUTTER_STATUS_OPENING)
        clock.tick(5)
        device.update_status(SHUTTER_STATUS_STOPPED)
        assert device.estimated_position is None

    def test_wall_move_to_a_limit_resynchronizes(self, clock: FakeClock) -> None:
        """A wall move that runs to a limit fixes an unknown position."""
        device = DominaDevice(id="71", name="Shutter", device_type=3)
        device.travel_estimator = ShutterTravelEstimator(20, 20, time_func=clock)
        device.update_status(SHUTTER_STATUS_OPENING)
        clock.tick(5)
        device.update_status(SHUTTER_STATUS_STOPPED)
        assert device.estimated_position is None
        device.update_status(SHUTTER_STATUS_CLOSING)
        clock.tick(20)
        device.update_status(SHUTTER_STATUS_CLOSED)
        assert device.estimated_position == 0


class TestSetPosition:
    """Seeding a known position, e.g. restoring one across a restart."""

    def test_rejects_out_of_range(self, clock: FakeClock) -> None:
        est = ShutterTravelEstimator(20, 20, time_func=clock)
        with pytest.raises(ValueError):
            est.set_position(-1)
        with pytest.raises(ValueError):
            est.set_position(100.5)

    def test_accepts_the_bounds(self, clock: FakeClock) -> None:
        est = ShutterTravelEstimator(20, 20, time_func=clock)
        est.set_position(0)
        assert est.position == 0
        est.set_position(100)
        assert est.position == 100

    def test_seeded_position_makes_travel_time_computable(
        self, clock: FakeClock
    ) -> None:
        """This is the point of the method: aiming works without a limit run."""
        est = ShutterTravelEstimator(20, 10, time_func=clock)
        assert est.travel_time_to(40) is None  # unknown until seeded
        est.set_position(65)
        assert est.position == 65
        assert not est.is_traveling
        assert est.travel_time_to(40) == pytest.approx(2.5)  # 25% of a 10s close
        assert est.travel_time_to(100) == pytest.approx(7.0)  # 35% of a 20s open

    def test_seeding_mid_travel_clears_the_run(self, clock: FakeClock) -> None:
        est = ShutterTravelEstimator(20, 20, time_func=clock)
        est.update_from_status(SHUTTER_STATUS_CLOSED)
        est.update_from_status(SHUTTER_STATUS_OPENING)
        clock.tick(4)
        est.set_position(65)
        assert est.position == 65
        assert not est.is_traveling
        clock.tick(10)
        assert est.position == 65  # no longer accumulating travel

    def test_a_later_terminal_state_still_resynchronizes(
        self, clock: FakeClock
    ) -> None:
        """A seed is a restored estimate, not a terminal synchronization."""
        est = ShutterTravelEstimator(20, 20, time_func=clock)
        est.set_position(65)
        est.update_from_status(SHUTTER_STATUS_CLOSING)
        clock.tick(20)
        est.update_from_status(SHUTTER_STATUS_CLOSED)
        assert est.position == 0

    def test_travel_from_a_seed_interpolates(self, clock: FakeClock) -> None:
        est = ShutterTravelEstimator(20, 20, time_func=clock)
        est.set_position(50)
        est.update_from_status(SHUTTER_STATUS_OPENING)
        clock.tick(4)  # 20% of a 20s open
        assert est.position == pytest.approx(70)


class TestAttachWithInitialPosition:
    """attach_travel_estimator must apply a seed before the current status."""

    @staticmethod
    def _device(status: int) -> DominaDevice:
        return DominaDevice(
            id="102", name="Shutter", device_type=3, current_value=status
        )

    def test_stopped_keeps_the_seed(self) -> None:
        """A part-way shutter is exactly the case the seed exists for."""
        device = self._device(SHUTTER_STATUS_STOPPED)
        device.attach_travel_estimator(20, 20, initial_position=65)
        assert device.estimated_position == 65

    def test_never_reported_keeps_the_seed(self) -> None:
        """current_value 0 means no status ever arrived; the seed stands."""
        device = self._device(0)
        device.attach_travel_estimator(20, 20, initial_position=65)
        assert device.estimated_position == 65

    def test_closed_now_overrides_the_seed(self) -> None:
        """A shutter reported closed is at 0, whatever it was before."""
        device = self._device(SHUTTER_STATUS_CLOSED)
        device.attach_travel_estimator(20, 20, initial_position=65)
        assert device.estimated_position == 0

    def test_open_now_overrides_the_seed(self) -> None:
        device = self._device(SHUTTER_STATUS_OPEN)
        device.attach_travel_estimator(20, 20, initial_position=65)
        assert device.estimated_position == 100

    def test_moving_now_anchors_on_the_seed(self) -> None:
        """A shutter already opening carries on from the restored position.

        Without the seed this would report None until the run finished,
        because a move from an unknown position stays unknown.
        """
        device = self._device(SHUTTER_STATUS_OPENING)
        estimator = device.attach_travel_estimator(20, 20, initial_position=50)
        assert estimator.is_traveling
        assert device.estimated_position == pytest.approx(50, abs=1.0)

    def test_moving_without_a_seed_stays_unknown(self) -> None:
        """The contrast case: no seed means no position until a limit."""
        device = self._device(SHUTTER_STATUS_OPENING)
        device.attach_travel_estimator(20, 20)
        assert device.estimated_position is None

    def test_without_a_seed_behaviour_is_unchanged(self) -> None:
        device = self._device(SHUTTER_STATUS_STOPPED)
        device.attach_travel_estimator(20, 20)
        assert device.estimated_position is None

    def test_seed_is_validated(self) -> None:
        device = self._device(SHUTTER_STATUS_STOPPED)
        with pytest.raises(ValueError):
            device.attach_travel_estimator(20, 20, initial_position=150)


class TestTravelTimeProperties:
    """Consumers need to read back what an estimator was configured with."""

    def test_open_and_close_time_are_readable(self) -> None:
        est = ShutterTravelEstimator(31.1, 29.9)
        assert est.open_time == 31.1
        assert est.close_time == 29.9

    def test_readable_from_an_attached_estimator(self) -> None:
        device = DominaDevice(id="102", name="Shutter", device_type=3)
        estimator = device.attach_travel_estimator(12.5, 11.75)
        assert estimator.open_time == 12.5
        assert estimator.close_time == 11.75
