"""Shared test fixtures"""

import asyncio
from collections import Counter, defaultdict
from datetime import datetime
from typing import Optional

import pytest
from zino.config.models import PollDevice

from vwatcher.collect import DeviceState, SnmpError, SystemInfo
from vwatcher.config import Configuration
from vwatcher.store import LogTree

CISCO_OID = "1.3.6.1.4.1.9.1.222"
CISCO_DESCR = "Cisco IOS Software, 7200 Software (C7200-ADVENTERPRISEK9-M), Version 15.2(4)S7, RELEASE SOFTWARE (fc4)"
CISCO_DESCR_UPGRADED = (
    "Cisco IOS Software, 7200 Software (C7200-ADVENTERPRISEK9-M), Version 15.2(4)S8, RELEASE SOFTWARE (fc4)"
)
JUNIPER_OID = "1.3.6.1.4.1.2636.1.1.1.2.29"
JUNIPER_DESCR = "Juniper Networks, Inc. mx480 internet router, kernel JUNOS 20.4R3-S2.1, Build date: 2021-06-01"

BASE_TIME = datetime(2025, 9, 4, 12, 0, 0).timestamp()


class FakeClock:
    def __init__(self, now: float = BASE_TIME):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


class FakeSNMPSession:
    def __init__(
        self,
        uptime: int = 0,
        system: Optional[SystemInfo] = None,
        why_reload: str = "power-on",
        errors: Optional[dict] = None,
    ):
        self.uptime = uptime
        self.system = system if system is not None else SystemInfo(object_id=CISCO_OID, descr=CISCO_DESCR)
        self.why_reload = why_reload
        self.errors = errors or {}
        self.calls: Counter = Counter()

    async def get_uptime(self) -> int:
        return self._answer("get_uptime", self.uptime)

    async def get_system(self) -> SystemInfo:
        return self._answer("get_system", self.system)

    async def get_why_reload(self) -> str:
        return self._answer("get_why_reload", self.why_reload)

    def _answer(self, call: str, value):
        self.calls[call] += 1
        if call in self.errors:
            raise SnmpError(self.errors[call])
        return value


@pytest.fixture(scope="session")
def event_loop():
    """Redefine pytest-asyncio's event loop to live for the whole session.

    Zino's PySNMP back-end keeps one SNMP engine for the whole process, and it
    only works with the loop it was built on: a loop per test hangs the rest.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def log_tree(tmp_path, clock) -> LogTree:
    tree = LogTree(tmp_path / "logs", clock=clock)
    tree.ensure()
    return tree


@pytest.fixture
def state() -> dict:
    return defaultdict(DeviceState)


@pytest.fixture
def device() -> PollDevice:
    return PollDevice(name="example-gw", address="10.0.42.1", community="foobar")


@pytest.fixture
def session() -> FakeSNMPSession:
    return FakeSNMPSession(uptime=100 * 3600)


@pytest.fixture
def polldevs_conf(tmp_path):
    """Same shape pollfile Zino's test suite uses"""
    path = tmp_path / "polldevs.cf"
    path.write_text(
        """# polldevs test config
default interval: 5
default community: foobar
default domain: uninett.no
default statistics: yes

name: example-gw
address: 10.0.42.1

name: example-gw2
address: 10.0.43.1
priority: 200"""  # The missing final newline is intentional
    )
    return path


@pytest.fixture
def vwatcher_conf(tmp_path, polldevs_conf):
    path = tmp_path / "vwatcher.toml"
    path.write_text(
        f"""[polling]
file = "{polldevs_conf}"
period = 1

[logs]
directory = "{tmp_path / "logs"}"

[mail]
sender = "vwatcher@example.org"
recipient = "testuser@example.org"
"""
    )
    return path


@pytest.fixture
def config(tmp_path, polldevs_conf) -> Configuration:
    return Configuration.model_validate(
        {
            "polling": {"file": str(polldevs_conf)},
            "logs": {"directory": str(tmp_path / "logs")},
            "mail": {"sender": "vwatcher@example.org", "recipient": "testuser@example.org"},
        }
    )
