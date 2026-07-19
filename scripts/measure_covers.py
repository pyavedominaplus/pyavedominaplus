"""Measure travel times for all shutters on an AVE DominaPlus server.

Drives every shutter through a full close, then times a full open and a
full close using the pushed status transitions. The measured times are
what the ShutterTravelEstimator (and the Home Assistant integration's
"Shutter travel times" options) need.

WARNING: this physically moves every shutter on the system.

Usage:
    python measure_covers.py <host> [port] [--device-id ID ...] [--yes]
"""

import argparse
import asyncio
import statistics
import sys

from pyavedominaplus import (
    AVEDominaClient,
    DominaDevice,
    measure_shutter_travel_times,
)


async def main(host: str, port: int, device_ids: list[str], assume_yes: bool) -> int:
    client = AVEDominaClient(host=host, port=port)
    print(f"Connecting to {host}:{port} ...")
    await client.connect()
    try:
        await client.initialize()
        if not await client.wait_for_initialization(timeout=30.0):
            print("ERROR: initialization timed out")
            return 1

        shutters: list[DominaDevice] = [
            d for d in client.devices.values() if d.is_shutter
        ]
        if device_ids:
            shutters = [d for d in shutters if d.id in device_ids]
            missing = set(device_ids) - {d.id for d in shutters}
            if missing:
                print(f"ERROR: not a shutter or unknown: {', '.join(sorted(missing))}")
                return 1
        if not shutters:
            print("No shutters found on this system.")
            return 1

        print(f"\nFound {len(shutters)} shutter(s):")
        for d in shutters:
            print(f"  [{d.id}] {d.name}")
        print(
            "\nEach shutter will be fully closed, fully opened, and fully"
            " closed again."
        )
        if not assume_yes:
            answer = input("Continue? [y/N] ").strip().lower()
            if answer != "y":
                print("Aborted.")
                return 1

        results = []
        for i, device in enumerate(shutters, 1):
            print(f"\n[{i}/{len(shutters)}] Measuring [{device.id}] {device.name} ...")
            try:
                m = await measure_shutter_travel_times(client, device.id)
            except TimeoutError:
                print("  TIMEOUT - skipping (is the shutter responding?)")
                continue
            print(f"  open: {m.open_time:6.1f}s   close: {m.close_time:6.1f}s")
            results.append(m)

        if not results:
            print("\nNo successful measurements.")
            return 1

        print("\nResults:")
        print(f"  {'ID':>10}  {'Name':<30} {'Open':>7} {'Close':>7}")
        for m in results:
            print(
                f"  {m.device_id:>10}  {m.name:<30}"
                f" {m.open_time:6.1f}s {m.close_time:6.1f}s"
            )
        mean_open = statistics.mean(m.open_time for m in results)
        mean_close = statistics.mean(m.close_time for m in results)
        print(
            f"\nAverage: open {mean_open:.1f}s, close {mean_close:.1f}s\n"
            "Enter these as 'Shutter opening/closing time' in the Home"
            " Assistant integration options\n(Settings > Devices & services"
            " > AVE DominaPlus > Configure)."
        )
        return 0
    finally:
        await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Measure AVE DominaPlus shutter travel times"
    )
    parser.add_argument("host", help="Server IP address")
    parser.add_argument("port", nargs="?", type=int, default=14001, help="Port")
    parser.add_argument(
        "--device-id",
        action="append",
        default=[],
        help="Only measure this shutter (repeatable)",
    )
    parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt"
    )
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(main(args.host, args.port, args.device_id, args.yes)))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
