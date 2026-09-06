"""Tests for the CLI travel-time config file."""

import json

import pytest

from shutter_config import (
    CONFIG_VERSION,
    ConfigError,
    ShutterTravelTimes,
    TravelTimeConfig,
)


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "shutter_travel_times.json"


class TestShutterTravelTimes:
    def test_rejects_non_positive_times(self):
        with pytest.raises(ConfigError):
            ShutterTravelTimes(device_id="102", open_time=0, close_time=10)
        with pytest.raises(ConfigError):
            ShutterTravelTimes(device_id="102", open_time=10, close_time=-1)

    def test_rejects_missing_or_bad_fields(self):
        with pytest.raises(ConfigError):
            ShutterTravelTimes.from_json("102", {"open_time": 10})
        with pytest.raises(ConfigError):
            ShutterTravelTimes.from_json("102", {"open_time": "x", "close_time": 10})
        with pytest.raises(ConfigError):
            ShutterTravelTimes.from_json("102", ["not", "an", "object"])

    def test_round_trip(self):
        entry = ShutterTravelTimes(
            device_id="102",
            open_time=12.501,
            close_time=11.75,
            name="Window Blind",
            measured_at="2026-01-01T12:00:00+00:00",
        )
        back = ShutterTravelTimes.from_json("102", entry.to_json())
        assert back.open_time == 12.5  # rounded on write
        assert back.close_time == 11.75
        assert back.name == "Window Blind"
        assert back.measured_at == entry.measured_at


class TestTravelTimeConfig:
    def test_missing_file_loads_empty(self, config_path):
        config = TravelTimeConfig.load(config_path)
        assert config.shutters == {}
        assert config.host == ""

    def test_save_and_load(self, config_path):
        config = TravelTimeConfig(host="192.168.1.100")
        config.set_times("102", 12.5, 11.75, "Window Blind")
        config.save(config_path)

        loaded = TravelTimeConfig.load(config_path)
        assert loaded.host == "192.168.1.100"
        entry = loaded.get("102")
        assert entry is not None
        assert entry.open_time == 12.5
        assert entry.close_time == 11.75
        assert entry.name == "Window Blind"
        assert entry.measured_at  # stamped on write

    def test_saving_one_shutter_keeps_the_others(self, config_path):
        """Re-measuring one shutter must not drop the rest of the file."""
        config = TravelTimeConfig(host="h")
        config.set_times("101", 10.0, 11.0, "one")
        config.set_times("102", 20.0, 21.0, "two")
        config.save(config_path)

        again = TravelTimeConfig.load(config_path)
        again.set_times("102", 25.0, 26.0, "two")
        again.save(config_path)

        loaded = TravelTimeConfig.load(config_path)
        assert set(loaded.shutters) == {"101", "102"}
        assert loaded.get("101").open_time == 10.0
        assert loaded.get("102").open_time == 25.0

    def test_entries_sorted_numerically(self, config_path):
        config = TravelTimeConfig()
        for device_id in ("108", "12", "9"):
            config.set_times(device_id, 1.0, 1.0)
        config.save(config_path)
        raw = json.loads(config_path.read_text())
        assert list(raw["shutters"]) == ["9", "12", "108"]
        assert raw["version"] == CONFIG_VERSION

    def test_rejects_unsupported_version(self, config_path):
        config_path.write_text(json.dumps({"version": 99, "shutters": {}}))
        with pytest.raises(ConfigError, match="unsupported config version"):
            TravelTimeConfig.load(config_path)

    def test_rejects_malformed_json(self, config_path):
        config_path.write_text("{not json")
        with pytest.raises(ConfigError, match="Cannot read"):
            TravelTimeConfig.load(config_path)

    def test_rejects_non_object_top_level(self, config_path):
        config_path.write_text("[1, 2, 3]")
        with pytest.raises(ConfigError, match="expected a JSON object"):
            TravelTimeConfig.load(config_path)

    def test_rejects_non_object_shutters(self, config_path):
        config_path.write_text(json.dumps({"version": 1, "shutters": []}))
        with pytest.raises(ConfigError, match="'shutters' must be an object"):
            TravelTimeConfig.load(config_path)

    def test_creates_parent_directories(self, tmp_path):
        nested = tmp_path / "a" / "b" / "times.json"
        config = TravelTimeConfig()
        config.set_times("102", 1.0, 2.0)
        config.save(nested)
        assert TravelTimeConfig.load(nested).get("102").close_time == 2.0
