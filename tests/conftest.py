"""Shared test fixtures, including a simulated SNMP agent"""

import asyncio
import os
import signal
import socket
import subprocess
import sys
from importlib import import_module
from importlib.metadata import version
from shutil import which
from types import ModuleType

import pytest
import pytest_asyncio
from zino.config.models import PollDevice
from zino.snmp.base import SNMPBackendError
from zino.snmp.base import SnmpError as ZinoSnmpError

BACKENDS = {"pysnmp": "zino.snmp.pysnmp_backend", "netsnmp": "zino.snmp.netsnmpy_backend"}


@pytest.fixture(scope="session")
def event_loop():
    """
    Redefine pytest-asyncio's event loop to live for the whole session.

    Zino's PySNMP backend keeps one SNMP engine for the whole process, and it
    only works with the loop it was built on: a loop per test hangs the rest.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def backend(request) -> ModuleType:
    """One of Zino's SNMP backends, named by an indirect parameter"""
    return load_backend_module(request.param)


def load_backend_module(name: str) -> ModuleType:
    """Import and initialize one of Zino's backends, skipping if it is unavailable here"""
    try:
        module = import_module(BACKENDS[name])
        module.init_backend()
    except (ImportError, OSError, SNMPBackendError) as error:
        pytest.skip(f"the {name} backend is not available here: {error}")
    return module


@pytest.fixture(scope="session")
def snmp_port() -> int:
    """A free UDP port for the simulated agent to listen on"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(scope="session")
def snmp_fixture_directory() -> str:
    return os.path.join(os.path.dirname(__file__), "snmp_fixtures")


@pytest.fixture(scope="session")
def snmpsim_command(snmp_fixture_directory, snmp_port, tmp_path_factory) -> list[str]:
    """
    Build the command that starts snmpsim on the `snmp_fixtures` directory.

    Prefers running it through `uvx` on Python 3.11, because snmpsim is roughly
    25x slower on 3.13 and newer, where `dbm.sqlite3` fsyncs every write while
    the index is built.  See https://github.com/lextudio/pysnmp/issues/223.

    It gets a private `--cache-dir` to allow all the Python interpreters to run it.
    """
    arguments = [
        f"--data-dir={snmp_fixture_directory}",
        f"--cache-dir={tmp_path_factory.mktemp('snmpsim')}",
        "--log-level=error",
        f"--agent-udpv4-endpoint=127.0.0.1:{snmp_port}",
    ]

    if which("uvx") and _uv_has_python("3.11"):
        return [
            "uvx",
            "--python=3.11",
            # pysnmp needs cryptography, but only declares it as a dev dependency
            f"--with={_installed_spec('cryptography')}",
            # snmpsim.utils imports pysmi, but nothing pulls it in
            f"--with={_installed_spec('pysmi')}",
            f"--from={_installed_spec('snmpsim')}",
            "snmpsim-command-responder",
        ] + arguments

    path = which("snmpsim-command-responder")
    if not path:
        pytest.skip("neither uvx nor snmpsim-command-responder is installed")
    if sys.version_info >= (3, 13):
        pytest.skip("snmpsim is unusably slow on Python 3.13+; install uv to run it on 3.11 instead")
    return [path] + arguments


@pytest_asyncio.fixture(scope="session")
async def snmpsim(snmpsim_command, snmp_port):
    """Run a simulated SNMP agent for the tests that ask for it"""
    # uvx spawns snmpsim as a grandchild, so give it its own process group
    # to ensure the whole tree can be killed on teardown
    process = await asyncio.create_subprocess_exec(
        *snmpsim_command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        await _wait_until_it_answers(process, snmp_port)
        yield
    finally:
        _kill_process_group(process)
        await process.wait()


async def _wait_until_it_answers(process, port: int, tries: int = 3, delay: float = 0.5, backoff: int = 2) -> None:
    """Wait for the agent to answer, the way Zino's own snmpsim fixture does"""
    backend = load_backend_module("pysnmp")
    for attempt in range(tries):
        if process.returncode is not None:
            pytest.fail(f"snmpsim exited with status {process.returncode} before it answered")
        if await _answers(backend, port):
            return
        if attempt < tries - 1:
            await asyncio.sleep(delay)
            delay *= backoff
    raise TimeoutError("snmpsim never answered a query")


async def _answers(backend: ModuleType, port: int) -> bool:
    """Ask the simulated agent for its uptime, using the backend directly"""
    device = PollDevice(name="cisco-router", address="127.0.0.1", port=port, community="cisco-router")
    try:
        response = await backend.SNMP(device).get("SNMPv2-MIB", "sysUpTime", 0)
    except (ZinoSnmpError, OSError, TimeoutError):
        return False
    return int(response.value) > 0


def _kill_process_group(process) -> None:
    """Kill a whole process group, ignoring one that is already gone"""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _uv_has_python(wanted: str) -> bool:
    return subprocess.run(["uv", "python", "find", wanted], capture_output=True).returncode == 0


def _installed_spec(package: str) -> str:
    """Pin a `uvx` dependency to the version installed here"""
    return f"{package}=={version(package)}"
