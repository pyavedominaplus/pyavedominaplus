# pyavedominaplus

A Python SDK and Home Assistant custom integration for [AVE DominaPlus](https://www.ave.it/) home automation systems. Communicates with the DominaPlus server over WebSocket using the native binary protocol.

AI Disclaimer: This project was built using the assistance of Claude Code

## Features

- Async WebSocket client with automatic ping/pong keepalive
- Full binary protocol implementation (STX/ETX framing, CRC validation)
- Push-based real-time device status updates
- Home Assistant integration with config flow UI

### Supported devices

| Device type | HA platform | Controls |
|---|---|---|
| Light | `light` | On/off |
| Dimmer | `light` | On/off, brightness (0-254) |
| Shutter | `cover` | Open, close, stop |
| Thermostat | `climate` | Temperature setpoint, season mode, fan |
| Scenario | `switch` | Activate |
| Energy meter | — | Read-only |

## Requirements

- Python >= 3.13
- aiohttp >= 3.9

## Installation

### Python SDK

```bash
pip install -e .

# With dev dependencies
pip install -e ".[dev]"
```

## SDK usage

```python
import asyncio
from pyavedominaplus import AVEDominaClient

async def main():
    client = AVEDominaClient(host="192.168.1.100", port=14001)
    await client.connect()
    await client.initialize()
    await client.wait_for_initialization(timeout=30.0)

    # List discovered devices
    for device_id, device in client.devices.items():
        print(f"{device.name}: {device.device_type}")

    # Control devices
    await client.turn_on_light("100")
    await client.set_dimmer_level("101", 128)
    await client.open_shutter("102")
    await client.set_thermostat_set_point("103", 21.5)

    # Register for real-time updates
    def on_update(event_type, data):
        print(f"Update: {event_type} - {data}")

    client.register_update_callback(on_update)

    await asyncio.sleep(60)
    await client.disconnect()

asyncio.run(main())
```

## Project structure

```
pyavedominaplus/           Python SDK
  client.py                Async WebSocket client
  protocol.py              Message encoding/decoding, CRC
  models.py                DominaDevice, DominaThermostat, DominaArea
  const.py                 Protocol constants and device types

tests/                     SDK unit tests (133 tests)
```

## Running tests

```bash
# All tests
pytest

# With coverage
coverage run -m pytest tests
coverage report

# Specific module
pytest tests/test_client.py -v
```

## Protocol notes

The SDK implements AVE's custom binary WebSocket protocol:

- **Framing:** STX (0x02) marks message start, ETX (0x03) end, EOT (0x04) end of transmission
- **Fields:** Separated by GS (0x1D) and RS (0x1E) control characters
- **CRC:** XOR-based checksum (0xFF minus XOR of all payload bytes)
- **Port:** Default 14001
- **Special devices:** RGBW names prefixed with `$`, DALI names suffixed with `$`, VMC Daikin thermostats have IDs offset by 10000000

## License

See [LICENSE](LICENSE) for details.
