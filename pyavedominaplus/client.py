"""Async WebSocket client for AVE DominaPlus."""

import asyncio
import logging
from typing import Any, Callable

import aiohttp

from .const import (
    CMD_GET_DEVICE_STATUS_FAMILY,
    CMD_GET_MARCIA_ARRESTO,
    CMD_GET_NO_ACTION,
    CMD_GET_THERMOSTAT_MODE,
    CMD_GET_THERMOSTAT_STATUS,
    CMD_LIST_DEVICES,
    CMD_LIST_DEVICE_ADDRESSES,
    CMD_LIST_MAP_COMMANDS,
    CMD_LIST_MAPS,
    CMD_PONG,
    CMD_SET_DIMMER_LEVEL,
    CMD_SET_THERMOSTAT_STATUS,
    CMD_SUBSCRIBE_UPDATES_2,
    CMD_SUBSCRIBE_UPDATES_3,
    CONN_STATUS_CLOSE,
    CONN_STATUS_ERROR,
    CONN_STATUS_OPEN,
    DEFAULT_WS_PORT,
    DEVICE_TYPE_THERMOSTAT,
    UPD_DEVICE_STATUS,
    UPD_THERMOSTAT,
    UPD_THERMOSTAT_SETPOINT,
    UPD_THERMOSTAT_MODE,
    UPD_THERMOSTAT_KEYBOARD_LOCK,
    UPD_THERMOSTAT_WINDOW,
    UPD_HUMIDITY,
    UPD_RGB,
)
from .models import (
    DominaArea,
    DominaDevice,
    DominaMapCommand,
    DominaThermostat,
)
from .protocol import decode_message, encode_message

_LOGGER = logging.getLogger(__name__)

# Callback type for status updates
UpdateCallback = Callable[[str, dict[str, Any]], None]


class AVEDominaClient:
    """Async client for communicating with an AVE DominaPlus server."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_WS_PORT,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._session = session
        self._owns_session = session is None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._running = False
        self._listen_task: asyncio.Task | None = None
        self._devices: dict[str, DominaDevice] = {}
        self._thermostats: dict[str, DominaThermostat] = {}
        self._areas: dict[str, DominaArea] = {}
        self._update_callbacks: list[UpdateCallback] = []
        self._connection_status_callbacks: list[Callable[[str], None]] = []
        self._initialized = asyncio.Event()
        self._lm_loaded = False
        self._ldi_loaded = False
        self._lmc_pending = 0

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    @property
    def devices(self) -> dict[str, DominaDevice]:
        return self._devices

    @property
    def thermostats(self) -> dict[str, DominaThermostat]:
        return self._thermostats

    @property
    def areas(self) -> dict[str, DominaArea]:
        return self._areas

    def register_update_callback(self, callback: UpdateCallback) -> Callable[[], None]:
        """Register a callback for device status updates.

        Returns a callable to unregister.
        """
        self._update_callbacks.append(callback)

        def _unregister():
            self._update_callbacks.remove(callback)

        return _unregister

    def register_connection_callback(
        self, callback: Callable[[str], None]
    ) -> Callable[[], None]:
        """Register a callback for connection status changes."""
        self._connection_status_callbacks.append(callback)

        def _unregister():
            self._connection_status_callbacks.remove(callback)

        return _unregister

    def _notify_update(self, event_type: str, data: dict[str, Any]) -> None:
        for cb in self._update_callbacks:
            try:
                cb(event_type, data)
            except Exception:
                _LOGGER.exception("Error in update callback")

    def _notify_connection(self, status: str) -> None:
        for cb in self._connection_status_callbacks:
            try:
                cb(status)
            except Exception:
                _LOGGER.exception("Error in connection callback")

    async def connect(self) -> None:
        """Connect to the DominaPlus WebSocket server."""
        _LOGGER.debug("Connecting to %s", self.url)
        if self._owns_session or self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        self._ws = await self._session.ws_connect(
            self.url,
            protocols=["binary", "base64"],
            timeout=aiohttp.ClientWSTimeout(ws_close=10.0),
        )
        self._running = True
        self._notify_connection(CONN_STATUS_OPEN)
        self._listen_task = asyncio.ensure_future(self._listen_loop())

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        self._notify_connection(CONN_STATUS_CLOSE)

    async def send_command(
        self,
        command: str,
        parameters: list[str] | None = None,
        records: list[list[str]] | None = None,
    ) -> None:
        """Send a command to the server."""
        if not self.connected:
            raise ConnectionError("Not connected to DominaPlus server")
        msg = encode_message(command, parameters, records)
        await self._ws.send_bytes(msg)

    async def initialize(self) -> None:
        """Request initial data load (areas, devices)."""
        await self.send_command(CMD_LIST_MAPS)
        await self.send_command(CMD_LIST_DEVICES)

    async def request_device_statuses(self) -> None:
        """Request current status for all device families."""
        families = ["1", "2", "22", "9", "3", "16", "19", "6"]
        for family in families:
            await self.send_command(CMD_GET_DEVICE_STATUS_FAMILY, [family])
        await self.send_command(CMD_SUBSCRIBE_UPDATES_2)
        await self.send_command(CMD_SUBSCRIBE_UPDATES_3)

    async def turn_on_light(self, device_id: str) -> None:
        """Turn on a light device."""
        await self.send_command("WSC", [device_id, "1"])

    async def turn_off_light(self, device_id: str) -> None:
        """Turn off a light device."""
        await self.send_command("WSC", [device_id, "0"])

    async def set_dimmer_level(self, device_id: str, level: int) -> None:
        """Set a dimmer to a specific level (0-254)."""
        level = max(0, min(254, level))
        await self.send_command(CMD_SET_DIMMER_LEVEL, [device_id, str(level)])

    async def open_shutter(self, device_id: str) -> None:
        """Open a shutter."""
        await self.send_command("WSC", [device_id, "1"])

    async def close_shutter(self, device_id: str) -> None:
        """Close a shutter."""
        await self.send_command("WSC", [device_id, "0"])

    async def stop_shutter(self, device_id: str) -> None:
        """Stop a shutter."""
        await self.send_command("WSC", [device_id, "2"])

    async def activate_scenario(self, device_id: str) -> None:
        """Activate a scenario."""
        await self.send_command("WSC", [device_id, "1"])

    async def set_thermostat_set_point(self, device_id: str, set_point: float) -> None:
        """Set a thermostat's target temperature.

        set_point is in degrees (e.g. 21.5).
        """
        thermo = self._thermostats.get(device_id)
        if not thermo:
            return
        raw_sp = int(set_point * 10)
        await self.send_command(
            CMD_SET_THERMOSTAT_STATUS,
            [device_id],
            [[str(thermo.season), "1", str(raw_sp)]],
        )

    async def set_thermostat_season(self, device_id: str, season: int) -> None:
        """Set a thermostat's season (0=summer, 1=winter)."""
        thermo = self._thermostats.get(device_id)
        if not thermo:
            return
        raw_sp = int(thermo.set_point * 10)
        await self.send_command(
            CMD_SET_THERMOSTAT_STATUS,
            [device_id],
            [[str(season), str(thermo.mode), str(raw_sp)]],
        )

    async def _listen_loop(self) -> None:
        """Main loop for receiving messages from the server."""
        try:
            async for ws_msg in self._ws:
                if not self._running:
                    break
                if ws_msg.type == aiohttp.WSMsgType.BINARY:
                    raw = ws_msg.data
                elif ws_msg.type == aiohttp.WSMsgType.TEXT:
                    raw = ws_msg.data.encode("utf-8")
                elif ws_msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
                else:
                    continue
                try:
                    messages = decode_message(raw)
                    for msg in messages:
                        await self._handle_message(msg)
                except Exception:
                    _LOGGER.exception("Error processing message")
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Error in listen loop")
        finally:
            if self._running:
                self._notify_connection(CONN_STATUS_ERROR)

    async def _handle_message(self, msg: dict) -> None:
        """Handle a decoded protocol message."""
        command = msg["command"]
        parameters = msg["parameters"]
        records = msg["records"]

        handler = {
            "lm": self._handle_lm,
            "ldi": self._handle_ldi,
            "li2": self._handle_li2,
            "lmc": self._handle_lmc,
            "upd": self._handle_upd,
            "wts": self._handle_wts,
            "ping": self._handle_ping,
            "ack": self._handle_ack,
            "gsf": self._handle_gsf,
        }.get(command)

        if handler:
            await handler(parameters, records)

    async def _handle_lm(self, parameters: list[str], records: list[list[str]]) -> None:
        """Handle LM (list maps/areas) response."""
        if self._lm_loaded:
            return
        self._areas.clear()
        self._lmc_pending = len(records)
        for record in records:
            if len(record) >= 3:
                area = DominaArea(
                    id=record[0],
                    name=record[1],
                    order=record[2],
                )
                self._areas[area.id] = area
                await self.send_command(CMD_LIST_MAP_COMMANDS, [area.id])
        self._lm_loaded = True
        self._notify_update("lm_loaded", {"areas": self._areas})

    async def _handle_ldi(
        self, parameters: list[str], records: list[list[str]]
    ) -> None:
        """Handle LDI (list devices) response."""
        if self._ldi_loaded:
            return
        self._devices.clear()
        self._thermostats.clear()
        for record in records:
            if len(record) < 3:
                continue
            device_id = record[0]
            device_name = record[1]
            device_type = int(record[2])
            device_maps = record[3] if len(record) > 3 else ""

            # Handle RGBW prefix
            is_rgbw = device_name.startswith("$")
            if is_rgbw:
                device_name = device_name.lstrip("$")

            # Handle DALI suffix
            is_dali = device_name.endswith("$")
            if is_dali:
                device_name = device_name.rstrip("$")

            # Handle VMC Daikin ModBus (ID > 10000000)
            is_vmc_daikin = (
                device_type == DEVICE_TYPE_THERMOSTAT and int(device_id) > 10000000
            )
            if is_vmc_daikin:
                device_id = str(int(device_id) - 10000000)

            device = DominaDevice(
                id=device_id,
                name=device_name,
                device_type=device_type,
                maps=device_maps,
            )
            self._devices[device_id] = device

            # Create thermostat tracking object
            if device_type == DEVICE_TYPE_THERMOSTAT:
                thermo = DominaThermostat(
                    id=device_id,
                    name=device_name,
                    is_vmc_daikin=is_vmc_daikin,
                )
                self._thermostats[device_id] = thermo
                # Request thermostat status
                await self.send_command(CMD_GET_THERMOSTAT_STATUS, [device_id], [[""]])

        self._ldi_loaded = True
        await self.send_command(CMD_GET_THERMOSTAT_MODE)
        await self.send_command(CMD_GET_MARCIA_ARRESTO)
        await self.send_command(CMD_GET_NO_ACTION)
        await self.send_command(CMD_LIST_DEVICE_ADDRESSES)

        self._notify_update("ldi_loaded", {"devices": self._devices})

    async def _handle_li2(
        self, parameters: list[str], records: list[list[str]]
    ) -> None:
        """Handle LI2 (device addresses) response."""
        for record in records:
            if len(record) < 4:
                continue
            device_id = record[0]
            device = self._devices.get(device_id)
            if device:
                try:
                    device.avebus_address = int(record[3])
                except (ValueError, IndexError):
                    pass

    async def _handle_lmc(
        self, parameters: list[str], records: list[list[str]]
    ) -> None:
        """Handle LMC (list map commands) response."""
        area_id = parameters[0] if parameters else None
        area = self._areas.get(area_id) if area_id else None
        if area and not area.map_commands:
            for record in records:
                if len(record) < 16:
                    continue
                mc = DominaMapCommand(
                    command_id=record[0],
                    command_name=record[1],
                    command_type=int(record[2]),
                    x=record[3],
                    y=record[4],
                    icon_default=record[5],
                    icon_current=record[13] if len(record) > 13 else "",
                    device_id=record[14] if len(record) > 14 else "",
                    device_family=int(record[15]) if len(record) > 15 else 0,
                )
                area.map_commands.append(mc)
            self._notify_update(
                "lmc_loaded", {"area_id": area_id, "commands": area.map_commands}
            )

        self._lmc_pending -= 1
        if self._lmc_pending <= 0:
            # All map commands loaded, request device statuses
            await self.request_device_statuses()
            self._initialized.set()

    async def _handle_upd(
        self, parameters: list[str], records: list[list[str]]
    ) -> None:
        """Handle UPD (status update) messages."""
        if not parameters:
            return

        upd_type = parameters[0]

        if upd_type == UPD_DEVICE_STATUS:
            # WS: device_type, device_id, status
            if len(parameters) >= 4:
                device_type = int(parameters[1])
                device_id = parameters[2]
                device_status = int(parameters[3])
                device = self._devices.get(device_id)
                if device:
                    device.update_status(device_status)
                self._notify_update(
                    "device_status",
                    {
                        "device_id": device_id,
                        "device_type": device_type,
                        "status": device_status,
                    },
                )

        elif upd_type == UPD_THERMOSTAT:
            await self._handle_thermo_upd(parameters)

        elif upd_type == UPD_THERMOSTAT_SETPOINT:
            if len(parameters) >= 3:
                device_id = parameters[1]
                thermo = self._thermostats.get(device_id)
                if thermo:
                    thermo.update_set_point(parameters[2])
                self._notify_update(
                    "thermostat_setpoint",
                    {"device_id": device_id, "set_point": parameters[2]},
                )

        elif upd_type == UPD_THERMOSTAT_MODE:
            if len(parameters) >= 3:
                device_id = parameters[1]
                thermo = self._thermostats.get(device_id)
                if thermo:
                    thermo.mode = int(parameters[2])
                self._notify_update(
                    "thermostat_mode",
                    {"device_id": device_id, "mode": parameters[2]},
                )

        elif upd_type == UPD_THERMOSTAT_KEYBOARD_LOCK:
            if len(parameters) >= 3:
                device_id = parameters[1]
                thermo = self._thermostats.get(device_id)
                if thermo:
                    thermo.keyboard_lock = int(parameters[2])

        elif upd_type == UPD_THERMOSTAT_WINDOW:
            if len(parameters) >= 3:
                device_id = parameters[1]
                thermo = self._thermostats.get(device_id)
                if thermo:
                    thermo.window_state = int(parameters[2])

        elif upd_type == UPD_HUMIDITY:
            if len(parameters) >= 11:
                device_id = parameters[1]
                thermo = self._thermostats.get(device_id)
                if thermo:
                    thermo.humidity_value = int(parameters[2])
                    thermo.humidity_threshold_l = int(parameters[3])
                    thermo.humidity_threshold_m = int(parameters[4])
                    thermo.humidity_threshold_h = int(parameters[5])
                self._notify_update(
                    "humidity",
                    {"device_id": device_id, "humidity": int(parameters[2])},
                )

        elif upd_type == UPD_RGB:
            self._notify_update("rgb", {"parameters": parameters})

    async def _handle_thermo_upd(self, parameters: list[str]) -> None:
        """Handle WT (thermostat) sub-updates."""
        if len(parameters) < 4:
            return
        sub_type = parameters[1]
        device_id = parameters[2]
        value = parameters[3]
        thermo = self._thermostats.get(device_id)

        if sub_type == "T":  # Temperature
            if thermo:
                thermo.update_temperature(value)
            self._notify_update(
                "thermostat_temperature",
                {"device_id": device_id, "temperature": value},
            )
        elif sub_type == "S":  # Season
            if thermo:
                thermo.season = int(value)
            self._notify_update(
                "thermostat_season",
                {"device_id": device_id, "season": value},
            )
        elif sub_type == "O":  # Offset
            if thermo:
                thermo.update_offset(value)
            self._notify_update(
                "thermostat_offset",
                {"device_id": device_id, "offset": value},
            )
        elif sub_type == "L":  # Fan level
            if thermo:
                thermo.fan_level = int(value)
            self._notify_update(
                "thermostat_fan_level",
                {"device_id": device_id, "fan_level": value},
            )
        elif sub_type == "Z":  # Local OFF
            if thermo:
                thermo.local_off = int(value)
            self._notify_update(
                "thermostat_local_off",
                {"device_id": device_id, "local_off": value},
            )

    async def _handle_wts(
        self, parameters: list[str], records: list[list[str]]
    ) -> None:
        """Handle WTS (thermostat full status) response."""
        if not parameters:
            return
        device_id = parameters[0]
        thermo = self._thermostats.get(device_id)
        if thermo and records:
            thermo.update_from_wts(records)
            self._notify_update(
                "thermostat_full_status",
                {"device_id": device_id, "thermostat": thermo},
            )

    async def _handle_ping(
        self, parameters: list[str], records: list[list[str]]
    ) -> None:
        """Respond to server ping with pong."""
        await self.send_command(CMD_PONG)

    async def _handle_ack(
        self, parameters: list[str], records: list[list[str]]
    ) -> None:
        """Handle ACK - no operation needed."""
        pass

    async def _handle_gsf(
        self, parameters: list[str], records: list[list[str]]
    ) -> None:
        """Handle GSF (get sensor family) response."""
        pass

    async def wait_for_initialization(self, timeout: float = 30.0) -> bool:
        """Wait for the initial data load to complete."""
        try:
            await asyncio.wait_for(self._initialized.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
