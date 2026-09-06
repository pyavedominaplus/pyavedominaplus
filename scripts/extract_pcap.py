#!/usr/bin/env python3
"""Extract AVE DominaPlus protocol data from PCAP files.

Handles WebSocket framing (including unmasking client→server frames)
so that both directions are decoded correctly.
"""

import argparse
import json
import logging
import struct
import sys
from pathlib import Path
from typing import Any

try:
    from scapy.all import IP, TCP, PcapReader
except ImportError:
    print("Error: scapy is required. Install with: pip install scapy")
    sys.exit(1)

from pyavedominaplus.protocol import decode_message

_LOGGER = logging.getLogger(__name__)

# AVE DominaPlus default port
DOMINA_PORT = 14001

# WebSocket opcodes
WS_OP_CONTINUATION = 0x0
WS_OP_TEXT = 0x1
WS_OP_BINARY = 0x2
WS_OP_CLOSE = 0x8
WS_OP_PING = 0x9
WS_OP_PONG = 0xA


def parse_ws_frames(buf: bytes) -> tuple[list[bytes], bytes]:
    """Parse WebSocket frames from a buffer.

    Returns (list of unmasked payloads, remaining unconsumed bytes).
    """
    payloads = []
    pos = 0
    while pos < len(buf):
        # Need at least 2 bytes for frame header
        if pos + 2 > len(buf):
            break

        b0 = buf[pos]
        b1 = buf[pos + 1]
        masked = bool(b1 & 0x80)
        payload_len = b1 & 0x7F
        header_len = 2

        if payload_len == 126:
            if pos + 4 > len(buf):
                break
            payload_len = struct.unpack("!H", buf[pos + 2 : pos + 4])[0]
            header_len = 4
        elif payload_len == 127:
            if pos + 10 > len(buf):
                break
            payload_len = struct.unpack("!Q", buf[pos + 2 : pos + 10])[0]
            header_len = 10

        if masked:
            header_len += 4

        frame_len = header_len + payload_len
        if pos + frame_len > len(buf):
            # Incomplete frame
            break

        mask_offset = header_len - 4 if masked else header_len
        payload_start = pos + header_len
        payload_bytes = bytearray(buf[payload_start : payload_start + payload_len])

        if masked:
            mask_key = buf[pos + mask_offset : pos + mask_offset + 4]
            for i in range(len(payload_bytes)):
                payload_bytes[i] ^= mask_key[i % 4]

        opcode = b0 & 0x0F
        if opcode in (WS_OP_TEXT, WS_OP_BINARY):
            payloads.append(bytes(payload_bytes))

        pos += frame_len

    return payloads, buf[pos:]


class PCAPExtractor:
    """Extract AVE DominaPlus messages from PCAP files."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.messages = []
        self.stream_buffers: dict[str, bytes] = {}
        self.ws_buffers: dict[str, bytes] = {}
        self.ws_mode: dict[str, bool] = {}
        self.ave_buffers: dict[str, bytes] = {}

        if verbose:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

    def _flow_key(self, src_ip: str, src_port: int, dst_ip: str, dst_port: int) -> str:
        return f"{src_ip}:{src_port}-{dst_ip}:{dst_port}"

    def _extract_ave_messages(self, payload: bytes, flow_key: str) -> None:
        """Extract AVE protocol messages (STX..EOT) from raw payload."""
        buf = self.ave_buffers.get(flow_key, b"") + payload
        while buf:
            stx_pos = buf.find(b"\x02")
            if stx_pos == -1:
                buf = b""
                break
            if stx_pos > 0:
                buf = buf[stx_pos:]

            eot_pos = buf.find(b"\x04")
            if eot_pos == -1:
                break

            msg_bytes = buf[: eot_pos + 1]
            buf = buf[eot_pos + 1 :]

            try:
                messages = decode_message(msg_bytes)
                for msg in messages:
                    self.messages.append(
                        {"flow": flow_key, "data": msg, "raw": msg_bytes.hex()}
                    )
                    _LOGGER.info(
                        "Decoded: %s %s",
                        msg.get("command"),
                        msg.get("parameters", [])[:3],
                    )
            except Exception as e:  # noqa: BLE001
                # One malformed frame must not stop the whole scan.
                _LOGGER.warning("Failed to decode message: %s", e)

        self.ave_buffers[flow_key] = buf

    def _process_payload(self, payload: bytes, flow_key: str, direction: str) -> None:
        """Process a TCP payload, handling WebSocket framing."""
        tcp_buf = self.stream_buffers.get(flow_key, b"") + payload
        self.stream_buffers[flow_key] = b""

        # Check if this flow has entered WebSocket mode
        if not self.ws_mode.get(flow_key):
            # Look for HTTP 101 Switching Protocols (WS handshake response)
            if b"HTTP/1.1 101" in tcp_buf or b"HTTP/1.0 101" in tcp_buf:
                end = tcp_buf.find(b"\r\n\r\n")
                if end == -1:
                    # Incomplete HTTP response header
                    self.stream_buffers[flow_key] = tcp_buf
                    return
                _LOGGER.debug("WebSocket handshake detected on %s", flow_key)
                # Mark BOTH directions as WS mode (handshake is server→client
                # but client→server is also WS after this)
                self.ws_mode[flow_key] = True
                # Also mark the reverse flow
                parts = flow_key.split("-")
                if len(parts) == 2:
                    reverse_key = f"{parts[1]}-{parts[0]}"
                    self.ws_mode[reverse_key] = True
                # Remainder after HTTP headers may contain WS frames
                tcp_buf = tcp_buf[end + 4 :]
                if not tcp_buf:
                    return
            elif tcp_buf.startswith((b"GET ", b"HTTP/")):
                # HTTP handshake in progress, buffer it
                self.stream_buffers[flow_key] = tcp_buf
                return
            else:
                # Not HTTP, try direct AVE protocol (no WS framing)
                self._extract_ave_messages(tcp_buf, flow_key)
                return

        # WebSocket mode: parse frames
        ws_buf = self.ws_buffers.get(flow_key, b"") + tcp_buf
        payloads, remaining = parse_ws_frames(ws_buf)
        self.ws_buffers[flow_key] = remaining

        for ws_payload in payloads:
            self._extract_ave_messages(ws_payload, flow_key)

    def parse_pcap(self, pcap_file: str) -> int:
        """Parse a PCAP file and extract DominaPlus messages."""
        if not Path(pcap_file).exists():
            _LOGGER.error("PCAP file not found: %s", pcap_file)
            return 0

        _LOGGER.info("Reading PCAP file: %s", pcap_file)
        packet_count = 0
        payload_count = 0

        try:
            with PcapReader(pcap_file) as pcap:
                for packet in pcap:
                    packet_count += 1

                    if not (IP in packet and TCP in packet):
                        continue

                    ip_layer = packet[IP]
                    tcp_layer = packet[TCP]

                    src_port = tcp_layer.sport
                    dst_port = tcp_layer.dport

                    if not (src_port == DOMINA_PORT or dst_port == DOMINA_PORT):
                        continue

                    if len(tcp_layer.payload) == 0:
                        continue

                    payload = bytes(tcp_layer.payload)
                    payload_count += 1
                    src_ip = ip_layer.src
                    dst_ip = ip_layer.dst

                    if dst_port == DOMINA_PORT:
                        flow_key = self._flow_key(src_ip, src_port, dst_ip, dst_port)
                        direction = "client"
                    else:
                        flow_key = self._flow_key(dst_ip, dst_port, src_ip, src_port)
                        direction = "server"

                    self._process_payload(payload, flow_key, direction)

            _LOGGER.info(
                "Parsed %d packets, found %d DominaPlus payloads, "
                "extracted %d messages",
                packet_count,
                payload_count,
                len(self.messages),
            )

        except FileNotFoundError:
            _LOGGER.error("Could not open PCAP file: %s", pcap_file)
            return 0
        except Exception:
            # Any malformed capture is reported, not raised: this is a CLI.
            _LOGGER.exception("Error reading PCAP file")
            return 0

        return len(self.messages)

    def get_commands(self) -> dict[str, int]:
        """Get a summary of commands found."""
        commands: dict[str, int] = {}
        for msg in self.messages:
            cmd = msg["data"].get("command")
            commands[cmd] = commands.get(cmd, 0) + 1
        return commands

    def get_devices(self) -> dict[str, Any]:
        """Extract device information from messages."""
        devices: dict[str, Any] = {}
        for msg in self.messages:
            cmd = msg["data"].get("command")
            records = msg["data"].get("records", [])
            if cmd == "ldi":
                for record in records:
                    if len(record) >= 3:
                        devices[record[0]] = {
                            "name": record[1],
                            "type": record[2],
                            "raw_record": record,
                        }
        return devices

    def get_areas(self) -> dict[str, Any]:
        """Extract area information from messages."""
        areas: dict[str, Any] = {}
        for msg in self.messages:
            cmd = msg["data"].get("command")
            records = msg["data"].get("records", [])
            if cmd == "lm":
                for record in records:
                    if len(record) >= 3:
                        areas[record[0]] = {
                            "name": record[1],
                            "order": record[2],
                        }
        return areas

    def export_json(self, output_file: str) -> None:
        """Export extracted data to JSON."""
        data = {
            "messages": self.messages,
            "summary": {
                "total_messages": len(self.messages),
                "commands": self.get_commands(),
                "devices": self.get_devices(),
                "areas": self.get_areas(),
            },
        }
        with open(output_file, "w") as f:
            json.dump(data, f, indent=2, default=str)
        _LOGGER.info("Exported data to: %s", output_file)

    def print_summary(self) -> None:
        """Print summary of extracted data."""
        print("\n" + "=" * 70)
        print("AVE DominaPlus PCAP Extraction Summary")
        print("=" * 70)

        commands = self.get_commands()
        print(f"\nTotal Messages: {len(self.messages)}")
        print(f"Commands found: {len(commands)}")

        print("\nCommand Breakdown:")
        for cmd, count in sorted(commands.items(), key=lambda x: -x[1]):
            print(f"  {cmd:8} - {count:4} messages")

        devices = self.get_devices()
        if devices:
            print(f"\nDevices ({len(devices)}):")
            for dev_id, info in sorted(devices.items()):
                print(f"  {dev_id:6} - {info['name']:30} (type {info['type']})")

        areas = self.get_areas()
        if areas:
            print(f"\nAreas ({len(areas)}):")
            for area_id, info in sorted(areas.items()):
                print(f"  {area_id:6} - {info['name']:30} (order {info['order']})")

        print("\n" + "=" * 70 + "\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Extract AVE DominaPlus data from PCAP files"
    )
    parser.add_argument("pcap_file", help="Path to PCAP file")
    parser.add_argument("-o", "--output", help="Export data to JSON file")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()

    extractor = PCAPExtractor(verbose=args.verbose)
    msg_count = extractor.parse_pcap(args.pcap_file)

    if msg_count == 0:
        print("No AVE DominaPlus messages found in PCAP file")
        sys.exit(1)

    extractor.print_summary()

    if args.output:
        extractor.export_json(args.output)


if __name__ == "__main__":
    main()
