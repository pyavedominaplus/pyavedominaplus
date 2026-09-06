"""Travel-time configuration for the command line scripts.

The Home Assistant integration reads shutter travel times from its own
options flow. The CLI scripts have no such store, so they keep them in a
small JSON file instead: measure_covers.py writes it, and
test_cover_position.py reads it back.

Format (version 1)::

    {
      "version": 1,
      "host": "192.168.1.100",
      "shutters": {
        "102": {
          "name": "Window Blind",
          "open_time": 12.5,
          "close_time": 11.75,
          "measured_at": "2026-01-01T12:00:00+00:00"
        }
      }
    }

Writes merge into an existing file, so measuring one shutter never drops
the entries for the others.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("shutter_travel_times.json")
CONFIG_VERSION = 1


class ConfigError(Exception):
    """Raised when a travel time config file cannot be read or written."""


def utc_now() -> str:
    """Return the current UTC time as a second-resolution ISO 8601 string."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _sort_key(device_id: str) -> tuple[int, int, str]:
    """Sort numeric device ids numerically, anything else alphabetically."""
    if device_id.isdigit():
        return (0, int(device_id), "")
    return (1, 0, device_id)


@dataclass
class ShutterTravelTimes:
    """Full-travel times for one shutter, in seconds."""

    device_id: str
    open_time: float
    close_time: float
    name: str = ""
    measured_at: str = ""

    def __post_init__(self) -> None:
        if self.open_time <= 0 or self.close_time <= 0:
            raise ConfigError(
                f"Shutter {self.device_id}: open_time and close_time must be"
                f" positive (got {self.open_time}, {self.close_time})"
            )

    @classmethod
    def from_json(cls, device_id: str, raw: Any) -> ShutterTravelTimes:
        """Build an entry from its JSON representation."""
        if not isinstance(raw, dict):
            raise ConfigError(f"Shutter {device_id}: expected an object")
        try:
            open_time = float(raw["open_time"])
            close_time = float(raw["close_time"])
        except (KeyError, TypeError, ValueError) as err:
            raise ConfigError(
                f"Shutter {device_id}: open_time/close_time missing or not a number"
            ) from err
        return cls(
            device_id=device_id,
            open_time=open_time,
            close_time=close_time,
            name=str(raw.get("name", "")),
            measured_at=str(raw.get("measured_at", "")),
        )

    def to_json(self) -> dict[str, Any]:
        """Return the JSON representation of this entry."""
        return {
            "name": self.name,
            "open_time": round(self.open_time, 2),
            "close_time": round(self.close_time, 2),
            "measured_at": self.measured_at,
        }


@dataclass
class TravelTimeConfig:
    """The travel times for every shutter on one system."""

    host: str = ""
    shutters: dict[str, ShutterTravelTimes] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str) -> TravelTimeConfig:
        """Load a config file, or return an empty config if it does not exist."""
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise ConfigError(f"Cannot read {path}: {err}") from err
        if not isinstance(raw, dict):
            raise ConfigError(f"{path}: expected a JSON object at the top level")
        version = raw.get("version", CONFIG_VERSION)
        if version != CONFIG_VERSION:
            raise ConfigError(
                f"{path}: unsupported config version {version!r}"
                f" (this build writes version {CONFIG_VERSION})"
            )
        shutters_raw = raw.get("shutters", {})
        if not isinstance(shutters_raw, dict):
            raise ConfigError(f"{path}: 'shutters' must be an object")
        return cls(
            host=str(raw.get("host", "")),
            shutters={
                device_id: ShutterTravelTimes.from_json(device_id, entry)
                for device_id, entry in shutters_raw.items()
            },
        )

    def save(self, path: Path | str) -> None:
        """Write the config, replacing whatever is at path."""
        path = Path(path)
        payload = {
            "version": CONFIG_VERSION,
            "host": self.host,
            "shutters": {
                device_id: self.shutters[device_id].to_json()
                for device_id in sorted(self.shutters, key=_sort_key)
            },
        }
        try:
            if path.parent != Path(""):
                path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except OSError as err:
            raise ConfigError(f"Cannot write {path}: {err}") from err

    def get(self, device_id: str) -> ShutterTravelTimes | None:
        """Return the entry for a device, if the config has one."""
        return self.shutters.get(device_id)

    def set_times(
        self,
        device_id: str,
        open_time: float,
        close_time: float,
        name: str = "",
    ) -> ShutterTravelTimes:
        """Add or replace one shutter's travel times, stamped with the time."""
        entry = ShutterTravelTimes(
            device_id=device_id,
            open_time=open_time,
            close_time=close_time,
            name=name,
            measured_at=utc_now(),
        )
        self.shutters[device_id] = entry
        return entry
