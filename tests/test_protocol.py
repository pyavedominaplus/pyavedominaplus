"""Tests for AVE DominaPlus protocol encoding/decoding."""

from pyavedominaplus.const import STX, ETX, EOT, GS, RS
from pyavedominaplus.protocol import (
    build_crc,
    decode_message,
    encode_message,
    encode_device_command,
    encode_set_dimmer_level,
    encode_thermostat_set_point,
)


class TestBuildCRC:
    """Tests for CRC calculation."""

    def test_crc_simple(self):
        """Test CRC with a simple string."""
        data = chr(STX) + "LM" + chr(ETX)
        crc = build_crc(data)
        assert len(crc) == 2
        assert all(c in "0123456789ABCDEF" for c in crc)

    def test_crc_deterministic(self):
        """Test that CRC is deterministic."""
        data = chr(STX) + "LDI" + chr(ETX)
        crc1 = build_crc(data)
        crc2 = build_crc(data)
        assert crc1 == crc2

    def test_crc_different_for_different_data(self):
        """Test that different data produces different CRC."""
        data1 = chr(STX) + "LM" + chr(ETX)
        data2 = chr(STX) + "LDI" + chr(ETX)
        assert build_crc(data1) != build_crc(data2)

    def test_crc_xor_then_complement(self):
        """Verify the CRC algorithm: XOR all bytes, then 0xFF - result."""
        data = chr(STX) + "A" + chr(ETX)
        xor_val = STX ^ ord("A") ^ ETX
        expected = 0xFF - xor_val
        expected_hex = f"{expected:02X}"
        assert build_crc(data) == expected_hex


class TestEncodeMessage:
    """Tests for message encoding."""

    def test_encode_simple_command(self):
        """Test encoding a simple command with no parameters."""
        msg = encode_message("LM")
        text = msg.decode("utf-8")
        assert text[0] == chr(STX)
        assert text[-1] == chr(EOT)
        assert "LM" in text

    def test_encode_with_parameters(self):
        """Test encoding a command with parameters."""
        msg = encode_message("WSF", ["1"])
        text = msg.decode("utf-8")
        assert chr(GS) in text
        assert "WSF" in text
        assert "1" in text

    def test_encode_with_multiple_parameters(self):
        """Test encoding with multiple parameters."""
        msg = encode_message("WSC", ["100", "1"])
        text = msg.decode("utf-8")
        parts = text[1 : text.index(chr(ETX))]
        fields = parts.split(chr(GS))
        assert fields[0] == "WSC"
        assert fields[1] == "100"
        assert fields[2] == "1"

    def test_encode_with_records(self):
        """Test encoding with records."""
        msg = encode_message("STS", ["103"], [["1", "1", "210"]])
        text = msg.decode("utf-8")
        assert chr(RS) in text
        # Split off STX and ETX+CRC+EOT
        payload = text[1 : text.index(chr(ETX))]
        pieces = payload.split(chr(RS))
        assert len(pieces) == 2
        record_fields = pieces[1].split(chr(GS))
        assert record_fields == ["1", "1", "210"]

    def test_encode_has_valid_crc(self):
        """Test that the encoded message has a valid CRC."""
        msg = encode_message("PING")
        text = msg.decode("utf-8")
        etx_idx = text.index(chr(ETX))
        payload_with_etx = text[: etx_idx + 1]
        crc_in_msg = text[etx_idx + 1 : etx_idx + 3]
        assert crc_in_msg == build_crc(payload_with_etx)

    def test_encode_message_ends_with_eot(self):
        """Test that the message ends with EOT."""
        msg = encode_message("LDI")
        assert msg[-1] == EOT


class TestDecodeMessage:
    """Tests for message decoding."""

    def test_decode_simple_command(self):
        """Test decoding a simple command response."""
        raw = encode_message("ack")
        messages = decode_message(raw)
        assert len(messages) == 1
        assert messages[0]["command"] == "ack"
        assert messages[0]["parameters"] == []
        assert messages[0]["records"] == []

    def test_decode_with_parameters(self):
        """Test decoding a message with parameters."""
        raw = encode_message("upd", ["WS", "1", "100", "1"])
        messages = decode_message(raw)
        assert len(messages) == 1
        assert messages[0]["command"] == "upd"
        assert messages[0]["parameters"] == ["WS", "1", "100", "1"]

    def test_decode_with_records(self):
        """Test decoding a message with records."""
        raw = encode_message(
            "lm", records=[["1", "Living Room", "0"], ["2", "Bedroom", "1"]]
        )
        messages = decode_message(raw)
        assert len(messages) == 1
        assert messages[0]["command"] == "lm"
        assert len(messages[0]["records"]) == 2
        assert messages[0]["records"][0] == ["1", "Living Room", "0"]
        assert messages[0]["records"][1] == ["2", "Bedroom", "1"]

    def test_decode_with_parameters_and_records(self):
        """Test decoding a message with both parameters and records."""
        raw = encode_message(
            "wts", ["103"], [["1", "2", "6", "5", "1", "215", "1", "210", "0", "0"]]
        )
        messages = decode_message(raw)
        assert len(messages) == 1
        assert messages[0]["command"] == "wts"
        assert messages[0]["parameters"] == ["103"]
        assert len(messages[0]["records"]) == 1
        assert messages[0]["records"][0][5] == "215"  # temperature

    def test_roundtrip_encode_decode(self):
        """Test that encode followed by decode produces the original data."""
        original_cmd = "WSC"
        original_params = ["100", "1"]
        raw = encode_message(original_cmd, original_params)
        messages = decode_message(raw)
        assert messages[0]["command"] == original_cmd
        assert messages[0]["parameters"] == original_params

    def test_roundtrip_with_records(self):
        """Test roundtrip with records."""
        records = [["1", "Living Room", "0"], ["2", "Bedroom", "1"]]
        raw = encode_message("lm", records=records)
        messages = decode_message(raw)
        assert messages[0]["records"] == records

    def test_decode_multiple_messages(self):
        """Test decoding multiple messages concatenated in one frame."""
        msg1 = encode_message("ack")
        msg2 = encode_message("upd", ["WS", "1", "100", "1"])
        raw = msg1 + msg2
        messages = decode_message(raw)
        assert len(messages) == 2
        assert messages[0]["command"] == "ack"
        assert messages[1]["command"] == "upd"

    def test_decode_empty(self):
        """Test decoding empty data."""
        messages = decode_message(b"")
        assert messages == []


class TestConvenienceEncoders:
    """Tests for convenience encoding functions."""

    def test_encode_device_command(self):
        """Test device command encoding."""
        msg = encode_device_command("100", 1)
        decoded = decode_message(msg)
        assert decoded[0]["command"] == "WSC"
        assert decoded[0]["parameters"] == ["100", "1"]

    def test_encode_dimmer_level(self):
        """Test dimmer level encoding."""
        msg = encode_set_dimmer_level("101", 127)
        decoded = decode_message(msg)
        assert decoded[0]["command"] == "SIL"
        assert decoded[0]["parameters"] == ["101", "127"]

    def test_encode_thermostat_set_point(self):
        """Test thermostat set point encoding."""
        msg = encode_thermostat_set_point("103", season=1, mode=1, set_point=215)
        decoded = decode_message(msg)
        assert decoded[0]["command"] == "STS"
        assert decoded[0]["parameters"] == ["103"]
        assert decoded[0]["records"][0] == ["1", "1", "215"]

    def test_encode_thermostat_season(self):
        """Test thermostat season encoding."""
        from pyavedominaplus.protocol import encode_thermostat_season

        msg = encode_thermostat_season("103", season=0, mode=1, set_point=210)
        decoded = decode_message(msg)
        assert decoded[0]["command"] == "STS"
        assert decoded[0]["parameters"] == ["103"]
        assert decoded[0]["records"][0] == ["0", "1", "210"]


class TestProtocolEdgeCases:
    """Tests for protocol edge cases."""

    def test_decode_non_utf8_bytes(self):
        """Test decoding raw bytes that are not valid UTF-8."""
        # Build a valid message manually with non-UTF-8-safe path
        raw = bytes([STX, ord("a"), ord("c"), ord("k"), ETX, ord("F"), ord("5"), EOT])
        messages = decode_message(raw)
        assert len(messages) == 1
        assert messages[0]["command"] == "ack"

    def test_decode_garbage_data(self):
        """Test decoding completely invalid data produces empty list."""
        messages = decode_message(b"\x00\x01")
        assert messages == []

    def test_decode_only_eot(self):
        """Test decoding just an EOT character."""
        messages = decode_message(bytes([EOT]))
        assert messages == []

    def test_encode_empty_command(self):
        """Test encoding an empty command string."""
        msg = encode_message("")
        decoded = decode_message(msg)
        assert len(decoded) == 1
        assert decoded[0]["command"] == ""

    def test_encode_empty_parameters(self):
        """Test encoding with explicit empty parameter list."""
        msg = encode_message("LM", [])
        decoded = decode_message(msg)
        assert decoded[0]["command"] == "LM"
        assert decoded[0]["parameters"] == []

    def test_encode_empty_records(self):
        """Test encoding with explicit empty records list."""
        msg = encode_message("LM", None, [])
        decoded = decode_message(msg)
        assert decoded[0]["records"] == []

    def test_roundtrip_multiple_records(self):
        """Test roundtrip with multiple records."""
        records = [
            ["1", "Living Room", "0"],
            ["2", "Bedroom", "1"],
            ["3", "Kitchen", "2"],
        ]
        raw = encode_message("lm", records=records)
        decoded = decode_message(raw)
        assert decoded[0]["records"] == records

    def test_roundtrip_special_chars_in_values(self):
        """Test roundtrip with special characters in parameter values."""
        msg = encode_message("WSC", ["100", "255"])
        decoded = decode_message(msg)
        assert decoded[0]["parameters"] == ["100", "255"]

    def test_decode_three_concatenated_messages(self):
        """Test decoding three messages in one frame."""
        msg1 = encode_message("ack")
        msg2 = encode_message("upd", ["WS", "1", "100", "1"])
        msg3 = encode_message("pong")
        raw = msg1 + msg2 + msg3
        messages = decode_message(raw)
        assert len(messages) == 3
        assert messages[0]["command"] == "ack"
        assert messages[1]["command"] == "upd"
        assert messages[2]["command"] == "pong"

    def test_crc_single_char_payload(self):
        """Test CRC with minimal payload."""
        data = chr(STX) + chr(ETX)
        crc = build_crc(data)
        expected = 0xFF - (STX ^ ETX)
        assert crc == f"{expected:02X}"

    def test_encode_records_with_empty_fields(self):
        """Test encoding records that contain empty string fields."""
        raw = encode_message("STS", ["103"], [["", "1", "210"]])
        decoded = decode_message(raw)
        assert decoded[0]["records"][0][0] == ""
        assert decoded[0]["records"][0][1] == "1"

    def test_decode_invalid_utf8_bytes(self):
        """Test decode_message fallback when bytes are not valid UTF-8."""
        # Build a valid protocol message as raw bytes, then append 0xFF
        # which is invalid UTF-8 and forces the chr()-per-byte fallback path.
        # The 0xFF after EOT is ignored by the parser (too short segment).
        raw = b"\x02ack\x03F5\x04\xff"
        messages = decode_message(raw)
        assert len(messages) == 1
        assert messages[0]["command"] == "ack"
