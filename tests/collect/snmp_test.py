"""Tests for the SNMP layer"""

import pytest
from zino.oid import OID
from zino.snmp.base import Identifier, MibObject
from zino.snmp.base import SnmpError as ZinoSnmpError

from tests.conftest import CISCO_DESCR, CISCO_OID, JUNIPER_OID
from vwatcher.collect import snmp
from vwatcher.collect.snmp import SnmpError, SystemInfo, ZinoSession, load_backend, open_session


class FakeZinoSession:
    def __init__(self):
        self.values = []
        self.error = None
        self.requested = []

    async def get(self, *mib_object):
        self.requested.append((mib_object,))
        self._raise()
        return MibObject(oid=OID(".1.3.6.1.2.1.1.3.0"), value=self.values[0])

    async def get2(self, *mib_objects):
        self.requested.append(mib_objects)
        self._raise()
        return [(Identifier(*variable[:2], OID(".0")), value) for variable, value in zip(mib_objects, self.values)]

    def _raise(self):
        if self.error:
            raise self.error


@pytest.fixture
def zino_session(monkeypatch) -> FakeZinoSession:
    session = FakeZinoSession()
    monkeypatch.setattr(snmp, "get_snmp_session", lambda device: session)
    return session


@pytest.fixture
def polled(monkeypatch) -> list:
    devices = []
    monkeypatch.setattr(snmp, "get_snmp_session", lambda device: devices.append(device))
    return devices


class TestSystemInfo:
    @pytest.mark.parametrize(
        "object_id, is_cisco",
        [
            pytest.param(CISCO_OID, True, id="cisco"),
            pytest.param(JUNIPER_OID, False, id="another vendor"),
            pytest.param("who knows", False, id="not even an OID"),
        ],
    )
    def test_should_recognize_cisco_gear_by_its_object_id(self, object_id, is_cisco):
        assert SystemInfo(object_id=object_id, descr="").is_cisco is is_cisco


class TestOpenSession:
    def test_should_wrap_the_session_zino_hands_out(self, device, zino_session):
        session = open_session(device)

        assert isinstance(session, ZinoSession)
        assert session.session is zino_session

    def test_should_poll_a_device_with_high_capacity_counters_as_configured(self, device, polled):
        open_session(device)

        assert polled[0].snmpversion == "v2c"

    def test_when_high_capacity_counters_are_off_then_it_should_fall_back_to_v1(self, device, polled):
        # Old zino only used hcounters to derive versions
        open_session(device.model_copy(update={"hcounters": False}))

        assert polled[0].snmpversion == "v1"


class TestLoadBackend:
    def test_should_import_and_initialize_the_named_backend(self, monkeypatch):
        loaded = []
        monkeypatch.setattr(
            snmp,
            "import_snmp_backend",
            lambda backend: type("Backend", (), {"init_backend": staticmethod(lambda: loaded.append(backend))}),
        )

        load_backend("pysnmp")

        assert loaded == ["pysnmp"]


class TestZinoSession:
    async def test_should_return_the_uptime_as_an_integer(self, zino_session):
        zino_session.values = [360000]

        assert await ZinoSession(zino_session).get_uptime() == 360000

    async def test_should_return_the_system_information(self, zino_session):
        zino_session.values = [OID(f".{CISCO_OID}"), CISCO_DESCR]

        assert await ZinoSession(zino_session).get_system() == SystemInfo(object_id=f".{CISCO_OID}", descr=CISCO_DESCR)

    async def test_should_return_the_restart_reason(self, zino_session):
        zino_session.values = ["power-on"]

        assert await ZinoSession(zino_session).get_why_reload() == "power-on"

    async def test_should_ask_for_two_objects_at_once(self, zino_session):
        zino_session.values = [OID(f".{CISCO_OID}"), CISCO_DESCR]
        await ZinoSession(zino_session).get_system()

        assert len(zino_session.requested[0]) == 2

    async def test_should_decode_a_string_a_backend_hands_over_as_bytes(self, zino_session):
        # The netsnmp backend does not decode OctetStrings, PySNMP does"""
        zino_session.values = [OID(f".{CISCO_OID}"), CISCO_DESCR.encode()]

        assert (await ZinoSession(zino_session).get_system()).descr == CISCO_DESCR

    async def test_when_a_string_cannot_be_decoded_then_it_should_be_replaced(self, zino_session):
        zino_session.values = [b"reloaded by \xff"]

        assert await ZinoSession(zino_session).get_why_reload() == "reloaded by \ufffd"

    async def test_should_raise_what_the_backend_raised(self, zino_session):
        zino_session.error = ZinoSnmpError("Could not find object at .1.3.6.1.2.1.1.3.0")

        with pytest.raises(SnmpError, match="Could not find object"):
            await ZinoSession(zino_session).get_uptime()

    async def test_should_raise_a_timeout_as_an_snmp_error(self, zino_session):
        # Zino raises the built-in TimeoutError, which is not an SnmpError
        zino_session.error = TimeoutError("No SNMP response received before timeout")

        with pytest.raises(SnmpError, match="No SNMP response"):
            await ZinoSession(zino_session).get_uptime()

    async def test_when_an_error_carries_no_message_then_it_should_be_named(self, zino_session):
        zino_session.error = TimeoutError()

        with pytest.raises(SnmpError, match="TimeoutError"):
            await ZinoSession(zino_session).get_system()
