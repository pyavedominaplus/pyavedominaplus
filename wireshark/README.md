# AVE DominaPlus Wireshark Dissector

A Wireshark Lua dissector for the AVE DominaPlus home automation protocol. It decodes the binary-framed messages exchanged between clients and the AVE Domina Plus server over WebSocket (port 14001).

## Installation

Copy `ave_dominaplus.lua` to your Wireshark plugins directory, or load it manually:

```
wireshark -X lua_script:ave_dominaplus.lua
```

The personal plugins directory is typically:

| OS      | Path                                              |
| ------- | ------------------------------------------------- |
| Linux   | `~/.local/lib/wireshark/plugins/`                 |
| macOS   | `~/.local/lib/wireshark/plugins/`                 |
| Windows | `%APPDATA%\Wireshark\plugins\`                    |

## Protocol Framing

Each message follows this structure:

```
STX (0x02)
  command [ GS (0x1D) param1  GS param2 ... ]
          [ RS (0x1E) rec1_field1  GS rec1_field2 ... ]
          [ RS rec2_field1  GS rec2_field2 ... ]
ETX (0x03)
CRC (2 hex characters)
EOT (0x04)
```

- **STX/ETX/EOT** — frame delimiters
- **GS (0x1D)** — separates parameters/fields within a section
- **RS (0x1E)** — separates records
- **CRC** — XOR of all bytes from STX through ETX, subtracted from 0xFF, encoded as two uppercase hex characters

## Dissected Fields

| Filter                | Description              |
| --------------------- | ------------------------ |
| `ave.raw`             | Raw message bytes        |
| `ave.command`         | Command code             |
| `ave.cmd_desc`        | Human-readable command   |
| `ave.params`          | Parameters subtree       |
| `ave.param`           | Individual parameter     |
| `ave.records`         | Records subtree          |
| `ave.record`          | Individual record        |
| `ave.field`           | Record field             |
| `ave.crc`             | CRC value                |
| `ave.crc_valid`       | CRC validation result    |

## Supported Commands

### Client to Server

| Code  | Description            |
| ----- | ---------------------- |
| `LM`  | List Maps (Areas)      |
| `LDI` | List Devices           |
| `LI2` | List Device Addresses  |
| `LMC` | List Map Commands      |
| `LML` | List Map Labels        |
| `WTS` | Get Thermostat Status  |
| `STS` | Set Thermostat Status  |
| `WSF` | Get Device Status Family |
| `SU2` | Subscribe Updates 2    |
| `SU3` | Subscribe Updates 3    |
| `GTM` | Get Thermostat Mode    |
| `SIL` | Set Dimmer Level       |
| `EBI` | Light/Energy Command   |
| `EAI` | Shutter Command        |
| `ES`  | Execute Scenario       |
| `EBC` | Execute Map Command    |
| `TOO` | Thermostat Local Off   |
| `TUU` | Thermostat Local Off (TS01) |
| `TTK` | Thermostat Keyboard Lock |

### Server to Client

| Code  | Description                  |
| ----- | ---------------------------- |
| `lm`  | List Maps Response           |
| `ldi` | List Devices Response        |
| `li2` | Device Addresses Response    |
| `lmc` | Map Commands Response        |
| `lml` | Map Labels Response          |
| `wts` | Thermostat Status Response   |
| `wsf` | Device Status Family Response |
| `gsf` | Sensor Family Response       |
| `upd` | Status Update                |
| `ack` | Acknowledgement              |

## Detection

The dissector registers itself in three ways:

1. **WebSocket sub-protocol** — matches the `"binary"` sub-protocol
2. **Heuristic dissector** — detects AVE frames (STX…ETX…EOT) inside any WebSocket payload
3. **TCP port 14001** — fallback for raw TCP captures without WebSocket framing
