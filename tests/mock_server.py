"""Mock AVE DominaPlus WebSocket server for testing."""

from aiohttp import web, WSMsgType

from pyavedominaplus.protocol import decode_message, encode_message

# Sample device data for the mock server
MOCK_AREAS = [
    ["1", "Living Room", "0"],
    ["2", "Bedroom", "1"],
    ["3", "Kitchen", "2"],
]

MOCK_DEVICES = [
    ["100", "Ceiling Light", "1", "1"],  # Light in Living Room
    ["101", "Floor Lamp", "2", "1"],  # Dimmer in Living Room
    ["102", "Window Blind", "3", "1;2"],  # Shutter in Living Room + Bedroom
    ["103", "Thermostat LR", "4", "1"],  # Thermostat in Living Room
    ["104", "Night Scenario", "6", "2"],  # Scenario in Bedroom
    ["105", "Bedroom Light", "1", "2"],  # Light in Bedroom
    ["106", "Kitchen Light", "22", "3"],  # Light variant in Kitchen
    ["107", "Roller Shutter", "16", "3"],  # Shutter variant in Kitchen
    ["108", "Power Meter", "9", "1"],  # Energy in Living Room
]

MOCK_MAP_COMMANDS = {
    "1": [  # Living Room
        [
            "1",
            "Ceiling Light",
            "1",
            "50",
            "60",
            "0",
            "1",
            "2",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "100",
            "1",
        ],
        [
            "2",
            "Floor Lamp",
            "2",
            "100",
            "60",
            "0",
            "1",
            "2",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "101",
            "2",
        ],
        [
            "3",
            "Window Blind",
            "3",
            "150",
            "60",
            "0",
            "1",
            "2",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "102",
            "3",
        ],
    ],
    "2": [  # Bedroom
        [
            "4",
            "Bedroom Light",
            "1",
            "50",
            "60",
            "0",
            "1",
            "2",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "105",
            "1",
        ],
        [
            "5",
            "Night Scenario",
            "17",  # command_type 17 = scenario
            "100",
            "60",
            "0",
            "1",
            "2",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "104",
            "6",
        ],
    ],
    "3": [  # Kitchen
        [
            "6",
            "Kitchen Light",
            "1",
            "50",
            "60",
            "0",
            "1",
            "2",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "106",
            "22",
        ],
        [
            "7",
            "Roller Shutter",
            "3",
            "100",
            "60",
            "0",
            "1",
            "2",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "107",
            "16",
        ],
    ],
}

# Map from command_id to device_id (for ES scenario execution)
MOCK_COMMAND_TO_DEVICE = {
    cmd[0]: cmd[14]
    for area_cmds in MOCK_MAP_COMMANDS.values()
    for cmd in area_cmds
    if len(cmd) > 14
}

MOCK_DEVICE_ADDRESSES = [
    ["100", "Ceiling Light", "1", "10"],
    ["101", "Floor Lamp", "2", "11"],
    ["102", "Window Blind", "3", "12"],
    ["105", "Bedroom Light", "1", "15"],
    ["106", "Kitchen Light", "22", "16"],
    ["108", "Power Meter", "9", "18"],
]

# Device statuses: device_id -> current value
_device_statuses: dict[str, int] = {
    "100": 0,
    "101": 0,
    "102": 0,
    "103": 0,
    "104": 0,
    "105": 1,
    "106": 0,
    "107": 0,
    "108": 0,
}

# Thermostat status records:
# [fan_on, fan_level, configuration, offset, season_bits, temperature, mode, set_point, antifreeze, local_off]
MOCK_THERMOSTAT_STATUS = ["1", "2", "6", "5", "1", "215", "1", "210", "0", "0"]


class MockDominaServer:
    """A mock AVE DominaPlus server for testing."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._clients: set[web.WebSocketResponse] = set()
        self.device_statuses = dict(_device_statuses)
        self.received_commands: list[dict] = []

    async def start(self) -> int:
        """Start the mock server and return the assigned port."""
        self._app = web.Application()
        self._app.router.add_get("/", self._handler)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        # Get actual port (useful when port=0 for auto-assign)
        sockets = self._site._server.sockets
        if sockets:
            self.port = sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        """Stop the mock server."""
        # Close all open websocket connections
        for ws in list(self._clients):
            await ws.close()
        self._clients.clear()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._app = None
        self._site = None

    async def send_update(
        self,
        command: str,
        parameters: list[str] | None = None,
        records: list[list[str]] | None = None,
    ) -> None:
        """Send an update to all connected clients (simulates server-initiated events)."""
        msg = encode_message(command.lower(), parameters, records)
        for ws in list(self._clients):
            try:
                await ws.send_bytes(msg)
            except Exception:
                pass

    async def send_text_update(
        self,
        command: str,
        parameters: list[str] | None = None,
        records: list[list[str]] | None = None,
    ) -> None:
        """Send a text-framed update to all connected clients."""
        msg = encode_message(command.lower(), parameters, records)
        text = msg.decode("utf-8")
        for ws in list(self._clients):
            try:
                await ws.send_str(text)
            except Exception:
                pass

    async def _handler(self, request: web.Request) -> web.WebSocketResponse:
        """Handle a WebSocket connection."""
        ws = web.WebSocketResponse(protocols=["binary", "base64"])
        await ws.prepare(request)
        self._clients.add(ws)
        try:
            async for ws_msg in ws:
                if ws_msg.type == WSMsgType.BINARY:
                    raw = ws_msg.data
                elif ws_msg.type == WSMsgType.TEXT:
                    raw = ws_msg.data.encode("utf-8")
                elif ws_msg.type in (
                    WSMsgType.ERROR,
                    WSMsgType.CLOSED,
                    WSMsgType.CLOSING,
                ):
                    break
                else:
                    continue
                messages = decode_message(raw)
                for msg in messages:
                    self.received_commands.append(msg)
                    await self._process_command(ws, msg)
        finally:
            self._clients.discard(ws)
        return ws

    async def _process_command(self, ws: web.WebSocketResponse, msg: dict) -> None:
        """Process a command and send the appropriate response."""
        command = msg["command"]
        parameters = msg["parameters"]

        if command == "LM":
            await self._respond_lm(ws)
        elif command == "LDI":
            await self._respond_ldi(ws)
        elif command == "LI2":
            await self._respond_li2(ws)
        elif command == "LMC":
            await self._respond_lmc(ws, parameters)
        elif command == "LML":
            resp = encode_message("ack", [command])
            await ws.send_bytes(resp)
        elif command == "WTS":
            await self._respond_wts(ws, parameters)
        elif command == "WSF":
            await self._respond_wsf(ws, parameters)
        elif command == "EBI":
            await self._process_ebi(ws, parameters)
        elif command == "EAI":
            await self._process_eai(ws, parameters)
        elif command == "ES":
            await self._process_es(ws, parameters)
        elif command == "SIL":
            await self._process_sil(ws, parameters)
        elif command == "STS":
            await self._process_sts(ws, parameters, msg["records"])
        elif command == "PING":
            resp = encode_message("pong")
            await ws.send_bytes(resp)
        elif command == "PONG":
            pass  # No response needed
        elif command in ("SU2", "SU3", "GTM", "GMA", "GNA", "GSF", "TTK", "TOO", "TUU"):
            resp = encode_message("ack", [command])
            await ws.send_bytes(resp)

    async def _respond_lm(self, ws: web.WebSocketResponse) -> None:
        """Send the list of maps/areas."""
        resp = encode_message("lm", records=MOCK_AREAS)
        await ws.send_bytes(resp)

    async def _respond_ldi(self, ws: web.WebSocketResponse) -> None:
        """Send the list of devices."""
        resp = encode_message("ldi", records=MOCK_DEVICES)
        await ws.send_bytes(resp)

    async def _respond_li2(self, ws: web.WebSocketResponse) -> None:
        """Send device addresses."""
        resp = encode_message("li2", records=MOCK_DEVICE_ADDRESSES)
        await ws.send_bytes(resp)

    async def _respond_lmc(
        self, ws: web.WebSocketResponse, parameters: list[str]
    ) -> None:
        """Send map commands for a given area."""
        area_id = parameters[0] if parameters else None
        commands = MOCK_MAP_COMMANDS.get(area_id, [])
        resp = encode_message(
            "lmc", parameters=[area_id] if area_id else None, records=commands
        )
        await ws.send_bytes(resp)

    async def _respond_wts(
        self, ws: web.WebSocketResponse, parameters: list[str]
    ) -> None:
        """Send thermostat status."""
        device_id = parameters[0] if parameters else "103"
        resp = encode_message(
            "wts", parameters=[device_id], records=[MOCK_THERMOSTAT_STATUS]
        )
        await ws.send_bytes(resp)

    async def _respond_wsf(
        self, ws: web.WebSocketResponse, parameters: list[str]
    ) -> None:
        """Send device statuses for a given family."""
        family = parameters[0] if parameters else "1"
        family_int = int(family)
        # Send UPD WS for each device of this family
        for device in MOCK_DEVICES:
            device_type = int(device[2])
            if device_type == family_int:
                device_id = device[0]
                status = self.device_statuses.get(device_id, 0)
                upd = encode_message(
                    "upd",
                    parameters=["WS", str(device_type), device_id, str(status)],
                )
                await ws.send_bytes(upd)
        # Send ACK when done
        resp = encode_message("ack")
        await ws.send_bytes(resp)

    def _find_device_type(self, device_id: str) -> int:
        """Find the device type for a device ID."""
        for d in MOCK_DEVICES:
            if d[0] == device_id:
                return int(d[2])
        return 1

    async def _send_upd_ws(self, device_id: str, value: int) -> None:
        """Send a WS update to all clients."""
        device_type = self._find_device_type(device_id)
        upd = encode_message(
            "upd",
            parameters=["WS", str(device_type), device_id, str(value)],
        )
        for client in self._clients:
            try:
                await client.send_bytes(upd)
            except Exception:
                pass

    async def _process_ebi(
        self, ws: web.WebSocketResponse, parameters: list[str]
    ) -> None:
        """Process a light/energy EBI command."""
        if len(parameters) < 2:
            return
        device_id = parameters[0]
        sub_cmd = parameters[1]
        current = self.device_statuses.get(device_id, 0)
        if sub_cmd == "11":  # ON
            new_value = 1
        elif sub_cmd == "12":  # OFF
            new_value = 0
        elif sub_cmd == "10":  # TOGGLE
            new_value = 0 if current else 1
        elif sub_cmd == "2":  # DIMMER STEP
            new_value = 0 if current else 1
        else:
            new_value = current
        self.device_statuses[device_id] = new_value
        resp = encode_message("ack", ["EBI"])
        await ws.send_bytes(resp)
        await self._send_upd_ws(device_id, new_value)

    async def _process_eai(
        self, ws: web.WebSocketResponse, parameters: list[str]
    ) -> None:
        """Process a shutter EAI command."""
        if len(parameters) < 2:
            return
        device_id = parameters[0]
        sub_cmd = parameters[1]
        if sub_cmd == "8":  # OPEN -> OPENING (2) -> OPEN (1)
            new_value = 2  # OPENING
        elif sub_cmd == "9":  # CLOSE -> CLOSING (4) -> CLOSED (3)
            new_value = 4  # CLOSING
        else:
            new_value = self.device_statuses.get(device_id, 0)
        self.device_statuses[device_id] = new_value
        resp = encode_message("ack", ["EAI"])
        await ws.send_bytes(resp)
        await self._send_upd_ws(device_id, new_value)

    async def _process_es(
        self, ws: web.WebSocketResponse, parameters: list[str]
    ) -> None:
        """Process an ES (execute scenario) command."""
        if not parameters:
            return
        command_id = parameters[0]
        device_id = MOCK_COMMAND_TO_DEVICE.get(command_id)
        if device_id:
            self.device_statuses[device_id] = 1
            resp = encode_message("ack", ["ES"])
            await ws.send_bytes(resp)
            await self._send_upd_ws(device_id, 1)

    async def _process_sil(
        self, ws: web.WebSocketResponse, parameters: list[str]
    ) -> None:
        """Process a dimmer level command."""
        if len(parameters) >= 2:
            device_id = parameters[0]
            level = int(parameters[1])
            self.device_statuses[device_id] = level
            await self._send_upd_ws(device_id, level)

    async def _process_sts(
        self, ws: web.WebSocketResponse, parameters: list[str], records: list[list[str]]
    ) -> None:
        """Process a thermostat set command."""
        if not parameters:
            return
        device_id = parameters[0]
        if records and records[0] and len(records[0]) >= 3:
            set_point = records[0][2]
            # Send UPD for set point change
            upd = encode_message("upd", parameters=["TP", device_id, set_point])
            for client in self._clients:
                try:
                    await client.send_bytes(upd)
                except Exception:
                    pass
