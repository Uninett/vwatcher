"""
The narrow SNMP interface vwatcher needs, building on top of Zino's SNMP backends.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from zino.config.models import PollDevice
from zino.oid import OID
from zino.snmp import get_snmp_session, import_snmp_backend
from zino.snmp.base import SnmpError as ZinoSnmpError

if TYPE_CHECKING:
    from zino.snmp.pysnmp_backend import SNMP

# Cisco's arc of the enterprises tree, which its `sysObjectID` starts with
CISCO_ENTERPRISE = OID(".1.3.6.1.4.1.9")


def load_backend(backend: Optional[str] = None) -> None:
    """Load the Zino SNMP backend every session will use, once per process"""
    import_snmp_backend(backend).init_backend()


class SnmpError(Exception):
    """An SNMP request failed. Message ends up verbatim in the event log."""


@dataclass
class SystemInfo:
    object_id: str
    descr: str

    @property
    def is_cisco(self) -> bool:
        try:
            return CISCO_ENTERPRISE.is_a_prefix_of(self.object_id)
        except (TypeError, ValueError):
            # Device may not answer with an OID
            return False


class ZinoSession:
    """The SNMP operations a poll task needs, answered by one of Zino's backends"""

    def __init__(self, session: "SNMP"):
        self.session = session

    async def get_uptime(self) -> int:
        """Return `sysUpTime` in centiseconds"""
        response = await _as_snmp_error(self.session.get("SNMPv2-MIB", "sysUpTime", 0))
        return int(response.value)

    async def get_system(self) -> SystemInfo:
        """Return `sysObjectID` and `sysDescr`"""
        (_, object_id), (_, descr) = await _as_snmp_error(
            self.session.get2(("SNMPv2-MIB", "sysObjectID", 0), ("SNMPv2-MIB", "sysDescr", 0))
        )
        return SystemInfo(object_id=str(object_id), descr=_decoded_text(descr))

    async def get_why_reload(self) -> str:
        """Return the Cisco restart reason"""
        response = await _as_snmp_error(self.session.get("OLD-CISCO-SYSTEM-MIB", "whyReload", 0))
        return _decoded_text(response.value)


def open_session(device: PollDevice) -> ZinoSession:
    return ZinoSession(get_snmp_session(device=_polling_device(device)))


def _polling_device(device: PollDevice) -> PollDevice:
    """
    Adapt a device to what Zino's SNMP layer should see.

    Zino honours `snmpversion` and ignores `hcounters`; the Tcl `vwatch`
    derived the version from `hcounters` alone, and old gear still needs that.
    """
    if device.hcounters:
        return device
    return device.model_copy(update={"snmpversion": "v1"})


def _decoded_text(value) -> str:
    """
    Decode a string a device answered with.

    Zino's PySNMP backend decodes an OctetString, the netsnmp one does not.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


async def _as_snmp_error(request):
    """
    Await a Zino request, raising what it fails with as `SnmpError`.

    Assume timeout results in an OSError
    """
    try:
        return await request
    except (ZinoSnmpError, OSError) as error:
        raise SnmpError(str(error) or type(error).__name__) from error
