"""Tests of the SNMP layer against a simulated agent"""

from types import ModuleType

import pytest
from zino.config.models import PollDevice

from tests.conftest import BACKENDS
from vwatcher.collect.snmp import SnmpError, ZinoSession


def _session(backend: ModuleType, community: str, port: int, **timeouts) -> ZinoSession:
    device = PollDevice(name=community, address="127.0.0.1", port=port, community=community, **timeouts)
    return ZinoSession(backend.SNMP(device))


@pytest.mark.parametrize("backend", BACKENDS, indirect=True)
class TestAgainstSimulatedAgent:
    async def test_should_read_the_uptime_in_centiseconds(self, backend, snmpsim, snmp_port):
        assert await _session(backend, "cisco-router", snmp_port).get_uptime() == 360000

    async def test_should_read_the_system_information_of_cisco_device(self, backend, snmpsim, snmp_port):
        system = await _session(backend, "cisco-router", snmp_port).get_system()

        assert system.descr == (
            "Cisco IOS Software, 7200 Software (C7200-ADVENTERPRISEK9-M), Version 15.2(4)S7, RELEASE SOFTWARE (fc4)"
        )
        assert system.is_cisco

    async def test_should_read_the_restart_reason_of_cisco_device(self, backend, snmpsim, snmp_port):
        assert await _session(backend, "cisco-router", snmp_port).get_why_reload() == "power-on"

    async def test_should_not_take_another_vendor_for_cisco(self, backend, snmpsim, snmp_port):
        system = await _session(backend, "juniper-router", snmp_port).get_system()

        assert system.descr == (
            "Juniper Networks, Inc. mx480 internet router, kernel JUNOS 20.4R3-S2.1, Build date: 2021-06-01"
        )
        assert not system.is_cisco

    async def test_when_device_does_not_answer_then_it_should_raise(self, backend, snmpsim, snmp_port):
        with pytest.raises(SnmpError):
            await _session(backend, "no-such-community", snmp_port, timeout=1, retries=0).get_uptime()

    async def test_when_device_lacks_the_object_then_it_should_raise(self, backend, snmpsim, snmp_port):
        # A Juniper has no whyReload, which is only ever asked of Cisco gear
        with pytest.raises(SnmpError):
            await _session(backend, "juniper-router", snmp_port).get_why_reload()
