"""End-to-end tests of the measure -> config -> drive CLI flow.

The interactive loop takes its input through an injected prompt, so the
whole thing runs against the mock server without a human.
"""

import asyncio

import pytest
import pytest_asyncio

import measure_covers
import test_cover_position
from shutter_config import TravelTimeConfig
from tests.mock_server import MockDominaServer

CLOSED, OPEN, STOPPED = 3, 1, 5


def scripted(*answers: str):
    """Return a prompt that replays answers, failing on an unexpected ask."""
    remaining = list(answers)
    asked: list[str] = []

    async def _prompt(text: str) -> str:
        asked.append(text)
        if not remaining:
            raise AssertionError(f"unexpected prompt: {text!r}")
        return remaining.pop(0)

    _prompt.asked = asked  # type: ignore[attr-defined]
    return _prompt


@pytest_asyncio.fixture
async def server():
    """Mock server whose shutters take 0.4s for a full run."""
    s = MockDominaServer(shutter_transition_time=0.4)
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "times.json"


@pytest_asyncio.fixture
async def measured(server, config_path):
    """Measure shutter 102 so the config has real travel times."""
    rc = await measure_covers.main(
        "127.0.0.1", server.port, ["102"], True, 10.0, config_path
    )
    assert rc == 0
    return config_path


async def drive(server, config_path, *answers, device_id=None):
    return await test_cover_position.main(
        "127.0.0.1",
        server.port,
        device_id,
        config_path,
        10.0,
        prompt=scripted(*answers),
    )


class TestMeasureWritesConfig:
    async def test_config_contents(self, server, measured):
        config = TravelTimeConfig.load(measured)
        entry = config.get("102")
        assert entry is not None
        assert entry.name == "Window Blind"
        assert 0.2 < entry.open_time < 1.5
        assert 0.2 < entry.close_time < 1.5
        assert config.host == "127.0.0.1"

    async def test_no_config_writes_nothing(self, server, config_path):
        rc = await measure_covers.main(
            "127.0.0.1", server.port, ["102"], True, 10.0, None
        )
        assert rc == 0
        assert not config_path.exists()


class TestInteractiveDriving:
    async def test_drives_to_each_target_then_closes(self, server, measured):
        """A run of targets, then 'q' leaves the cover closed."""
        rc = await drive(server, measured, "40", "80", "10", "q")
        assert rc == 0
        assert server.device_statuses["102"] == CLOSED
        assert server.shutter_positions["102"] == pytest.approx(0.0, abs=0.01)

    async def test_position_reached_is_accurate(self, server, measured):
        """The mock's true position matches the requested percentage."""
        seen = {}
        original = test_cover_position.move_to

        async def spy(client, waiter, device, estimator, target):
            await original(client, waiter, device, estimator, target)
            seen[target] = server.shutter_positions["102"]

        test_cover_position.move_to = spy
        try:
            rc = await drive(server, measured, "70", "30", "q")
        finally:
            test_cover_position.move_to = original
        assert rc == 0
        assert seen[70.0] == pytest.approx(0.70, abs=0.15)
        assert seen[30.0] == pytest.approx(0.30, abs=0.15)

    async def test_full_open_and_close_targets_use_the_limits(self, server, measured):
        """0 and 100 run to the physical limit rather than being timed."""
        rc = await drive(server, measured, "100", "0", "q")
        assert rc == 0
        assert server.device_statuses["102"] == CLOSED

    async def test_rejects_bad_input_and_keeps_asking(self, server, measured):
        """Non-numeric, out of range and off-step values are re-prompted."""
        prompt = scripted("abc", "150", "35", "-10", "q")
        rc = await test_cover_position.main(
            "127.0.0.1", server.port, None, measured, 10.0, prompt=prompt
        )
        assert rc == 0
        # every bad answer got its own prompt, plus the reference line
        assert len(prompt.asked) == 5

    async def test_already_at_target_is_a_no_op(self, server, measured):
        """Asking for the position it is already at does not move the motor."""
        rc = await drive(server, measured, "0", "q")
        assert rc == 0
        assert server.device_statuses["102"] == CLOSED


class TestReference:
    async def test_partially_open_cover_is_opened_after_confirmation(
        self, server, measured
    ):
        """A cover part way must be opened first to know where it is."""
        server.set_shutter_position("102", 0.5)
        rc = await drive(server, measured, "y", "q")
        assert rc == 0
        # opened to establish the reference, then closed on exit
        assert server.device_statuses["102"] == CLOSED

    async def test_declining_the_reference_aborts(self, server, measured):
        """Refusing to open leaves the cover untouched."""
        server.set_shutter_position("102", 0.5)
        rc = await drive(server, measured, "n")
        assert rc == 1
        assert server.device_statuses["102"] == STOPPED

    async def test_terminal_cover_needs_no_confirmation(self, server, measured):
        """Starting fully closed goes straight to the target prompt."""
        server.set_shutter_position("102", 0.0)
        prompt = scripted("q")
        rc = await test_cover_position.main(
            "127.0.0.1", server.port, None, measured, 10.0, prompt=prompt
        )
        assert rc == 0
        assert "Target %" in prompt.asked[0]


class TestDeviceSelection:
    async def test_missing_config_is_an_error(self, server, config_path):
        rc = await drive(server, config_path, "q")
        assert rc == 1

    async def test_device_absent_from_system_is_an_error(self, server, config_path):
        config = TravelTimeConfig(host="127.0.0.1")
        config.set_times("9999", 5.0, 5.0, "Ghost")
        config.save(config_path)
        rc = await test_cover_position.main(
            "127.0.0.1", server.port, None, config_path, 10.0, prompt=scripted()
        )
        assert rc == 1

    async def test_explicit_device_id_without_times_is_an_error(self, server, measured):
        rc = await test_cover_position.main(
            "127.0.0.1", server.port, "107", measured, 10.0, prompt=scripted()
        )
        assert rc == 1

    async def test_asks_which_shutter_when_several_are_configured(
        self, server, measured
    ):
        """Two configured shutters means the user picks one."""
        config = TravelTimeConfig.load(measured)
        config.set_times("107", 0.4, 0.4, "Roller Shutter")
        config.save(measured)
        prompt = scripted("2", "q")
        rc = await test_cover_position.main(
            "127.0.0.1", server.port, None, measured, 10.0, prompt=prompt
        )
        assert rc == 0
        assert "Pick one" in prompt.asked[0]


class TestWallSwitch:
    """Moves started at the physical wall switch must be noticed."""

    async def test_wall_move_while_at_the_prompt_is_picked_up(self, server, measured):
        """The cover moving under us is reflected in the next move."""
        positions = []
        original = test_cover_position.move_to

        async def spy(client, waiter, device, estimator, target):
            positions.append(estimator.position)
            await original(client, waiter, device, estimator, target)

        async def slow_prompt_factory():
            answers = ["30", "q"]

            async def _prompt(text: str) -> str:
                answer = answers.pop(0)
                if answer == "30":
                    # while the user is "typing", someone opens it fully
                    await server.press_wall_switch("102", "8")
                    await asyncio.sleep(0.6)
                return answer

            return _prompt

        test_cover_position.move_to = spy
        try:
            rc = await test_cover_position.main(
                "127.0.0.1",
                server.port,
                None,
                measured,
                10.0,
                prompt=await slow_prompt_factory(),
            )
        finally:
            test_cover_position.move_to = original
        assert rc == 0
        # It started closed; the wall switch took it to fully open, so the
        # move to 30% must have started from 100, not from 0.
        assert positions[0] == pytest.approx(100.0, abs=1.0)

    async def test_move_waits_for_an_in_progress_wall_move(self, server, measured):
        """Commanding a moving cover would stop it, so the script waits first."""
        server.set_shutter_position("102", 0.0)
        await server.press_wall_switch("102", "8")  # opening, mid-travel

        rc = await drive(server, measured, "50", "q")
        assert rc == 0
        assert server.device_statuses["102"] == CLOSED

    async def test_wall_move_at_startup_provides_the_reference(self, server, measured):
        """A wall move running to a limit removes the need to ask."""
        server.set_shutter_position("102", 0.2)
        await server.press_wall_switch("102", "8")  # will end fully open

        prompt = scripted("q")
        rc = await test_cover_position.main(
            "127.0.0.1", server.port, None, measured, 10.0, prompt=prompt
        )
        assert rc == 0
        # No reference confirmation needed: it settled at a known limit.
        assert "Target %" in prompt.asked[0]
        assert server.device_statuses["102"] == CLOSED

    async def test_stale_terminal_status_does_not_short_circuit_settle(
        self, server, measured
    ):
        """A queued terminal status must not be mistaken for "it stopped".

        Two wall presses while the user is at the prompt leave an OPEN in
        the queue and the cover still closing. Waiting on that stale OPEN
        would command a moving cover, which stops it instead of moving it,
        and then hang waiting for a motor-started push.
        """
        server.set_shutter_position("102", 0.0)
        answers = ["30", "q"]

        async def _prompt(text: str) -> str:
            answer = answers.pop(0)
            if answer == "30":
                await server.press_wall_switch("102", "8")  # opening
                await asyncio.sleep(0.6)  # runs to fully open -> OPEN queued
                await server.press_wall_switch("102", "9")  # now closing
                await asyncio.sleep(0.05)  # still travelling when we return
            return answer

        rc = await asyncio.wait_for(
            test_cover_position.main(
                "127.0.0.1", server.port, None, measured, 3.0, prompt=_prompt
            ),
            timeout=30,
        )
        assert rc == 0
        assert server.device_statuses["102"] == CLOSED
