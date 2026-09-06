"""Measure travel times for all shutters on an AVE DominaPlus server.

Drives every shutter through a full close, then times a full open and a
full close using the pushed status transitions. The measured times are
what the ShutterTravelEstimator needs.

Results are merged into a JSON config file (see shutter_config.py), which
is what test_cover_position.py reads. The Home Assistant integration gets
the same numbers from its own options flow instead.

WARNING: this physically moves every shutter on the system.

Usage:
    python measure_covers.py <host> [port] [--device-id ID ...] [--yes]
                             [--config PATH] [--no-config]
                             [--phase-timeout SECONDS] [--verbose]
"""

import argparse
import asyncio
import logging
import statistics
import sys
from pathlib import Path

from shutter_config import DEFAULT_CONFIG_PATH, ConfigError, TravelTimeConfig

from pyavedominaplus import (
    DEFAULT_PHASE_TIMEOUT,
    AVEDominaClient,
    DominaDevice,
    measure_shutter_travel_times,
)


async def main(
    host: str,
    port: int,
    device_ids: list[str],
    assume_yes: bool,
    phase_timeout: float = DEFAULT_PHASE_TIMEOUT,
    config_path: Path | None = DEFAULT_CONFIG_PATH,
) -> int:
    client = AVEDominaClient(host=host, port=port)
    print(f"Connecting to {host}:{port} ...")
    await client.connect()
    try:
        await client.initialize()
        if not await client.wait_for_initialization(timeout=60.0):
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
            # input() blocks the event loop, which would stop the client
            # answering the server's keepalive pings while the prompt sits
            # there; run it on a worker thread instead.
            answer = (
                (await asyncio.to_thread(input, "Continue? [y/N] ")).strip().lower()
            )
            if answer != "y":
                print("Aborted.")
                return 1

        # Track ids, not device objects: a reconnect mid-run re-reads the
        # device list, and the current object is the one to consult.
        results = []
        shutter_ids = [d.id for d in shutters]
        for i, device_id in enumerate(shutter_ids, 1):
            device = client.devices.get(device_id)
            name = device.name if device else device_id
            print(f"\n[{i}/{len(shutter_ids)}] Measuring [{device_id}] {name} ...")
            try:
                m = await measure_shutter_travel_times(
                    client,
                    device_id,
                    phase_timeout=phase_timeout,
                    progress=lambda msg: print(f"  ... {msg}", flush=True),
                )
            except ValueError as err:
                print(f"  SKIPPED - {err}")
                continue
            except TimeoutError:
                print(
                    f"  TIMEOUT after {phase_timeout:.0f}s - skipping"
                    " (is the shutter responding?)"
                )
                continue
            print(f"  open: {m.open_time:6.1f}s   close: {m.close_time:6.1f}s")
            results.append(m)

        if not results:
            print("\nNo successful measurements.")
            return 1

        print("\nResults - enter these per shutter:")
        print(f"  {'ID':>10}  {'Name':<30} {'Open':>7} {'Close':>7}")
        for m in results:
            print(
                f"  {m.device_id:>10}  {m.name:<30}"
                f" {m.open_time:6.1f}s {m.close_time:6.1f}s"
            )
        if len(results) > 1:
            mean_open = statistics.mean(m.open_time for m in results)
            mean_close = statistics.mean(m.close_time for m in results)
            print(
                f"\nFor reference, the mean across all measured shutters is"
                f" open {mean_open:.1f}s, close {mean_close:.1f}s. Shutters of"
                " different sizes travel at different speeds, so prefer the"
                " per-shutter values above."
            )
        skipped = len(shutter_ids) - len(results)
        if skipped:
            print(f"\n{skipped} shutter(s) could not be measured.")

        if config_path is None:
            return 0
        return _save_results(config_path, host, results)
    finally:
        await client.disconnect()


def _save_results(config_path: Path, host: str, results: list) -> int:
    """Merge the measurements into the config file. Returns an exit code."""
    try:
        config = TravelTimeConfig.load(config_path)
    except ConfigError as err:
        print(f"\nERROR: {err}")
        print(
            "Refusing to overwrite it. The measurements above are still"
            " valid - fix or move the file and re-run, or pass --no-config."
        )
        return 1
    if config.host and config.host != host:
        print(
            f"\nNOTE: {config_path} was written for host {config.host};"
            f" its entries are being kept alongside {host}."
        )
    config.host = host
    for m in results:
        config.set_times(m.device_id, m.open_time, m.close_time, m.name)
    try:
        config.save(config_path)
    except ConfigError as err:
        print(f"\nERROR: {err}")
        return 1
    kept = len(config.shutters) - len(results)
    print(f"\nSaved {len(results)} shutter(s) to {config_path}", end="")
    print(
        f" ({kept} existing entr{'y' if kept == 1 else 'ies'} kept)." if kept else "."
    )
    print(f"Drive it to positions with: python scripts/test_cover_position.py {host}")
    return 0


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
    parser.add_argument(
        "--phase-timeout",
        type=float,
        default=DEFAULT_PHASE_TIMEOUT,
        help=(
            "Seconds to wait for each status transition"
            f" (default: {DEFAULT_PHASE_TIMEOUT:.0f})"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Travel time config file to merge into (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="Only print the results, do not touch the config file",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log the protocol traffic, useful when a phase stalls",
    )
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
                    args.yes,
                    args.phase_timeout,
                    None if args.no_config else args.config,
                )
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
