"""Mock AVE DominaPlus WebSocket server for testing."""

import asyncio
import time

from aiohttp import WSMsgType, web

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
        [
            "8",
            "Thermostat LR",
            "4",  # command_type 4 = thermostat
            "200",
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
            "103",
            "4",
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
# Shutters report 1/2/3/4/5 only; 3 (closed) is what real hardware reports
# for an idle shutter (see test_real_app_initialization.pcap, where all 13
# shutters answer WSF family 3 with status 3).
_device_statuses: dict[str, int] = {
    "100": 0,
    "101": 0,
    "102": 3,
    "103": 0,
    "104": 0,
    "105": 1,
    "106": 0,
    "107": 3,
    "108": 0,
}

# Thermostat status records:
# [fan_on, fan_level, configuration, offset, season_bits, temperature, mode, set_point, antifreeze, local_off]
MOCK_THERMOSTAT_STATUS = ["1", "2", "6", "5", "1", "215", "1", "210", "0", "0"]


class MockDominaServer:
    """A mock AVE DominaPlus server for testing."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        shutter_transition_time: float = 60.0,
        wsf_records: bool = False,
        sts_echo: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.shutter_transition_time = shutter_transition_time
        # Every capture in this repo shows real hardware answering WSF with
        # individual UPD WS messages, so that is the default. Set wsf_records
        # to emulate the wsf record response the dissector documents.
        self.wsf_records = wsf_records
        # Real hardware does not reliably echo TP/TM/WT S after STS;
        # set sts_echo=False to emulate that.
        self.sts_echo = sts_echo
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._clients: set[web.WebSocketResponse] = set()
        self.device_statuses = dict(_device_statuses)
        self.thermostat_status: list[str] = list(MOCK_THERMOSTAT_STATUS)
        self.thermostat_local_off: dict[str, int] = {"103": 0}
        self.received_commands: list[dict] = []
        self._shutter_tasks: dict[str, asyncio.Task] = {}
        # Fractional shutter travel, 0.0 = closed, 1.0 = open. Tracked so a
        # run resumed from a partial position takes only the time the
        # remaining distance needs, the way real hardware behaves.
        self.shutter_positions: dict[str, float] = {}
        # device_id -> (started_at, start_position, target_position)
        self._shutter_moves: dict[str, tuple[float, float, float]] = {}

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
        # Cancel pending shutter transition tasks
        for task in self._shutter_tasks.values():
            task.cancel()
        self._shutter_tasks.clear()
        # Close all open websocket connections
        for ws in list(self._clients):
            await ws.close()
        self._clients.clear()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._app = None
        self._site = None

    async def _broadcast(self, *messages: bytes) -> None:
        """Send messages to every connected client, skipping ones that fail.

        A client that has gone away mid-test must not turn into a test
        failure, so send errors are dropped here rather than at each of the
        call sites that broadcast.
        """
        for ws in list(self._clients):
            try:
                for msg in messages:
                    await ws.send_bytes(msg)
            except Exception:  # noqa: BLE001, S110 - a gone client is not a failure
                pass

    async def send_update(
        self,
        command: str,
        parameters: list[str] | None = None,
        records: list[list[str]] | None = None,
    ) -> None:
        """Send an update to all connected clients (simulates server-initiated events)."""
        await self._broadcast(encode_message(command.lower(), parameters, records))

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
            except Exception:  # noqa: BLE001, S110 - a gone client is not a failure
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
            await self._process_sil(ws, parameters, msg["records"])
        elif command == "STS":
            await self._process_sts(ws, parameters, msg["records"])
        elif command == "PING":
            resp = encode_message("pong")
            await ws.send_bytes(resp)
        elif command == "PONG":
            pass  # No response needed
        elif command in ("TOO", "TUU"):
            await self._process_too(ws, parameters)
        elif command in ("SU2", "SU3", "GTM", "GMA", "GNA", "GSF", "TTK"):
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
            "wts", parameters=[device_id], records=[self.thermostat_status]
        )
        await ws.send_bytes(resp)

    async def _respond_wsf(
        self, ws: web.WebSocketResponse, parameters: list[str]
    ) -> None:
        """Send device statuses for a given family.

        Real hardware answers with individual UPD WS messages; the
        wsf_records mode answers with a single wsf message whose records
        are [device_id, status] pairs instead.
        """
        family = parameters[0] if parameters else "1"
        family_int = int(family)
        if not self.wsf_records:
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
            return
        records = [
            [device[0], str(self.device_statuses.get(device[0], 0))]
            for device in MOCK_DEVICES
            if int(device[2]) == family_int
        ]
        resp = encode_message("wsf", parameters=[family], records=records)
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
        await self._broadcast(upd)

    async def _process_ebi(
        self, ws: web.WebSocketResponse, parameters: list[str]
    ) -> None:
        """Process a light/energy EBI command."""
        if len(parameters) < 2:
            return
        device_id = parameters[0]
        sub_cmd = parameters[1]
        current = self.device_statuses.get(device_id, 0)
        if sub_cmd == "11":  # LIGHT ON
            new_value = 1
        elif sub_cmd == "12":  # LIGHT OFF
            new_value = 0
        elif sub_cmd == "10":  # LIGHT TOGGLE
            new_value = 0 if current else 1
        elif sub_cmd == "3":  # DIMMER ON
            new_value = 1
        elif sub_cmd == "4":  # DIMMER OFF
            new_value = 0
        elif sub_cmd == "2":  # DIMMER STEP
            new_value = 0 if current else 1
        else:
            new_value = current
        self.device_statuses[device_id] = new_value
        resp = encode_message("ack", ["EBI"])
        await ws.send_bytes(resp)
        await self._send_upd_ws(device_id, new_value)

    def _shutter_position(self, device_id: str) -> float:
        """Return the tracked travel fraction, deriving it from status if new.

        Tests set device_statuses directly, so a device with no tracked
        position falls back to what its status implies.
        """
        if device_id in self.shutter_positions:
            return self.shutter_positions[device_id]
        status = self.device_statuses.get(device_id, 0)
        return {1: 1.0, 3: 0.0}.get(status, 0.5)

    def set_shutter_position(self, device_id: str, fraction: float) -> None:
        """Place a shutter at a travel fraction (0.0 closed, 1.0 open).

        Also sets the reported status to match, so a test can start a run
        from a position that actually has somewhere left to travel.
        """
        fraction = max(0.0, min(1.0, fraction))
        self.shutter_positions[device_id] = fraction
        if fraction >= 1.0:
            status = 1  # OPEN
        elif fraction <= 0.0:
            status = 3  # CLOSED
        else:
            status = 5  # STOPPED mid-travel
        self.device_statuses[device_id] = status
        self._shutter_moves.pop(device_id, None)

    def _settle_shutter_position(self, device_id: str) -> None:
        """Freeze the travel fraction of a run that is being interrupted."""
        move = self._shutter_moves.pop(device_id, None)
        if move is None:
            return
        started, start_pos, target = move
        duration = abs(target - start_pos) * self.shutter_transition_time
        if duration <= 0:
            self.shutter_positions[device_id] = target
            return
        fraction = min(1.0, (time.monotonic() - started) / duration)
        self.shutter_positions[device_id] = start_pos + (target - start_pos) * fraction

    async def _process_eai(
        self, ws: web.WebSocketResponse, parameters: list[str]
    ) -> None:
        """Process a shutter EAI command.

        Simulates real hardware behavior:
        - Open/close starts the motor (OPENING=2 / CLOSING=4)
        - Re-sending the same direction while moving stops (STOPPED=5)
        - The run takes shutter_transition_time scaled by the distance left
          to travel, then reports the final state (OPEN=1 / CLOSED=3)
        """
        if len(parameters) < 2:
            return
        device_id = parameters[0]
        sub_cmd = parameters[1]
        resp = encode_message("ack", ["EAI"])
        await ws.send_bytes(resp)
        await self.apply_shutter_command(device_id, sub_cmd)

    async def press_wall_switch(self, device_id: str, sub_cmd: str) -> None:
        """Move a shutter as if someone pressed the physical wall switch.

        Same effect as an EAI command ("8" up, "9" down) but server
        initiated: clients only find out from the pushed status update.
        """
        await self.apply_shutter_command(device_id, sub_cmd)

    async def apply_shutter_command(self, device_id: str, sub_cmd: str) -> None:
        """Apply an open/close/stop to a shutter and push the new status."""
        current = self.device_statuses.get(device_id, 0)
        if sub_cmd == "8":
            if current == 2:
                # Open while opening -> stop
                new_value = 5
            else:
                new_value = 2  # OPENING
        elif sub_cmd == "9":
            if current == 4:
                # Close while closing -> stop
                new_value = 5
            else:
                new_value = 4  # CLOSING
        else:
            new_value = current
        # Cancel any pending transition for this device
        if device_id in self._shutter_tasks:
            self._shutter_tasks[device_id].cancel()
            del self._shutter_tasks[device_id]
        self._settle_shutter_position(device_id)
        # Resolve the start position while device_statuses still holds the
        # pre-command status: the fallback derives from it.
        start = self._shutter_position(device_id)
        self.shutter_positions[device_id] = start
        self.device_statuses[device_id] = new_value
        await self._send_upd_ws(device_id, new_value)
        # Schedule transition to final state if shutter is moving
        if new_value in (2, 4):
            final_value = 1 if new_value == 2 else 3  # OPEN or CLOSED
            target = 1.0 if new_value == 2 else 0.0
            duration = abs(target - start) * self.shutter_transition_time
            self._shutter_moves[device_id] = (time.monotonic(), start, target)
            self._shutter_tasks[device_id] = asyncio.ensure_future(
                self._shutter_transition(device_id, final_value, duration, target)
            )

    async def _shutter_transition(
        self,
        device_id: str,
        final_value: int,
        duration: float | None = None,
        target: float | None = None,
    ) -> None:
        """After the travel time, move the shutter to its final state."""
        if duration is None:
            duration = self.shutter_transition_time
        try:
            await asyncio.sleep(duration)
        except asyncio.CancelledError:
            return
        self._shutter_moves.pop(device_id, None)
        if target is not None:
            self.shutter_positions[device_id] = target
        self.device_statuses[device_id] = final_value
        await self._send_upd_ws(device_id, final_value)

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
        self, ws: web.WebSocketResponse, parameters: list[str], records: list[list[str]]
    ) -> None:
        """Process a dimmer level command.

        The level is sent as a record (RS-separated), not as a parameter.
        """
        if parameters and records and records[0]:
            device_id = parameters[0]
            level = int(records[0][0])
            self.device_statuses[device_id] = level
            await self._send_upd_ws(device_id, level)

    async def _process_sts(
        self, ws: web.WebSocketResponse, parameters: list[str], records: list[list[str]]
    ) -> None:
        """Process a thermostat set command (STS).

        Record format: [season, mode, set_point]
        Sends UPD TP for set point and UPD TM for mode changes.
        """
        if not parameters:
            return
        device_id = parameters[0]
        if records and records[0] and len(records[0]) >= 3:
            season = records[0][0]
            mode = records[0][1]
            set_point = records[0][2]
            # Keep the thermostat status in sync so WTS re-reads reflect
            # the change even when echoes are disabled.
            self.thermostat_status = list(MOCK_THERMOSTAT_STATUS)
            self.thermostat_status[4] = season
            self.thermostat_status[6] = mode
            self.thermostat_status[7] = set_point
            if not self.sts_echo:
                return
            # Send UPD for set point change
            upd_tp = encode_message("upd", parameters=["TP", device_id, set_point])
            # Send UPD for mode change
            # TM mode: 'M' for manual (1), 'A' for auto (0)
            mode_letter = "A" if mode == "0" else "M"
            upd_tm = encode_message("upd", parameters=["TM", device_id, mode_letter])
            # Send UPD for season change
            upd_season = encode_message(
                "upd", parameters=["WT", "S", device_id, season]
            )
            await self._broadcast(upd_tp, upd_tm, upd_season)

    async def _process_too(
        self, ws: web.WebSocketResponse, parameters: list[str]
    ) -> None:
        """Process a TOO/TUU (thermostat local off toggle) command.

        The client sends TOO/TUU with the current local_off value.
        The server inverts it and sends a UPD WT Z update.
        """
        if len(parameters) < 2:
            return
        device_id = parameters[0]
        current = int(parameters[1])
        new_local_off = 0 if current == 1 else 1
        self.thermostat_local_off[device_id] = new_local_off
        # Send ACK
        resp = encode_message("ack", ["TOO"])
        await ws.send_bytes(resp)
        # Send UPD WT Z with new local_off state
        upd = encode_message(
            "upd", parameters=["WT", "Z", device_id, str(new_local_off)]
        )
        await self._broadcast(upd)
