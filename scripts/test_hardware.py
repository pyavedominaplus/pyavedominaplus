"""Interactive hardware test for AVE DominaPlus.

Connects to real hardware, discovers devices, lets you pick one per category,
and walks through each operation with human confirmation.

Usage:
    python test_hardware.py <host> [port]
"""

import asyncio
import sys

from pyavedominaplus import AVEDominaClient

# -- Helpers ------------------------------------------------------------------

DEVICE_TYPE_NAMES = {
    1: "Light",
    2: "Dimmer",
    3: "Shutter",
    4: "Thermostat",
    5: "Economizer",
    6: "Scenario",
    9: "Energy",
    16: "Shutter (type 16)",
    19: "Shutter (type 19)",
    22: "Light (type 22)",
}


def print_device_state(device) -> None:
    """Print the current device state."""
    print(f"  State: value={device.current_value} is_on={device.is_on}")


def print_dimmer_state(device) -> None:
    """Print the current dimmer state."""
    print(
        f"  State: value={device.current_value} is_on={device.is_on} brightness={device.brightness}"
    )


def print_shutter_state(device) -> None:
    """Print the current shutter state."""
    print(
        f"  State: value={device.current_value} "
        f"opening={device.is_opening} open={device.is_open} "
        f"closing={device.is_closing} closed={device.is_closed} "
        f"stopped={device.is_stopped}"
    )


def print_thermo_state(thermo) -> None:
    """Print the full thermostat state after a WTS re-read."""
    print(
        f"  State: temp={thermo.temperature}°C sp={thermo.set_point}°C "
        f"season={'winter' if thermo.is_heating else 'summer'} "
        f"mode={'auto' if thermo.is_auto_mode else 'manual'} "
        f"local_off={thermo.local_off} fan={thermo.fan_level}"
        f"{f' humidity={thermo.humidity_value}%' if thermo.humidity_enabled else ''}"
    )


async def prompt(text: str) -> str:
    """Read a line from the user without blocking the event loop.

    input() would stall the loop, so the client could not answer the
    server's keepalive pings while a prompt sits unanswered.
    """
    return (await asyncio.to_thread(input, text)).strip()


async def ask(question: str) -> str:
    """Prompt the user and return their input (lowercase, stripped)."""
    return (await prompt(f"\n  {question} [y/n/skip]: ")).lower()


async def confirm(question: str) -> bool | None:
    """Ask user to confirm. Returns True, False, or None (skip)."""
    ans = await ask(question)
    if ans.startswith("s"):
        return None
    return ans.startswith("y")


async def pick_device(devices: list, category: str) -> object | None:
    """Let the user pick a device from a list, or skip."""
    if not devices:
        print(f"\n  No {category} devices found, skipping.")
        return None
    if len(devices) == 1:
        d = devices[0]
        print(f"\n  Found 1 {category}: [{d.id}] {d.name}")
        ans = await ask(f"Use this device for {category} tests?")
        if ans.startswith("s") or ans.startswith("n"):
            return None
        return d
    print(f"\n  Found {len(devices)} {category} devices:")
    for i, d in enumerate(devices):
        print(
            f"    {i + 1}. [{d.id}] {d.name} (type {d.device_type}, value={d.current_value})"
        )
    while True:
        choice = await prompt(f"  Pick a number (1-{len(devices)}) or 's' to skip: ")
        if choice.lower() == "s":
            return None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(devices):
                return devices[idx]
        except ValueError:
            pass
        print("  Invalid choice, try again.")


async def wait_for_update(
    client: AVEDominaClient, device_id: str, timeout: float = 5.0
):
    """Wait for a status update for a specific device."""
    event = asyncio.Event()
    received = {}

    def _on_update(event_type, data):
        if data.get("device_id") == device_id:
            received["event_type"] = event_type
            received["data"] = data
            event.set()

    unsub = client.register_update_callback(_on_update)
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    unsub()
    return received


# -- Test sequences -----------------------------------------------------------


async def test_light(client: AVEDominaClient, device):
    """Test light on/off."""
    print(f"\n{'='*60}")
    print(f"  LIGHT TEST: [{device.id}] {device.name}")
    print(f"  Current state: {'ON' if device.is_on else 'OFF'}")
    print(f"{'='*60}")

    passed = 0
    failed = 0
    skipped = 0

    # Turn ON
    print("\n  >> Turning light ON...")
    await client.turn_on_light(device.id)
    await wait_for_update(client, device.id)
    print_device_state(device)
    result = await confirm("Is the light ON?")
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Light did not turn on")

    # Turn OFF
    print("\n  >> Turning light OFF...")
    await client.turn_off_light(device.id)
    await wait_for_update(client, device.id)
    print_device_state(device)
    result = await confirm("Is the light OFF?")
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Light did not turn off")

    # Toggle
    print("\n  >> Toggling light...")
    await client.toggle_light(device.id)
    await wait_for_update(client, device.id)
    print_device_state(device)
    result = await confirm("Did the light toggle?")
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Light did not toggle")

    # Toggle back
    print("\n  >> Toggling light back...")
    await client.toggle_light(device.id)
    await wait_for_update(client, device.id)
    print_device_state(device)

    return passed, failed, skipped


async def test_dimmer(client: AVEDominaClient, device):
    """Test dimmer on/off and brightness levels."""
    print(f"\n{'='*60}")
    print(f"  DIMMER TEST: [{device.id}] {device.name}")
    print(f"  Current state: value={device.current_value}")
    print(f"{'='*60}")

    passed = 0
    failed = 0
    skipped = 0

    # Turn ON
    print("\n  >> Turning dimmer ON...")
    await client.turn_on_dimmer(device.id)
    await wait_for_update(client, device.id)
    print_dimmer_state(device)
    result = await confirm("Is the dimmer ON?")
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Dimmer did not turn on")

    # Set brightness to 31 (max)
    print("\n  >> Setting brightness to 31 (max)...")
    await client.set_dimmer_level(device.id, 31)
    await wait_for_update(client, device.id)
    print_dimmer_state(device)
    result = await confirm("Is brightness at maximum?")
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Brightness not at max")

    # Set brightness to 15 (mid)
    print("\n  >> Setting brightness to 15 (mid)...")
    await client.set_dimmer_level(device.id, 15)
    await wait_for_update(client, device.id)
    print_dimmer_state(device)
    result = await confirm("Is brightness at about half?")
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Brightness not at mid level")

    # Set brightness to 1 (min)
    print("\n  >> Setting brightness to 1 (minimum)...")
    await client.set_dimmer_level(device.id, 1)
    await wait_for_update(client, device.id)
    print_dimmer_state(device)
    result = await confirm("Is brightness at minimum?")
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Brightness not at minimum")

    # Turn OFF
    print("\n  >> Turning dimmer OFF...")
    await client.turn_off_dimmer(device.id)
    await wait_for_update(client, device.id)
    print_dimmer_state(device)
    result = await confirm("Is the dimmer OFF?")
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Dimmer did not turn off")

    return passed, failed, skipped


async def test_shutter(client: AVEDominaClient, device):
    """Test shutter open/close."""
    print(f"\n{'='*60}")
    print(f"  SHUTTER TEST: [{device.id}] {device.name}")
    print(f"  Current state: value={device.current_value}")
    print(f"{'='*60}")

    passed = 0
    failed = 0
    skipped = 0

    # Open
    print("\n  >> Opening shutter...")
    await client.open_shutter(device.id)
    await wait_for_update(client, device.id)
    print_shutter_state(device)
    result = await confirm("Is the shutter opening/open?")
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Shutter did not open")

    await asyncio.sleep(2)

    # Stop while opening
    print("\n  >> Opening shutter (to test stop)...")
    await client.open_shutter(device.id)
    await wait_for_update(client, device.id)
    await asyncio.sleep(1)
    print("  >> Stopping shutter...")
    await client.stop_shutter(device.id)
    await wait_for_update(client, device.id)
    print_shutter_state(device)
    result = await confirm("Did the shutter stop?")
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Shutter did not stop")

    await asyncio.sleep(2)

    # Close
    print("\n  >> Closing shutter...")
    await client.close_shutter(device.id)
    await wait_for_update(client, device.id)
    print_shutter_state(device)
    result = await confirm("Is the shutter closing/closed?")
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Shutter did not close")

    return passed, failed, skipped


async def test_thermostat(client: AVEDominaClient, device):
    """Test thermostat on/off, season, setpoint, mode."""
    thermo = client.thermostats.get(device.id)
    if not thermo:
        print(f"\n  No thermostat data for device {device.id}, skipping.")
        return 0, 0, 1

    print(f"\n{'='*60}")
    print(f"  THERMOSTAT TEST: [{device.id}] {device.name}")
    print(f"  Temperature: {thermo.temperature}°C")
    print(f"  Set point:   {thermo.set_point}°C")
    print(f"  Season:      {'winter' if thermo.is_heating else 'summer'}")
    print(f"  Mode:        {'auto' if thermo.is_auto_mode else 'manual'}")
    print(f"  Local off:   {thermo.local_off} ({'OFF' if thermo.is_off else 'ON'})")
    if thermo.humidity_enabled:
        print(f"  Humidity:    {thermo.humidity_value}%")
    print(f"{'='*60}")

    passed = 0
    failed = 0
    skipped = 0

    original_season = thermo.season
    original_local_off = thermo.local_off

    # Turn OFF
    if not thermo.is_off:
        print("\n  >> Turning thermostat OFF...")
        await client.turn_off_thermostat(device.id)
        await wait_for_update(client, device.id)
        print_thermo_state(thermo)
        result = await confirm("Is the thermostat OFF?")
        if result is None:
            skipped += 1
        elif result:
            passed += 1
        else:
            failed += 1
            print("  !! FAIL: Thermostat did not turn off")
    else:
        print("\n  Thermostat is already OFF, skipping turn-off test.")

    # Turn ON
    print("\n  >> Turning thermostat ON...")
    await client.turn_on_thermostat(device.id)
    await wait_for_update(client, device.id)
    print_thermo_state(thermo)
    result = await confirm("Is the thermostat ON?")
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Thermostat did not turn on")

    # Change set point
    # Refresh thermostat state before capturing baseline
    await client.send_command("WTS", [device.id], [[""]])
    await wait_for_update(client, device.id)
    set_point_before = thermo.set_point
    new_sp = set_point_before + 1.0
    if new_sp > 35.0:
        new_sp = set_point_before - 1.0
    print(f"\n  >> Changing set point from {set_point_before}°C to {new_sp}°C...")
    await client.set_thermostat_set_point(device.id, new_sp)
    await wait_for_update(client, device.id)
    await client.send_command("WTS", [device.id], [[""]])
    await wait_for_update(client, device.id)
    print_thermo_state(thermo)
    result = await confirm(
        f"Did the set point change? (set_point={thermo.set_point}°C)"
    )
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Set point did not change")
    print_thermo_state(thermo)
    # Restore set point
    print(f"\n  >> Restoring set point to {set_point_before}°C...")
    await client.set_thermostat_set_point(device.id, set_point_before)
    await wait_for_update(client, device.id)
    await client.send_command("WTS", [device.id], [[""]])
    await wait_for_update(client, device.id)
    print_thermo_state(thermo)

    # Switch season
    new_season = 0 if original_season == 1 else 1
    season_name = "summer" if new_season == 0 else "winter"
    print(f"\n  >> Switching season to {season_name}...")
    await client.set_thermostat_season(device.id, new_season)
    await wait_for_update(client, device.id)
    await client.send_command("WTS", [device.id], [[""]])
    await wait_for_update(client, device.id)
    print_thermo_state(thermo)
    result = await confirm(
        f"Did the season change to {season_name}? (season={thermo.season})"
    )
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Season did not change")

    # Restore season
    original_season_name = "summer" if original_season == 0 else "winter"
    print(f"\n  >> Restoring season to {original_season_name}...")
    await client.set_thermostat_season(device.id, original_season)
    await wait_for_update(client, device.id)
    await client.send_command("WTS", [device.id], [[""]])
    await wait_for_update(client, device.id)
    print_thermo_state(thermo)

    # Switch mode to auto
    print("\n  >> Switching mode to auto (schedule)...")
    await client.set_thermostat_mode(device.id, 0)
    await wait_for_update(client, device.id)
    # Force re-read: server doesn't always send TM updates after STS
    await client.send_command("WTS", [device.id], [[""]])
    await wait_for_update(client, device.id)
    print_thermo_state(thermo)
    result = await confirm(
        f"Is the thermostat in auto/schedule mode? (mode={thermo.mode})"
    )
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Mode did not switch to auto")

    # Switch mode to manual
    print("\n  >> Switching mode to manual...")
    await client.set_thermostat_mode(device.id, 1)
    await wait_for_update(client, device.id)
    # Force re-read: server doesn't always send TM updates after STS
    await client.send_command("WTS", [device.id], [[""]])
    await wait_for_update(client, device.id)
    print_thermo_state(thermo)
    result = await confirm(f"Is the thermostat in manual mode? (mode={thermo.mode})")
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Mode did not switch to manual")

    # Restore original local_off state
    if original_local_off != thermo.local_off:
        if original_local_off == 1:
            print("\n  >> Restoring thermostat to OFF...")
            await client.turn_off_thermostat(device.id)
        else:
            print("\n  >> Restoring thermostat to ON...")
            await client.turn_on_thermostat(device.id)
        await wait_for_update(client, device.id)

    return passed, failed, skipped


async def test_scenario(client: AVEDominaClient, device):
    """Test scenario activation."""
    print(f"\n{'='*60}")
    print(f"  SCENARIO TEST: [{device.id}] {device.name}")
    print(f"{'='*60}")

    passed = 0
    failed = 0
    skipped = 0

    print("\n  >> Activating scenario...")
    await client.activate_scenario(device.id)
    await asyncio.sleep(1)
    result = await confirm("Did the scenario activate? (check physical devices)")
    if result is None:
        skipped += 1
    elif result:
        passed += 1
    else:
        failed += 1
        print("  !! FAIL: Scenario did not activate")

    return passed, failed, skipped


# -- Main ---------------------------------------------------------------------


async def main(host: str, port: int):
    print("\n  AVE DominaPlus Hardware Test")
    print(f"  Connecting to {host}:{port}...")

    client = AVEDominaClient(host=host, port=port)
    await client.connect()

    try:
        print("  Initializing (loading devices, areas, statuses)...")
        await client.initialize()
        ok = await client.wait_for_initialization(timeout=60.0)
        if not ok:
            print("  ERROR: Initialization timed out!")
            return

        # Categorize devices
        lights = [d for d in client.devices.values() if d.is_light]
        dimmers = [d for d in client.devices.values() if d.is_dimmer]
        shutters = [d for d in client.devices.values() if d.is_shutter]
        thermostats = [d for d in client.devices.values() if d.is_thermostat]
        scenarios = [d for d in client.devices.values() if d.is_scenario]

        print(f"\n  Discovered {len(client.devices)} devices:")
        print(f"    Lights:      {len(lights)}")
        print(f"    Dimmers:     {len(dimmers)}")
        print(f"    Shutters:    {len(shutters)}")
        print(f"    Thermostats: {len(thermostats)}")
        print(f"    Scenarios:   {len(scenarios)}")

        # List all devices
        print("\n  All devices:")
        for d in client.devices.values():
            type_name = DEVICE_TYPE_NAMES.get(
                d.device_type, f"Unknown({d.device_type})"
            )
            print(f"    [{d.id}] {d.name} - {type_name} (value={d.current_value})")

        # Run tests per category
        total_passed = 0
        total_failed = 0
        total_skipped = 0

        test_plan = [
            ("Light", lights, test_light),
            ("Dimmer", dimmers, test_dimmer),
            ("Shutter", shutters, test_shutter),
            ("Thermostat", thermostats, test_thermostat),
            ("Scenario", scenarios, test_scenario),
        ]

        for category, devices, test_fn in test_plan:
            device = await pick_device(devices, category)
            if device is None:
                continue
            p, f, s = await test_fn(client, device)
            total_passed += p
            total_failed += f
            total_skipped += s

        # Summary
        print(f"\n{'='*60}")
        print("  RESULTS")
        print(f"{'='*60}")
        print(f"    Passed:  {total_passed}")
        print(f"    Failed:  {total_failed}")
        print(f"    Skipped: {total_skipped}")
        print(f"{'='*60}")

        if total_failed == 0:
            print("  All tests passed!")
        else:
            print(f"  {total_failed} test(s) FAILED")

    finally:
        await client.disconnect()
        print("\n  Disconnected.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <host> [port]")
        sys.exit(1)

    host = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 14001

    asyncio.run(main(host, port))
