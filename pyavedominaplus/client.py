"""Async WebSocket client for AVE DominaPlus."""

import asyncio
import logging
from typing import Any, Callable

import aiohttp

from .const import (
    CMD_EXECUTE_SCENARIO,
    CMD_GET_DEVICE_STATUS_FAMILY,
    CMD_GET_MARCIA_ARRESTO,
    CMD_GET_NO_ACTION,
    CMD_GET_THERMOSTAT_MODE,
    CMD_GET_THERMOSTAT_STATUS,
    CMD_LIGHT_COMMAND,
    CMD_LIST_DEVICES,
    CMD_LIST_DEVICE_ADDRESSES,
    CMD_LIST_MAP_COMMANDS,
    CMD_LIST_MAP_LABELS,
    CMD_LIST_MAPS,
    CMD_PONG,
    CMD_SET_DIMMER_LEVEL,
    CMD_SET_THERMOSTAT_STATUS,
    CMD_SHUTTER_COMMAND,
    CMD_SUBSCRIBE_UPDATES_2,
    CMD_SUBSCRIBE_UPDATES_3,
    CMD_THERMOSTAT_KEYBOARD_LOCK,
    CMD_THERMOSTAT_SET_OFF,
    CMD_THERMOSTAT_SET_OFF_TS01,
    CONN_STATUS_CLOSE,
    CONN_STATUS_ERROR,
    CONN_STATUS_OPEN,
    DEFAULT_WS_PORT,
    DEVICE_TYPE_THERMOSTAT,
    DIMMER_CMD_OFF,
    DIMMER_CMD_ON,
    DIMMER_CMD_STEP,
    LIGHT_CMD_OFF,
    LIGHT_CMD_ON,
    LIGHT_CMD_TOGGLE,
    SHUTTER_CMD_CLOSE,
    SHUTTER_CMD_OPEN,
    THERMOSTAT_MODE_AUTO,
    THERMOSTAT_MODE_MANUAL,
    UPD_DEVICE_STATUS,
    UPD_HUMIDITY,
    UPD_RGB,
    UPD_THERMOSTAT,
    UPD_THERMOSTAT_FANLEVEL_MAP,
    UPD_THERMOSTAT_KEYBOARD_LOCK,
    UPD_THERMOSTAT_LOCAL_OFF_MAP,
    UPD_THERMOSTAT_MODE,
    UPD_THERMOSTAT_OFFSET_MAP,
    UPD_THERMOSTAT_SEASON_MAP,
    UPD_THERMOSTAT_SETPOINT,
    UPD_THERMOSTAT_TEMP_MAP,
    UPD_THERMOSTAT_FUNCTION,
    UPD_THERMOSTAT_REQUEST,
    UPD_THERMOSTAT_WINDOW,
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
        auto_reconnect: bool = True,
        reconnect_interval: float = 5.0,
        max_reconnect_interval: float = 300.0,
        command_delay: float = 0.3,
    ) -> None:
        self.host = host
        self.port = port
        self._session = session
        self._owns_session = session is None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._running = False
        self._listen_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._auto_reconnect = auto_reconnect
        self._reconnect_interval = reconnect_interval
        self._max_reconnect_interval = max_reconnect_interval
        self._command_delay = command_delay
        self._devices: dict[str, DominaDevice] = {}
        self._thermostats: dict[str, DominaThermostat] = {}
        self._areas: dict[str, DominaArea] = {}
        self._update_callbacks: list[UpdateCallback] = []
        self._connection_status_callbacks: list[Callable[[str], None]] = []
        self._initialized = asyncio.Event()
        self._lm_loaded = False
        self._ldi_loaded = False
        self._lmc_pending = 0
        self._pending_devices: set[str] = set()

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
        _LOGGER.debug("Connected successfully")
        self._running = True
        self._notify_connection(CONN_STATUS_OPEN)
        self._listen_task = asyncio.ensure_future(self._listen_loop())

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        _LOGGER.debug("Disconnecting from %s", self.url)
        self._running = False
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None
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
        _LOGGER.debug("Disconnected")
        self._notify_connection(CONN_STATUS_CLOSE)

    def _reset_init_state(self) -> None:
        """Reset initialization flags so initialize() works after reconnect."""
        self._initialized.clear()
        self._lm_loaded = False
        self._ldi_loaded = False
        self._lmc_pending = 0
        self._pending_devices = set()

    async def _reconnect_loop(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        delay = self._reconnect_interval
        while self._running:
            _LOGGER.info("Reconnecting in %.1fs...", delay)
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            if not self._running:
                break
            try:
                # Clean up stale connection
                if self._ws and not self._ws.closed:
                    await self._ws.close()
                self._ws = None
                if self._owns_session:
                    if self._session and not self._session.closed:
                        await self._session.close()
                    self._session = aiohttp.ClientSession()
                self._reset_init_state()
                self._ws = await self._session.ws_connect(
                    self.url,
                    protocols=["binary", "base64"],
                    timeout=aiohttp.ClientWSTimeout(ws_close=10.0),
                )
                _LOGGER.info("Reconnected to %s", self.url)
                self._notify_connection(CONN_STATUS_OPEN)
                self._listen_task = asyncio.ensure_future(self._listen_loop())
                await self.initialize()
                return
            except asyncio.CancelledError:
                return
            except Exception:
                _LOGGER.warning(
                    "Reconnect failed, retrying in %.1fs...",
                    min(delay * 2, self._max_reconnect_interval),
                    exc_info=True,
                )
                delay = min(delay * 2, self._max_reconnect_interval)

    async def send_command(
        self,
        command: str,
        parameters: list[str] | None = None,
        records: list[list[str]] | None = None,
    ) -> None:
        """Send a command to the server.

        If auto-reconnect is enabled and the connection is down, waits up to
        30 seconds for reconnection before raising ConnectionError.
        """
        if not self.connected:
            if self._auto_reconnect and self._running:
                _LOGGER.debug(
                    "Not connected, waiting for reconnect before sending %s",
                    command,
                )
                for _ in range(60):
                    await asyncio.sleep(0.5)
                    if self.connected:
                        break
                    if not self._running:
                        break
            if not self.connected:
                raise ConnectionError("Not connected to DominaPlus server")
        msg = encode_message(command, parameters, records)
        _LOGGER.debug(
            "Sending command: %s, params=%s, data=%s",
            command,
            parameters or [],
            msg.hex(),
        )
        await self._ws.send_bytes(msg)

    async def initialize(self) -> None:
        """Request initial data load (areas, devices)."""
        await self.send_command(CMD_LIST_MAPS)
        await self.send_command(CMD_LIST_DEVICES)

    async def request_device_statuses(self) -> None:
        """Request current status for all device families.

        Commands are staggered with short delays to avoid overwhelming
        the server (mirrors the original AVE SDK behaviour).
        """
        families = ["1", "2", "22", "9", "3", "16", "19", "6"]
        await self.send_command(CMD_SUBSCRIBE_UPDATES_2)
        if self._command_delay:
            await asyncio.sleep(self._command_delay)
        await self.send_command(CMD_SUBSCRIBE_UPDATES_3)
        for family in families:
            if self._command_delay:
                await asyncio.sleep(self._command_delay)
            await self.send_command(CMD_GET_DEVICE_STATUS_FAMILY, [family])

    async def turn_on_light(self, device_id: str) -> None:
        """Turn on a light or energy device."""
        await self.send_command(CMD_LIGHT_COMMAND, [device_id, LIGHT_CMD_ON])

    async def turn_off_light(self, device_id: str) -> None:
        """Turn off a light or energy device."""
        await self.send_command(CMD_LIGHT_COMMAND, [device_id, LIGHT_CMD_OFF])

    async def toggle_light(self, device_id: str) -> None:
        """Toggle a light or energy device on/off."""
        await self.send_command(CMD_LIGHT_COMMAND, [device_id, LIGHT_CMD_TOGGLE])

    async def turn_on_dimmer(self, device_id: str) -> None:
        """Turn on a dimmer.

        Dimmers use different EBI sub-commands than regular lights:
        "3" for on instead of "11".
        """
        await self.send_command(CMD_LIGHT_COMMAND, [device_id, DIMMER_CMD_ON])

    async def turn_off_dimmer(self, device_id: str) -> None:
        """Turn off a dimmer.

        Dimmers use different EBI sub-commands than regular lights:
        "4" for off instead of "12".
        """
        await self.send_command(CMD_LIGHT_COMMAND, [device_id, DIMMER_CMD_OFF])

    async def set_dimmer_level(self, device_id: str, level: int) -> None:
        """Set a dimmer to a specific level (0-31)."""
        level = max(0, min(31, level))
        await self.send_command(CMD_SET_DIMMER_LEVEL, [device_id], [[str(level)]])

    async def step_dimmer(self, device_id: str) -> None:
        """Step a dimmer (toggle on/off)."""
        await self.send_command(CMD_LIGHT_COMMAND, [device_id, DIMMER_CMD_STEP])

    async def open_shutter(self, device_id: str) -> None:
        """Open/raise a shutter."""
        await self.send_command(CMD_SHUTTER_COMMAND, [device_id, SHUTTER_CMD_OPEN])

    async def close_shutter(self, device_id: str) -> None:
        """Close/lower a shutter."""
        await self.send_command(CMD_SHUTTER_COMMAND, [device_id, SHUTTER_CMD_CLOSE])

    async def stop_shutter(self, device_id: str) -> None:
        """Stop a shutter mid-movement.

        Re-sends the current direction command which causes the motor to stop.
        The device will report status 5 (stopped/partially open).
        """
        device = self._devices.get(device_id)
        if not device:
            return
        if device.is_opening:
            await self.send_command(CMD_SHUTTER_COMMAND, [device_id, SHUTTER_CMD_OPEN])
        elif device.is_closing:
            await self.send_command(CMD_SHUTTER_COMMAND, [device_id, SHUTTER_CMD_CLOSE])

    async def activate_scenario(self, device_id: str) -> None:
        """Activate a scenario by finding its map command and executing it."""
        for area in self._areas.values():
            for cmd in area.map_commands:
                if cmd.device_id == device_id and cmd.command_type == 17:
                    await self.send_command(CMD_EXECUTE_SCENARIO, [cmd.command_id])
                    return
        # Fallback: try device_id directly as command_id
        await self.send_command(CMD_EXECUTE_SCENARIO, [device_id])

    def _thermostat_off_cmd(self, device_id: str) -> str:
        """Return TOO or TUU depending on thermostat type."""
        thermo = self._thermostats.get(device_id)
        if thermo and thermo.is_vmc_daikin:
            return CMD_THERMOSTAT_SET_OFF_TS01
        return CMD_THERMOSTAT_SET_OFF

    async def toggle_thermostat_local_off(self, device_id: str) -> None:
        """Toggle a thermostat's local off state (on <-> off).

        Sends the current local_off value; the server inverts it.
        Uses TUU for TS01 thermostats, TOO for standard ones.
        """
        thermo = self._thermostats.get(device_id)
        if not thermo:
            return
        await self.send_command(
            self._thermostat_off_cmd(device_id),
            [device_id, str(thermo.local_off)],
        )

    async def turn_on_thermostat(self, device_id: str) -> None:
        """Turn on a thermostat (clear local off state).

        The TOO/TUU protocol inverts the sent value. Sending "1" always
        results in local_off=0 (ON), regardless of the current state.
        This avoids issues with stale cached state.
        """
        await self.send_command(self._thermostat_off_cmd(device_id), [device_id, "1"])

    async def turn_off_thermostat(self, device_id: str) -> None:
        """Turn off a thermostat (set local off state).

        The TOO/TUU protocol inverts the sent value. Sending "0" always
        results in local_off=1 (OFF), regardless of the current state.
        This avoids issues with stale cached state.
        """
        await self.send_command(self._thermostat_off_cmd(device_id), [device_id, "0"])

    async def toggle_thermostat_keyboard_lock(self, device_id: str) -> None:
        """Toggle a thermostat's keyboard lock."""
        await self.send_command(CMD_THERMOSTAT_KEYBOARD_LOCK, [device_id])

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
            [[str(thermo.season), str(thermo.mode), str(raw_sp)]],
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

    async def set_thermostat_mode(self, device_id: str, mode: int) -> None:
        """Set a thermostat's mode.

        mode: 0 = automatic (follows built-in schedule),
              1 = manual (user-set temperature).
        Sends STS with the current season and set point.
        When switching to manual, uses the saved manual setpoint rather than
        the current set_point (which may be the auto-schedule value).
        """
        thermo = self._thermostats.get(device_id)
        if not thermo:
            return
        if mode == THERMOSTAT_MODE_MANUAL and thermo.manual_set_point > 0:
            raw_sp = int(thermo.manual_set_point * 10)
        else:
            raw_sp = int(thermo.set_point * 10)
        await self.send_command(
            CMD_SET_THERMOSTAT_STATUS,
            [device_id],
            [[str(thermo.season), str(mode), str(raw_sp)]],
        )

    async def _listen_loop(self) -> None:
        """Main loop for receiving messages from the server."""
        try:
            async for ws_msg in self._ws:
                if not self._running:
                    break
                if ws_msg.type == aiohttp.WSMsgType.BINARY:
                    raw = ws_msg.data
                    _LOGGER.debug("Received binary: %s", raw.hex())
                elif ws_msg.type == aiohttp.WSMsgType.TEXT:
                    raw = ws_msg.data.encode("utf-8")
                    _LOGGER.debug("Received text: %s", ws_msg.data)
                elif ws_msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.ERROR,
                ):
                    _LOGGER.debug("WebSocket closed or error: %s", ws_msg.type)
                    break
                else:
                    _LOGGER.debug("Ignoring message type: %s", ws_msg.type)
                    continue
                try:
                    messages = decode_message(raw)
                    _LOGGER.debug("Decoded %d message(s)", len(messages))
                    for msg in messages:
                        await self._handle_message(msg)
                except (ConnectionError, OSError):
                    _LOGGER.warning("Connection lost during message handling")
                    break
                except Exception:
                    _LOGGER.exception("Error processing message")
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Error in listen loop")
        finally:
            if self._running:
                self._notify_connection(CONN_STATUS_ERROR)
                if self._auto_reconnect:
                    self._reconnect_task = asyncio.ensure_future(self._reconnect_loop())

    async def _handle_message(self, msg: dict) -> None:
        """Handle a decoded protocol message."""
        command = msg["command"]
        parameters = msg["parameters"]
        records = msg["records"]

        _LOGGER.debug(
            "Handling message: command=%s, params=%s, records=%d",
            command,
            parameters,
            len(records),
        )

        handler = {
            "lm": self._handle_lm,
            "ldi": self._handle_ldi,
            "li2": self._handle_li2,
            "lmc": self._handle_lmc,
            "lml": self._handle_lml,
            "upd": self._handle_upd,
            "wts": self._handle_wts,
            "ping": self._handle_ping,
            "ack": self._handle_ack,
            "gsf": self._handle_gsf,
            "net": self._handle_net,
        }.get(command)

        if handler:
            await handler(parameters, records)
        else:
            _LOGGER.debug("No handler for command: %s", command)

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
                await self.send_command(CMD_LIST_MAP_LABELS, [area.id])
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
        # Track devices that will receive status updates.
        # WSF families produce UPD WS; thermostats resolve via WTS.
        wsf_families = {1, 2, 3, 6, 9, 16, 19, 22}
        self._pending_devices = {
            did
            for did, dev in self._devices.items()
            if dev.device_type in wsf_families
            or dev.device_type == DEVICE_TYPE_THERMOSTAT
        }
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
            # If no devices to track, mark initialized now
            if not self._pending_devices:
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
                self._pending_devices.discard(device_id)
                if not self._pending_devices and not self._initialized.is_set():
                    self._initialized.set()
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
                    if thermo.is_manual_mode:
                        thermo.manual_set_point = thermo.set_point
                self._notify_update(
                    "thermostat_setpoint",
                    {"device_id": device_id, "set_point": parameters[2]},
                )

        elif upd_type == UPD_THERMOSTAT_MODE:
            if len(parameters) >= 3:
                device_id = parameters[1]
                raw_mode = parameters[2]
                # TM sends mode as "M" (manual=1) or "A" (auto=0)
                if raw_mode == "M":
                    mode_int = THERMOSTAT_MODE_MANUAL
                elif raw_mode == "A":
                    mode_int = THERMOSTAT_MODE_AUTO
                else:
                    mode_int = int(raw_mode)
                thermo = self._thermostats.get(device_id)
                if thermo:
                    thermo.mode = mode_int
                self._notify_update(
                    "thermostat_mode",
                    {"device_id": device_id, "mode": mode_int},
                )

        elif upd_type == UPD_THERMOSTAT_KEYBOARD_LOCK:
            if len(parameters) >= 3:
                device_id = parameters[1]
                thermo = self._thermostats.get(device_id)
                if thermo:
                    thermo.keyboard_lock = int(parameters[2])
                self._notify_update(
                    "thermostat_keyboard_lock",
                    {"device_id": device_id, "keyboard_lock": parameters[2]},
                )

        elif upd_type == UPD_THERMOSTAT_WINDOW:
            if len(parameters) >= 3:
                device_id = parameters[1]
                thermo = self._thermostats.get(device_id)
                if thermo:
                    thermo.window_state = int(parameters[2])
                self._notify_update(
                    "thermostat_window",
                    {"device_id": device_id, "window_state": parameters[2]},
                )

        elif upd_type == UPD_HUMIDITY:
            if len(parameters) >= 6:
                device_id = parameters[1]
                thermo = self._thermostats.get(device_id)
                if thermo:
                    thermo.humidity_value = int(parameters[2])
                    # Only enable humidity if a non-zero value is reported.
                    # UMI messages are sent for all thermostats, but those
                    # without a probe always report 0.
                    if thermo.humidity_value > 0:
                        thermo.humidity_enabled = True
                    if len(parameters) >= 11:
                        thermo.humidity_threshold_l = int(parameters[3])
                        thermo.humidity_threshold_m = int(parameters[4])
                        thermo.humidity_threshold_h = int(parameters[5])
                self._notify_update(
                    "humidity",
                    {"device_id": device_id, "humidity": int(parameters[2])},
                )

        elif upd_type == UPD_THERMOSTAT_LOCAL_OFF_MAP:
            # TLO: thermostat local off from map command
            # Format: TLO, map_command_id, value (INVERTED: 0->1, 1->0)
            if len(parameters) >= 3:
                device_id = self._resolve_device_id(parameters[1])
                if device_id:
                    # Value is inverted compared to WT/Z
                    raw = int(parameters[2])
                    local_off = 0 if raw else 1
                    thermo = self._thermostats.get(device_id)
                    if thermo:
                        thermo.local_off = local_off
                    self._notify_update(
                        "thermostat_local_off",
                        {"device_id": device_id, "local_off": str(local_off)},
                    )

        elif upd_type == UPD_THERMOSTAT_SEASON_MAP:
            # TS: thermostat season from map command
            if len(parameters) >= 3:
                device_id = self._resolve_device_id(parameters[1])
                if device_id:
                    thermo = self._thermostats.get(device_id)
                    if thermo:
                        thermo.season = int(parameters[2])
                    self._notify_update(
                        "thermostat_season",
                        {"device_id": device_id, "season": parameters[2]},
                    )

        elif upd_type == UPD_THERMOSTAT_TEMP_MAP:
            # TT: thermostat temperature from map command
            if len(parameters) >= 3:
                device_id = self._resolve_device_id(parameters[1])
                if device_id:
                    thermo = self._thermostats.get(device_id)
                    if thermo:
                        thermo.update_temperature(parameters[2])
                    self._notify_update(
                        "thermostat_temperature",
                        {"device_id": device_id, "temperature": parameters[2]},
                    )

        elif upd_type == UPD_THERMOSTAT_OFFSET_MAP:
            # TO: thermostat offset from map command
            if len(parameters) >= 3:
                device_id = self._resolve_device_id(parameters[1])
                if device_id:
                    thermo = self._thermostats.get(device_id)
                    if thermo:
                        thermo.update_offset(parameters[2])
                    self._notify_update(
                        "thermostat_offset",
                        {"device_id": device_id, "offset": parameters[2]},
                    )

        elif upd_type == UPD_THERMOSTAT_FANLEVEL_MAP:
            # TL: thermostat fan level from map command
            if len(parameters) >= 3:
                device_id = self._resolve_device_id(parameters[1])
                if device_id:
                    thermo = self._thermostats.get(device_id)
                    if thermo:
                        thermo.fan_level = int(parameters[2])
                    self._notify_update(
                        "thermostat_fan_level",
                        {"device_id": device_id, "fan_level": parameters[2]},
                    )

        elif upd_type == UPD_THERMOSTAT_FUNCTION:
            # TF: thermostat function/scheduling update
            # Format: TF, device_id, value, ...
            if len(parameters) >= 3:
                device_id = parameters[1]
                self._notify_update(
                    "thermostat_function",
                    {"device_id": device_id, "parameters": parameters[2:]},
                )

        elif upd_type == UPD_THERMOSTAT_REQUEST:
            # TR: thermostat request
            if len(parameters) >= 3:
                device_id = parameters[1]
                self._notify_update(
                    "thermostat_request",
                    {"device_id": device_id, "value": parameters[2]},
                )

        elif upd_type == UPD_RGB:
            self._notify_update("rgb", {"parameters": parameters})

    def _resolve_device_id(self, id_or_map_cmd: str) -> str | None:
        """Resolve a device_id from either a direct device ID or a map command ID.

        Some UPD updates (TLO, TS, TT, TO, TL) may reference either a device ID
        directly or a map command ID. This tries direct device lookup first,
        then falls back to map command lookup.
        """
        if id_or_map_cmd in self._thermostats:
            return id_or_map_cmd
        if id_or_map_cmd in self._devices:
            return id_or_map_cmd
        for area in self._areas.values():
            for cmd in area.map_commands:
                if cmd.command_id == id_or_map_cmd:
                    return cmd.device_id
        return None

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
            self._pending_devices.discard(device_id)
            if not self._pending_devices and not self._initialized.is_set():
                self._initialized.set()
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

    async def _handle_lml(
        self, parameters: list[str], records: list[list[str]]
    ) -> None:
        """Handle LML (list map labels) response - no action needed."""
        pass

    async def _handle_gsf(
        self, parameters: list[str], records: list[list[str]]
    ) -> None:
        """Handle GSF (get sensor family) response."""
        pass

    async def _handle_net(
        self, parameters: list[str], records: list[list[str]]
    ) -> None:
        """Handle NET (network status) messages - no action needed."""
        pass

    async def wait_for_initialization(self, timeout: float = 30.0) -> bool:
        """Wait for the initial data load to complete."""
        try:
            await asyncio.wait_for(self._initialized.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
