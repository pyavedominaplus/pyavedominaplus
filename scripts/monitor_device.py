"""Live device monitor for AVE DominaPlus.

Connects to hardware, lets you pick a device, then shows its state
with live updates from the WebSocket.

Usage:
    python monitor_device.py <host> [port] [--device-id ID]
"""

import argparse
import asyncio
import os

from pyavedominaplus import AVEDominaClient

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


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


async def pick_device(devices: list) -> object | None:
    """Let the user pick a device from a list.

    Reads on a worker thread: a blocking input() would stall the event
    loop and stop the client answering the server's keepalive pings.
    """
    if not devices:
        print("  No devices found.")
        return None

    print(f"\n  Found {len(devices)} devices:\n")
    for i, d in enumerate(devices):
        type_name = DEVICE_TYPE_NAMES.get(d.device_type, f"Unknown({d.device_type})")
        print(
            f"    {i + 1:3}. [{d.id}] {d.name} ({type_name}, value={d.current_value})"
        )

    while True:
        choice = (
            await asyncio.to_thread(
                input, f"\n  Pick a device (1-{len(devices)}) or 'q' to quit: "
            )
        ).strip()
        if choice.lower() == "q":
            return None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(devices):
                return devices[idx]
        except ValueError:
            pass
        print("  Invalid choice, try again.")


def format_device_state(device, thermo=None) -> str:
    """Build a multi-line state display for a device."""
    type_name = DEVICE_TYPE_NAMES.get(
        device.device_type, f"Unknown({device.device_type})"
    )
    lines = []
    lines.append(f"  Device:    [{device.id}] {device.name}")
    lines.append(f"  Type:      {type_name}")
    lines.append(f"  Raw value: {device.current_value}")

    if device.is_light:
        lines.append(f"  On:        {device.is_on}")

    elif device.is_dimmer:
        lines.append(f"  On:        {device.is_on}")
        lines.append(f"  Level:     {device.brightness}/31")

    elif device.is_shutter:
        if device.is_open:
            state = "Open"
        elif device.is_opening:
            state = "Opening"
        elif device.is_closed:
            state = "Closed"
        elif device.is_closing:
            state = "Closing"
        elif device.is_stopped:
            state = "Stopped"
        else:
            state = f"Unknown ({device.current_value})"
        lines.append(f"  State:     {state}")

    elif device.is_thermostat and thermo:
        lines.append(f"  Temp:      {thermo.temperature}°C")
        lines.append(f"  Setpoint:  {thermo.set_point}°C")
        lines.append(f"  Season:    {'Winter' if thermo.is_heating else 'Summer'}")
        lines.append(f"  Mode:      {'Auto' if thermo.is_auto_mode else 'Manual'}")
        lines.append(f"  On/Off:    {'OFF' if thermo.is_off else 'ON'}")
        lines.append(f"  Fan:       {thermo.fan_level}")
        lines.append(f"  Offset:    {thermo.offset}°C")
        if thermo.humidity_enabled:
            lines.append(f"  Humidity:  {thermo.humidity_value}%")
        if thermo.keyboard_lock:
            lines.append("  Keylock:   Yes")
        if thermo.window_visibility:
            lines.append(f"  Window:    {thermo.window_state}")

    return "\n".join(lines)


def render(device, thermo, update_count, last_event):
    """Clear screen and print current state."""
    clear_screen()
    print()
    print("  " + "=" * 50)
    print("  AVE DominaPlus — Live Device Monitor")
    print("  " + "=" * 50)
    print()
    print(format_device_state(device, thermo))
    print()
    print(f"  Updates received: {update_count}")
    if last_event:
        print(f"  Last event:       {last_event}")
    print()
    print("  Press Ctrl+C to quit.")


async def monitor(client: AVEDominaClient, device):
    """Subscribe to updates and re-render on changes."""
    thermo = client.thermostats.get(device.id) if device.is_thermostat else None
    update_count = 0
    last_event = ""
    conn_status = "OPEN"

    render(device, thermo, update_count, last_event)

    event = asyncio.Event()

    def _on_update(event_type, data):
        nonlocal update_count, last_event
        if data.get("device_id") == device.id:
            update_count += 1
            last_event = event_type
            event.set()

    def _on_connection(status):
        nonlocal conn_status
        conn_status = status
        event.set()

    unsub = client.register_update_callback(_on_update)
    unsub_conn = client.register_connection_callback(_on_connection)
    try:
        while True:
            event.clear()
            try:
                await asyncio.wait_for(event.wait(), timeout=30.0)
            except TimeoutError:
                pass
            if device.is_thermostat:
                thermo = client.thermostats.get(device.id)
            render(device, thermo, update_count, last_event)
            if conn_status == "ERROR":
                print("  Connection lost — reconnecting...")
            elif conn_status == "OPEN" and last_event == "":
                pass  # Initial state, no extra message needed
    except asyncio.CancelledError:
        pass
    finally:
        unsub()
        unsub_conn()


async def main(host: str, port: int, device_id: str | None = None):
    print(f"\n  Connecting to {host}:{port}...")

    client = AVEDominaClient(host=host, port=port)
    await client.connect()

    try:
        print("  Initializing (loading devices, areas, statuses)...")
        await client.initialize()
        ok = await client.wait_for_initialization(timeout=60.0)
        if not ok:
            print("  ERROR: Initialization timed out!")
            return

        if device_id:
            device = client.devices.get(device_id)
            if device is None:
                print(f"  ERROR: Device '{device_id}' not found.")
                return
        else:
            all_devices = sorted(
                client.devices.values(), key=lambda d: (d.device_type, d.name)
            )
            device = await pick_device(all_devices)
            if device is None:
                return

        await monitor(client, device)

    finally:
        await client.disconnect()
        print("\n  Disconnected.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AVE DominaPlus live device monitor")
    parser.add_argument("host", help="DominaPlus server hostname or IP")
    parser.add_argument(
        "port",
        nargs="?",
        type=int,
        default=14001,
        help="WebSocket port (default: 14001)",
    )
    parser.add_argument(
        "--device-id", help="Device ID to monitor (skip interactive picker)"
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(args.host, args.port, args.device_id))
    except KeyboardInterrupt:
        print("\n  Bye.")
