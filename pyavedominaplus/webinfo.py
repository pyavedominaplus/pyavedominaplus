"""Read device metadata from the DominaPlus webserver's HTTP endpoints.

The webserver serves plain HTTP on port 80 alongside the WebSocket protocol
on 14001. Two endpoints carry information the WebSocket protocol does not
expose at all:

``/revealcode.php``::

    <devinfo>
      <devtype>WBS</devtype>
      <macaddress>60:e8:5b:16:e2:47</macaddress>
      <plantcode>37a9</plantcode>
      <version>526-421-P84-36|5-32-38-...|-39-WBS</version>
    </devinfo>

``/systeminfo.php`` returns a flat ``<root>`` of scalar elements: dhcp,
remotesupport, ipaddress, subnet, gateway, dns1, dns2, uptime, memory, cf,
temperature, os, app, launcher, DPServer, DPClient, firmware, cloud, iot.

The MAC matters because it is the only stable identifier the device offers:
anything keyed on the IP address regenerates when the DHCP lease changes.

Everything here is best effort. The WebSocket is the contract; this is a
bonus, so a closed port 80, a 404, malformed XML or a timeout all resolve to
"unknown" rather than an error.

Note on privacy: systeminfo.php reports the device's network configuration
(``ipaddress``, ``subnet``, ``gateway``, ``dns1``, ``dns2``) and revealcode
reports an installation identifier (``plantcode``). Consumers that publish
this - Home Assistant diagnostics, bug reports - should redact those keys.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from xml.etree.ElementTree import Element, ParseError

import aiohttp
from defusedxml.ElementTree import fromstring

_LOGGER = logging.getLogger(__name__)

REVEAL_CODE_PATH = "/revealcode.php"
SYSTEM_INFO_PATH = "/systeminfo.php"

DEFAULT_HTTP_PORT = 80
DEFAULT_HTTP_TIMEOUT = 5.0

#: Keys in system_info that describe the network the device sits on, plus the
#: installation identifier. Consumers publishing this data should redact them.
SENSITIVE_KEYS = frozenset(
    {"ipaddress", "subnet", "gateway", "dns1", "dns2", "plantcode"}
)


@dataclass(frozen=True)
class WebserverInfo:
    """Metadata read from the webserver's HTTP endpoints.

    Every field is optional: each endpoint is fetched independently, so one
    failing does not blank the other.
    """

    mac_address: str | None = None
    device_type: str | None = None
    plant_code: str | None = None
    device_version: str | None = None
    system_info: dict[str, str] = field(default_factory=dict)


def _text(root: Element, tag: str) -> str | None:
    """Return a child element's stripped text, or None if absent or empty.

    Real payloads pad values (``<uptime> 77 days</uptime>``) and use empty
    elements for unset fields (``<dns2></dns2>``, whose text is None).
    """
    element = root.find(tag)
    if element is None:
        return None
    value = (element.text or "").strip()
    return value or None


def parse_reveal_code(xml: str) -> WebserverInfo:
    """Parse a /revealcode.php response.

    Returns an empty WebserverInfo if the document cannot be parsed; missing
    elements are left as None.
    """
    try:
        root = fromstring(xml)
    except ParseError:
        _LOGGER.debug("revealcode.php returned unparseable XML")
        return WebserverInfo()
    mac = _text(root, "macaddress")
    return WebserverInfo(
        mac_address=mac.lower() if mac else None,
        device_type=_text(root, "devtype"),
        plant_code=_text(root, "plantcode"),
        device_version=_text(root, "version"),
    )


def parse_system_info(xml: str) -> dict[str, str]:
    """Parse a /systeminfo.php response into a flat dict.

    Every child element of the root is included, so firmware revisions the
    library does not know about still reach the consumer. Empty elements map
    to "" rather than being dropped.
    """
    try:
        root = fromstring(xml)
    except ParseError:
        _LOGGER.debug("systeminfo.php returned unparseable XML")
        return {}
    return {child.tag: (child.text or "").strip() for child in root}


async def _get(session: aiohttp.ClientSession, url: str, timeout: float) -> str | None:
    """GET a URL, returning its body, or None for any failure.

    The endpoints answer with Content-Type text/html rather than an XML
    type, so the content type is deliberately not checked.
    """
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as response:
            if response.status != 200:
                _LOGGER.debug("%s returned HTTP %s", url, response.status)
                return None
            return await response.text()
    except (TimeoutError, aiohttp.ClientError, OSError) as err:
        _LOGGER.debug("Could not read %s: %s", url, err)
        return None


async def fetch_webserver_info(
    session: aiohttp.ClientSession,
    host: str,
    port: int = DEFAULT_HTTP_PORT,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
) -> WebserverInfo:
    """Read both HTTP endpoints, tolerating any failure.

    Never raises: a device with port 80 closed simply yields a
    WebserverInfo with everything unset.
    """
    base = f"http://{host}" if port == DEFAULT_HTTP_PORT else f"http://{host}:{port}"

    reveal_body = await _get(session, f"{base}{REVEAL_CODE_PATH}", timeout)
    info = (
        parse_reveal_code(reveal_body) if reveal_body is not None else WebserverInfo()
    )

    system_body = await _get(session, f"{base}{SYSTEM_INFO_PATH}", timeout)
    system_info = parse_system_info(system_body) if system_body is not None else {}

    return WebserverInfo(
        mac_address=info.mac_address,
        device_type=info.device_type,
        plant_code=info.plant_code,
        device_version=info.device_version,
        system_info=system_info,
    )
