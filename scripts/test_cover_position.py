"""Interactively drive a cover to percentage positions, by travel time.

The protocol has no notion of position, so this uses the travel times
measured by measure_covers.py to drive a shutter to an arbitrary
percentage: it starts the motor, waits the interpolated time, then stops.

Flow:
  1. The cover must start fully open or fully closed, because a partial
     position cannot be known. If it is part way, the first step (after
     confirmation) is to open it fully to establish the reference.
  2. Enter a target position - 0 (fully closed) to 100 (fully open) in
     steps of 10 - and the cover moves there.
  3. Repeat from wherever it now is, until you enter 'q', which closes
     the cover and exits.

Each move reports the predicted travel time against the time it actually
took, which is the number that says whether the estimate is trustworthy.

WARNING: this physically moves the cover.

Usage:
    python test_cover_position.py <host> [port] [--device-id ID]
                                  [--config PATH] [--verbose]
"""

import argparse
import asyncio
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from shutter_config import DEFAULT_CONFIG_PATH, ConfigError, TravelTimeConfig

from pyavedominaplus import (
    DEFAULT_PHASE_TIMEOUT,
    EVENT_DEVICE_STATUS,
    AVEDominaClient,
    DominaDevice,
)
from pyavedominaplus.const import (
    SHUTTER_STATUS_CLOSED,
    SHUTTER_STATUS_CLOSING,
    SHUTTER_STATUS_OPEN,
    SHUTTER_STATUS_OPENING,
    SHUTTER_STATUS_STOPPED,
)
from pyavedominaplus.travel import ShutterTravelEstimator

#: Reads a line from the user. Injected so the loop can be tested.
Prompt = Callable[[str], Awaitable[str]]

POSITION_STEP = 10


async def console_prompt(text: str) -> str:
    """Read a line without blocking the event loop.

    input() would stall the loop, so the client could not answer the
    server's keepalive pings while a prompt sits unanswered.
    """
    return (await asyncio.to_thread(input, text)).strip()


class StatusWaiter:
    """Collects a device's pushed statuses so moves can wait on them."""

    def __init__(self, client: AVEDominaClient, device_id: str, timeout: float) -> None:
        self._device_id = device_id
        self._timeout = timeout
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._unregister = client.register_update_callback(self._on_update)

    def _on_update(self, event_type: str, data: dict[str, Any]) -> None:
        if (
            event_type == EVENT_DEVICE_STATUS
            and data.get("device_id") == self._device_id
        ):
            self._queue.put_nowait(int(data["status"]))

    def drain(self) -> None:
        """Discard buffered statuses so a new move starts from a clean slate."""
        while not self._queue.empty():
            self._queue.get_nowait()

    async def wait_for(self, *expected: int) -> float:
        """Wait for one of the expected statuses; return its arrival time."""
        async with asyncio.timeout(self._timeout):
            while True:
                if await self._queue.get() in expected:
                    return time.monotonic()

    def close(self) -> None:
        self._unregister()


def describe(device: DominaDevice) -> str:
    """Return a human name for a shutter's current status."""
    return {
        SHUTTER_STATUS_OPEN: "fully open",
        SHUTTER_STATUS_OPENING: "opening",
        SHUTTER_STATUS_CLOSED: "fully closed",
        SHUTTER_STATUS_CLOSING: "closing",
        SHUTTER_STATUS_STOPPED: "part way (stopped)",
    }.get(device.current_value, f"unknown (status {device.current_value})")


async def settle(waiter: StatusWaiter, device: DominaDevice) -> None:
    """Wait out a move already under way, e.g. one started at the wall switch.

    Commanding a shutter that is already travelling in that direction stops
    it rather than moving it further, so nothing may be sent until the
    cover is stationary. Waiting also often hands us a free reference: a
    wall move that runs to its limit ends at a known 0% or 100%.
    """
    if not (device.is_opening or device.is_closing):
        return
    # Discard statuses buffered from earlier moves first: a stale terminal
    # one would satisfy the wait immediately and let us command a cover
    # that is still travelling. Safe to drop, because the device reporting
    # itself as moving means its terminal push has not arrived yet, and
    # nothing awaits between that check and this drain.
    waiter.drain()
    print(f"  Cover is {describe(device)} (wall switch?), waiting for it to stop ...")
    await waiter.wait_for(
        SHUTTER_STATUS_OPEN, SHUTTER_STATUS_CLOSED, SHUTTER_STATUS_STOPPED
    )
    print(f"  It is now {describe(device)}.")


async def move_to(
    client: AVEDominaClient,
    waiter: StatusWaiter,
    device: DominaDevice,
    estimator: ShutterTravelEstimator,
    target: float,
) -> None:
    """Drive the cover to target percent and report how close it landed.

    Runs to the physical limit for 0 and 100, so the hardware's terminal
    push re-synchronizes the estimate. Intermediate targets are timed and
    stopped, which is where the estimate is actually load-bearing.
    """
    await settle(waiter, device)
    start = estimator.position
    if start is None:
        print("  Position unknown, cannot move. Re-establish the reference first.")
        return
    if abs(target - start) < 0.5:
        print(f"  Already at {start:.0f}%, nothing to do.")
        return

    opening = target > start
    drive = client.open_shutter if opening else client.close_shutter
    moving = SHUTTER_STATUS_OPENING if opening else SHUTTER_STATUS_CLOSING
    terminal = SHUTTER_STATUS_OPEN if opening else SHUTTER_STATUS_CLOSED
    to_limit = target >= 100.0 or target <= 0.0

    waiter.drain()
    print(f"  {'Opening' if opening else 'Closing'} {start:.0f}% -> {target:.0f}% ...")
    await drive(device.id)
    started = await waiter.wait_for(moving)

    # Predict from the freshly anchored estimate, so any interpolation
    # drift up to the motor actually starting is accounted for.
    predicted = estimator.travel_time_to(target)
    if predicted is None:  # pragma: no cover - position is known by here
        predicted = 0.0

    if to_limit:
        await waiter.wait_for(terminal)
    else:
        await asyncio.sleep(predicted)
        await client.stop_shutter(device.id)
        # Accept the terminal state too: if the estimate ran short the
        # cover may have hit its limit before the stop landed.
        await waiter.wait_for(SHUTTER_STATUS_STOPPED, terminal)
    actual = time.monotonic() - started

    position = estimator.position
    now = "unknown" if position is None else f"{position:.1f}%"
    print(
        f"  predicted {predicted:.1f}s, took {actual:.1f}s"
        f" ({actual - predicted:+.1f}s) - now {now}, hardware says {describe(device)}"
    )


async def establish_reference(
    client: AVEDominaClient,
    waiter: StatusWaiter,
    device: DominaDevice,
    prompt: Prompt,
) -> bool:
    """Make sure the cover is at a known end position. Returns True if it is."""
    await settle(waiter, device)
    if device.is_open or device.is_closed:
        print(f"Cover is {describe(device)} - reference is known.")
        return True

    print(f"Cover is {describe(device)}, so its position cannot be known.")
    answer = (
        await prompt("Fully open it now to establish the reference? [y/N] ")
    ).lower()
    if answer != "y":
        print("Aborted.")
        return False

    waiter.drain()
    print("  Opening fully ...")
    await client.open_shutter(device.id)
    await waiter.wait_for(SHUTTER_STATUS_OPENING)
    await waiter.wait_for(SHUTTER_STATUS_OPEN)
    print("  Reference established: fully open.")
    return True


def parse_target(answer: str) -> float | None:
    """Parse a position input, or return None and explain what is wrong."""
    try:
        value = int(answer)
    except ValueError:
        print(f"  '{answer}' is not a whole number.")
        return None
    if not 0 <= value <= 100:
        print(f"  {value} is out of range, use 0 to 100.")
        return None
    if value % POSITION_STEP:
        print(f"  {value} is not a multiple of {POSITION_STEP}.")
        return None
    return float(value)


async def choose_device(
    client: AVEDominaClient,
    config: TravelTimeConfig,
    device_id: str | None,
    config_path: Path,
    prompt: Prompt,
) -> DominaDevice | None:
    """Resolve which shutter to drive, asking the user if it is ambiguous."""
    if device_id:
        device = client.devices.get(device_id)
        if device is None or not device.is_shutter:
            print(f"ERROR: [{device_id}] is not a shutter on this system.")
            return None
        if config.get(device_id) is None:
            print(f"ERROR: no travel times for [{device_id}] in {config_path}.")
            return None
        return device

    candidates = [
        client.devices[did]
        for did in sorted(config.shutters)
        if did in client.devices and client.devices[did].is_shutter
    ]
    if not candidates:
        print(f"ERROR: none of the shutters in {config_path} are on this system.")
        return None
    if len(candidates) == 1:
        return candidates[0]

    print("\nShutters with measured travel times:")
    for i, device in enumerate(candidates, 1):
        entry = config.shutters[device.id]
        print(
            f"  {i}. [{device.id}] {device.name}"
            f" - open {entry.open_time:.1f}s, close {entry.close_time:.1f}s"
        )
    while True:
        answer = await prompt(f"Pick one (1-{len(candidates)}), or 'q' to quit: ")
        if answer.lower() in ("q", "quit"):
            return None
        try:
            index = int(answer) - 1
        except ValueError:
            index = -1
        if 0 <= index < len(candidates):
            return candidates[index]
        print("  Invalid choice.")


async def main(
    host: str,
    port: int,
    device_id: str | None,
    config_path: Path,
    phase_timeout: float = DEFAULT_PHASE_TIMEOUT,
    prompt: Prompt = console_prompt,
) -> int:
    try:
        config = TravelTimeConfig.load(config_path)
    except ConfigError as err:
        print(f"ERROR: {err}")
        return 1
    if not config.shutters:
        print(
            f"No travel times in {config_path}.\n"
            f"Measure them first: python measure_covers.py {host}"
        )
        return 1

    client = AVEDominaClient(host=host, port=port)
    print(f"Connecting to {host}:{port} ...")
    await client.connect()
    try:
        await client.initialize()
        if not await client.wait_for_initialization(timeout=60.0):
            print("ERROR: initialization timed out")
            return 1

        device = await choose_device(client, config, device_id, config_path, prompt)
        if device is None:
            return 1
        entry = config.shutters[device.id]
        print(
            f"\n[{device.id}] {device.name}"
            f" - open {entry.open_time:.1f}s, close {entry.close_time:.1f}s"
            + (f" (measured {entry.measured_at})" if entry.measured_at else "")
        )

        estimator = device.attach_travel_estimator(entry.open_time, entry.close_time)
        waiter = StatusWaiter(client, device.id, phase_timeout)
        try:
            if not await establish_reference(client, waiter, device, prompt):
                return 1
            # attach_travel_estimator synced from the status at attach time;
            # re-sync in case establishing the reference moved the cover.
            estimator.update_from_status(device.current_value)

            while True:
                position = estimator.position
                now = "unknown" if position is None else f"{position:.0f}%"
                answer = await prompt(
                    f"\nAt {now}. Target % (0-100 in steps of {POSITION_STEP}),"
                    " or 'q' to close and finish: "
                )
                if answer.lower() in ("q", "quit"):
                    break
                target = parse_target(answer)
                if target is None:
                    continue
                current = estimator.position
                if (
                    position is not None
                    and current is not None
                    and abs(current - position) > 1.0
                ):
                    print(f"  (cover moved to {current:.0f}% while you were typing)")
                await move_to(client, waiter, device, estimator, target)

            if not device.is_closed:
                print("\nClosing the cover before exiting ...")
                await move_to(client, waiter, device, estimator, 0.0)
            else:
                print("\nCover is already closed.")
            return 0
        except TimeoutError:
            print(f"\nTIMEOUT: no status push within {phase_timeout:.0f}s.")
            await client.stop_shutter(device.id)
            return 1
        finally:
            waiter.close()
    finally:
        await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Interactively drive an AVE DominaPlus cover to percentages"
    )
    parser.add_argument("host", help="Server IP address")
    parser.add_argument("port", nargs="?", type=int, default=14001, help="Port")
    parser.add_argument(
        "--device-id", help="Shutter to drive (default: ask, if more than one)"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Travel time config to read (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--phase-timeout",
        type=float,
        default=DEFAULT_PHASE_TIMEOUT,
        help=(
            "Seconds to wait for each status transition"
            f" (default: {DEFAULT_PHASE_TIMEOUT:.0f})"
        ),
    )
    parser.add_argument("--verbose", action="store_true", help="Log protocol traffic")
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s"
        )
    try:
        sys.exit(
            asyncio.run(
                main(
                    args.host,
                    args.port,
                    args.device_id,
                    args.config,
                    args.phase_timeout,
                )
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
