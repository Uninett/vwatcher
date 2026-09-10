"""Tests of the SNMP layer against a simulated agent"""

import asyncio
import os
import socket
from importlib import import_module
from shutil import which
from types import ModuleType

import pytest
import pytest_asyncio
from zino.config.models import PollDevice
from zino.snmp.base import SNMPBackendError

from vwatcher.collect.snmp import SnmpError, ZinoSession

BACKENDS = {"pysnmp": "zino.snmp.pysnmp_backend", "netsnmp": "zino.snmp.netsnmpy_backend"}


@pytest.fixture
def backend(request) -> ModuleType:
    return _load_backend_module(request.param)


def _load_backend_module(name: str) -> ModuleType:
    try:
        module = import_module(BACKENDS[name])
        module.init_backend()
    except (ImportError, OSError, SNMPBackendError) as error:
        pytest.skip(f"the {name} back-end is not available here: {error}")
    return module


@pytest.fixture(scope="session")
def snmpsimd_path() -> str:
    path = which("snmpsim-command-responder")
    if not path:
        pytest.skip("snmpsim-command-responder is not installed")
    return path


@pytest.fixture(scope="session")
def snmp_port() -> int:
    """A free UDP port for the simulated agent to listen on"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest_asyncio.fixture(scope="session")
async def snmpsim(snmpsimd_path, snmp_port, tmp_path_factory):
    """
    Run `snmpsim` on the `snmp_fixtures` directory, as Zino's tests do.

    It gets a private `--cache-dir` to allow all the Python interpreters to run it.
    """
    fixtures = os.path.join(os.path.dirname(os.path.dirname(__file__)), "snmp_fixtures")
    process = await asyncio.create_subprocess_exec(
        snmpsimd_path,
        f"--data-dir={fixtures}",
        f"--cache-dir={tmp_path_factory.mktemp('snmpsim')}",
        "--log-level=error",
        f"--agent-udpv4-endpoint=127.0.0.1:{snmp_port}",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await _wait_until_it_answers(process, snmp_port)
        yield
    finally:
        if process.returncode is None:
            process.terminate()
        await process.wait()


async def _wait_until_it_answers(process, port: int, tries: int = 20) -> None:
    backend = _load_backend_module("pysnmp")
    for _ in range(tries):
        if process.returncode is not None:
            pytest.skip(f"snmpsim exited with status {process.returncode}; it may not run on this Python")
        if await _answers(backend, "cisco-router", port):
            return
        await asyncio.sleep(0.5)
    raise TimeoutError("snmpsim never answered a query")


def _session(backend: ModuleType, community: str, port: int, **timeouts) -> ZinoSession:
    device = PollDevice(name=community, address="127.0.0.1", port=port, community=community, **timeouts)
    return ZinoSession(backend.SNMP(device))


async def _answers(backend: ModuleType, community: str, port: int) -> bool:
    try:
        return await _session(backend, community, port).get_uptime() > 0
    except SnmpError:
        return False


@pytest.mark.parametrize("backend", BACKENDS, indirect=True)
class TestAgainstASimulatedAgent:
    async def test_should_read_the_uptime_in_centiseconds(self, backend, snmpsim, snmp_port):
        assert await _session(backend, "cisco-router", snmp_port).get_uptime() == 360000

    async def test_should_read_the_system_information_of_a_cisco_device(self, backend, snmpsim, snmp_port):
        system = await _session(backend, "cisco-router", snmp_port).get_system()

        assert system.descr == (
            "Cisco IOS Software, 7200 Software (C7200-ADVENTERPRISEK9-M), Version 15.2(4)S7, RELEASE SOFTWARE (fc4)"
        )
        assert system.is_cisco

    async def test_should_read_the_restart_reason_of_a_cisco_device(self, backend, snmpsim, snmp_port):
        assert await _session(backend, "cisco-router", snmp_port).get_why_reload() == "power-on"

    async def test_should_not_take_another_vendor_for_cisco(self, backend, snmpsim, snmp_port):
        system = await _session(backend, "juniper-router", snmp_port).get_system()

        assert system.descr == (
            "Juniper Networks, Inc. mx480 internet router, kernel JUNOS 20.4R3-S2.1, Build date: 2021-06-01"
        )
        assert not system.is_cisco

    async def test_when_a_device_does_not_answer_then_it_should_raise(self, backend, snmpsim, snmp_port):
        with pytest.raises(SnmpError):
            await _session(backend, "no-such-community", snmp_port, timeout=1, retries=0).get_uptime()

    async def test_when_a_device_lacks_the_object_then_it_should_raise(self, backend, snmpsim, snmp_port):
        # A Juniper has no whyReload, which is only ever asked of Cisco gear
        with pytest.raises(SnmpError):
            await _session(backend, "juniper-router", snmp_port).get_why_reload()
