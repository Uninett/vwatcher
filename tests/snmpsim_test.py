"""Tests that the simulated SNMP agent the other tests rely on actually answers"""

import pytest
from zino.config.models import PollDevice

from tests.conftest import BACKENDS


def _device(community: str, port: int) -> PollDevice:
    return PollDevice(name=community, address="127.0.0.1", port=port, community=community)


@pytest.mark.parametrize("backend", BACKENDS, indirect=True)
class TestSimulatedAgent:
    async def test_should_answer_the_uptime_of_the_cisco_fixture(self, backend, snmpsim, snmp_port):
        response = await backend.SNMP(_device("cisco-router", snmp_port)).get("SNMPv2-MIB", "sysUpTime", 0)

        assert int(response.value) == 360000

    async def test_should_answer_the_description_of_the_juniper_fixture(self, backend, snmpsim, snmp_port):
        response = await backend.SNMP(_device("juniper-router", snmp_port)).get("SNMPv2-MIB", "sysDescr", 0)

        assert "Juniper Networks" in str(response.value)
