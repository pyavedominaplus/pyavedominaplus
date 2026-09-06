"""Tests for the webserver HTTP metadata endpoints.

Parsing is unit tested against payloads shaped like the real ones; the
fetch paths are exercised over real HTTP against the mock server, because
the point of most of them is that a failure is survivable.
"""

import pytest
import pytest_asyncio

from pyavedominaplus.client import AVEDominaClient
from pyavedominaplus.webinfo import (
    SENSITIVE_KEYS,
    WebserverInfo,
    parse_reveal_code,
    parse_system_info,
)
from tests.mock_server import MOCK_REVEAL_CODE, MOCK_SYSTEM_INFO, MockDominaServer


class TestParseRevealCode:
    """revealcode.php is rooted at <devinfo>."""

    def test_parses_a_real_shaped_payload(self):
        info = parse_reveal_code(MOCK_REVEAL_CODE)
        assert info.mac_address == "aa:bb:cc:dd:ee:ff"  # lowercased
        assert info.device_type == "WBS"
        assert info.plant_code == "1a2b"
        assert info.device_version == "111-222-P84-36|5-32-38-1.5|-39-WBS"

    def test_mac_is_lowercased_and_stripped_but_not_reformatted(self):
        """Bare-hex firmwares must survive; the consumer normalizes further."""
        info = parse_reveal_code(
            "<devinfo><macaddress>  AABBCCDDEEFF </macaddress></devinfo>"
        )
        assert info.mac_address == "aabbccddeeff"

    def test_missing_element_is_none(self):
        info = parse_reveal_code("<devinfo><devtype>WBS</devtype></devinfo>")
        assert info.mac_address is None
        assert info.device_type == "WBS"

    def test_empty_element_is_none(self):
        info = parse_reveal_code("<devinfo><macaddress></macaddress></devinfo>")
        assert info.mac_address is None

    def test_malformed_xml_yields_empty(self):
        assert parse_reveal_code("<devinfo><unclosed>") == WebserverInfo()
        assert parse_reveal_code("") == WebserverInfo()
        assert parse_reveal_code("not xml at all") == WebserverInfo()


class TestParseSystemInfo:
    """systeminfo.php is a flat <root> of scalars."""

    def test_parses_every_element_the_device_reports(self):
        info = parse_system_info(MOCK_SYSTEM_INFO)
        assert len(info) == 19
        assert info["firmware"] == "164-4"
        assert info["DPServer"] == "192012-13-Jul02"
        assert info["os"] == "Linux AVE-WS 3.8.13-bone86"

    def test_values_are_stripped(self):
        """Real payloads pad values: <uptime> 77 days</uptime>."""
        assert parse_system_info(MOCK_SYSTEM_INFO)["uptime"] == "77 days"

    def test_empty_element_is_empty_string_not_none(self):
        """<dns2></dns2> parses to text None; it must not leak out as None."""
        assert parse_system_info(MOCK_SYSTEM_INFO)["dns2"] == ""

    def test_unknown_elements_are_passed_through(self):
        """Firmware may report keys this library has never seen."""
        info = parse_system_info("<root><somethingnew>42</somethingnew></root>")
        assert info == {"somethingnew": "42"}

    def test_malformed_xml_yields_empty_dict(self):
        assert parse_system_info("<root><unclosed>") == {}
        assert parse_system_info("") == {}

    def test_sensitive_keys_are_present_and_documented(self):
        """The keys a consumer must redact really do appear in the payload."""
        info = parse_system_info(MOCK_SYSTEM_INFO)
        assert SENSITIVE_KEYS - {"plantcode"} <= set(info)


@pytest_asyncio.fixture
async def server():
    s = MockDominaServer()
    await s.start()
    yield s
    await s.stop()


async def connected(server, **kwargs) -> AVEDominaClient:
    """Connect a client whose HTTP port points at the mock server."""
    client = AVEDominaClient(
        host="127.0.0.1",
        port=server.port,
        command_delay=0,
        http_port=server.port,
        **kwargs,
    )
    await client.connect()
    return client


class TestFetchOverHttp:
    """The happy path, end to end over real HTTP."""

    async def test_connect_populates_everything(self, server):
        client = await connected(server)
        try:
            assert client.connected
            assert client.mac_address == "aa:bb:cc:dd:ee:ff"
            assert client.device_type == "WBS"
            assert client.plant_code == "1a2b"
            assert client.device_version.startswith("111-222")
            assert client.system_info["firmware"] == "164-4"
        finally:
            await client.disconnect()

    async def test_system_info_is_a_copy(self, server):
        """Mutating the returned dict must not corrupt the client's state."""
        client = await connected(server)
        try:
            client.system_info["firmware"] = "tampered"
            assert client.system_info["firmware"] == "164-4"
        finally:
            await client.disconnect()

    async def test_fetch_can_be_repeated_without_reconnecting(self, server):
        client = await connected(server)
        try:
            server.systeminfo_payload = MOCK_SYSTEM_INFO.replace("164-4", "165-0")
            await client.fetch_system_info()
            assert client.system_info["firmware"] == "165-0"
        finally:
            await client.disconnect()

    async def test_values_survive_disconnect(self, server):
        """They are stable device facts, not connection state."""
        client = await connected(server)
        await client.disconnect()
        assert client.mac_address == "aa:bb:cc:dd:ee:ff"


class TestFetchFailuresAreNeverFatal:
    """Every failure mode must leave connect() successful.

    The WebSocket is the contract; the HTTP metadata is a bonus.
    """

    async def test_port_80_closed(self, server):
        """The default http_port is 80, which nothing is listening on here."""
        client = AVEDominaClient(host="127.0.0.1", port=server.port, command_delay=0)
        await client.connect()
        try:
            assert client.connected
            assert client.mac_address is None
            assert client.system_info == {}
        finally:
            await client.disconnect()

    async def test_endpoints_404(self, server):
        server.reveal_payload = None
        server.systeminfo_payload = None
        client = await connected(server)
        try:
            assert client.connected
            assert client.mac_address is None
            assert client.system_info == {}
        finally:
            await client.disconnect()

    async def test_malformed_xml(self, server):
        server.reveal_payload = "<devinfo><unclosed>"
        server.systeminfo_payload = "not xml at all"
        client = await connected(server)
        try:
            assert client.connected
            assert client.mac_address is None
            assert client.system_info == {}
        finally:
            await client.disconnect()

    async def test_missing_mac_element(self, server):
        server.reveal_payload = "<devinfo><devtype>WBS</devtype></devinfo>"
        client = await connected(server)
        try:
            assert client.connected
            assert client.mac_address is None
            assert client.device_type == "WBS"  # what did arrive is kept
        finally:
            await client.disconnect()

    async def test_timeout(self, server):
        server.http_delay = 1.0
        client = await connected(server, http_timeout=0.1)
        try:
            assert client.connected
            assert client.mac_address is None
            assert client.system_info == {}
        finally:
            await client.disconnect()

    async def test_one_endpoint_failing_does_not_blank_the_other(self, server):
        """The two endpoints are fetched independently."""
        server.systeminfo_payload = None
        client = await connected(server)
        try:
            assert client.mac_address == "aa:bb:cc:dd:ee:ff"
            assert client.system_info == {}
        finally:
            await client.disconnect()

    async def test_fetch_without_a_session_uses_a_temporary_one(self, server):
        """fetch_system_info() before connect() still works."""
        client = AVEDominaClient(
            host="127.0.0.1", port=server.port, command_delay=0, http_port=server.port
        )
        await client.fetch_system_info()
        assert client.mac_address == "aa:bb:cc:dd:ee:ff"

    async def test_ws_failure_still_raises(self, server):
        """The HTTP work must not swallow a real WebSocket failure."""
        client = AVEDominaClient(
            host="127.0.0.1", port=1, command_delay=0, connect_timeout=1.0
        )
        with pytest.raises(ConnectionError):
            await client.connect()
