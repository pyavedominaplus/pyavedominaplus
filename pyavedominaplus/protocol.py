"""AVE DominaPlus WebSocket protocol encoding/decoding."""

from typing import Any

from .const import STX, ETX, EOT, GS, RS


def build_crc(data: str) -> str:
    """Calculate the CRC for a DominaPlus message.

    XOR all bytes, subtract from 0xFF, return as 2-char hex string.
    """
    crc = 0
    for ch in data:
        crc ^= ord(ch)
    crc = 0xFF - crc
    return f"{crc:02X}"


def encode_message(
    command: str,
    parameters: list[str] | None = None,
    records: list[list[str]] | None = None,
) -> bytes:
    """Encode a command into a DominaPlus protocol message.

    Message format:
        STX + command [+ GS + param1 + GS + param2 ...]
        [+ RS + rec1_field1 + GS + rec1_field2 ...]
        + ETX + CRC + EOT
    """
    msg = chr(STX) + command

    if parameters:
        msg += chr(GS) + chr(GS).join(parameters)

    if records:
        for record in records:
            msg += chr(RS) + chr(GS).join(record)

    msg += chr(ETX)
    crc = build_crc(msg)
    msg += crc + chr(EOT)
    return msg.encode("utf-8")


def decode_message(raw: bytes) -> list[dict[str, Any]]:
    """Decode one or more DominaPlus messages from raw bytes.

    Returns a list of dicts, each with keys:
        command: str
        parameters: list[str]
        records: list[list[str]]
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = "".join(chr(b) for b in raw)

    messages = []
    # Split on EOT to handle multiple messages in one frame
    parts = text.split(chr(EOT))

    for part in parts:
        if not part or len(part) < 3:
            continue

        # Strip STX at start, then ETX + CRC at end
        # Format: STX + payload + ETX + CRC(2 chars)
        if ord(part[0]) == STX:
            part = part[1:]

        # Find ETX position - CRC is the last 2 chars after ETX
        etx_pos = part.find(chr(ETX))
        if etx_pos >= 0:
            part = part[:etx_pos]

        # Split records (RS separator)
        pieces = part.split(chr(RS))

        # First piece contains command + parameters (GS separated)
        fields = pieces[0].split(chr(GS))
        command = fields[0] if fields else ""
        parameters = fields[1:] if len(fields) > 1 else []

        # Remaining pieces are records
        records = []
        for i in range(1, len(pieces)):
            record_fields = pieces[i].split(chr(GS))
            records.append(record_fields)

        messages.append(
            {
                "command": command,
                "parameters": parameters,
                "records": records,
            }
        )

    return messages


def encode_light_command(device_id: str, sub_command: str) -> bytes:
    """Encode a light/energy on/off/toggle command (EBI).

    sub_command values for lights:
        "10" = toggle on/off
        "11" = turn on
        "12" = turn off
    sub_command values for dimmers:
        "2"  = dimmer step (toggle)
        "3"  = dimmer on
        "4"  = dimmer off
    """
    return encode_message("EBI", [device_id, sub_command])


def encode_shutter_command(device_id: str, sub_command: str) -> bytes:
    """Encode a shutter open/close command (EAI).

    sub_command values:
        "8" = open/raise
        "9" = close/lower
    """
    return encode_message("EAI", [device_id, sub_command])


def encode_set_dimmer_level(device_id: str, level: int) -> bytes:
    """Encode a dimmer level command (SIL = Set Intensity Level).

    The device_id is sent as a parameter (GS = Group Separator delimited)
    and the level is sent as a record (RS = Record Separator delimited),
    matching the format used by the official AVE webapp:
        SIL + GS + device_id + RS + level
    Level is 0-31.
    """
    return encode_message("SIL", [device_id], [[str(level)]])


def encode_thermostat_set_point(
    device_id: str, season: int, mode: int, set_point: int
) -> bytes:
    """Encode a thermostat set-point command.

    Sends: STS + GS + device_id + RS + season,mode,setpoint
    set_point is in tenths of a degree (e.g. 210 = 21.0C).
    """
    return encode_message(
        "STS", [device_id], [[str(season), str(mode), str(set_point)]]
    )
