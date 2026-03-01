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
| Light (type 1, 22) | `light` | On/off, toggle |
| Dimmer (type 2) | `light` | On/off, toggle, brightness (0-31) |
| Shutter (type 3, 16, 19) | `cover` | Open, close |
| Thermostat (type 4) | `climate` | Temperature setpoint, season mode, local off |
| Scenario (type 6) | `switch` | Activate |
| Energy meter (type 9) | — | Read-only |

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

    # Control lights (EBI command)
    await client.turn_on_light("100")
    await client.turn_off_light("100")
    await client.toggle_light("100")

    # Control dimmers (SIL command for level, EBI for step)
    await client.set_dimmer_level("101", 16)   # 0-31
    await client.step_dimmer("101")             # toggle on/off

    # Control shutters (EAI command)
    await client.open_shutter("102")
    await client.close_shutter("102")

    # Control thermostats (STS command)
    await client.set_thermostat_set_point("103", 21.5)
    await client.set_thermostat_season("103", season=1)   # 0=summer, 1=winter
    await client.toggle_thermostat_local_off("103")        # toggle on/off
    await client.toggle_thermostat_keyboard_lock("103")

    # Activate scenario (ES command via map lookup)
    await client.activate_scenario("104")

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

tests/                     SDK unit tests (158 tests)
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
- **Fields:** Separated by GS (0x1D) within a section, RS (0x1E) between sections (records)
- **CRC:** XOR-based checksum (0xFF minus XOR of all payload bytes)
- **Port:** Default 14001
- **Special devices:** RGBW names prefixed with `$`, DALI names suffixed with `$`, VMC Daikin thermostats have IDs offset by 10000000

### Command reference

| Command | Direction | Purpose |
|---|---|---|
| `LM` | → | List areas/maps |
| `LMC` | → | List map commands for an area |
| `LML` | → | List map labels for an area |
| `LDI` | → | List all devices |
| `LI2` | → | List device AVEbus addresses |
| `WSF <family>` | → | Request device statuses for a family (1=light, 2=dimmer, 3=shutter…) |
| `SU2` / `SU3` | → | Subscribe to real-time status updates |
| `WTS <id>` | → | Request thermostat full status |
| `GTM` | → | Request thermostat IR mode list |
| `GMA` | → | Request start/stop device list |
| `GNA` | → | Request no-action device list |
| `GSF <family>` | → | Request sensor family status |
| `EBI <id>,<cmd>` | → | Light/energy command: `10`=toggle, `11`=on, `12`=off, `2`=dimmer step |
| `EAI <id>,<cmd>` | → | Shutter command: `8`=open, `9`=close |
| `SIL <id>,<level>` | → | Set dimmer brightness (0-31) |
| `STS <id>` + record | → | Set thermostat (season, mode, setpoint×10) |
| `ES <cmdId>` | → | Execute scenario (map command ID) |
| `TOO <id>,<state>` | → | Toggle thermostat local off (standard) |
| `TUU <id>,<state>` | → | Toggle thermostat local off (TS01 type) |
| `TTK <id>` | → | Toggle thermostat keyboard lock |
| `PONG` | → | Reply to server ping |
| `upd WS <type> <id> <val>` | ← | Device status update |
| `upd WT <sub> <id> <val>` | ← | Thermostat sub-update (T=temp, S=season, O=offset, L=fan, Z=localOFF) |
| `upd TP <id> <val>` | ← | Thermostat setpoint update |
| `upd TM <id> <mode>` | ← | Thermostat mode update |
| `upd TK <id> <lock>` | ← | Thermostat keyboard lock update |
| `upd TW <id> <state>` | ← | Thermostat window state update |
| `upd UMI <id> …` | ← | Humidity probe update |
| `upd D <cmdId> <icon>` | ← | Map command icon update |
| `upd GRP …` | ← | Group dimmer update |
| `upd RGB …` | ← | RGBW update |
| `upd epv …` | ← | Economizer update |
| `wts <id>` + record | ← | Full thermostat status response |
| `lm` + records | ← | Area list response |
| `ldi` + records | ← | Device list response |
| `lmc <areaId>` + records | ← | Map commands response |
| `ack` | ← | Command acknowledgement |
| `ping` | ← | Keepalive ping |

## License

See [LICENSE](LICENSE) for details.
