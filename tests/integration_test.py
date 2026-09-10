"""Tests the daemon and the reports through two days in one device's life"""

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from tests.conftest import CISCO_DESCR_UPGRADED, FakeSNMPSession
from vwatcher.collect import VersionTask
from vwatcher.collect.versiontask import TICKS_PER_SECOND
from vwatcher.report import (
    current_versions,
    day_report,
    month_restart_report,
    month_upgrade_report,
    rotate_and_report,
)

POLL_INTERVAL_SECONDS = 300
# Been up from before the first test day
INITIAL_UPTIME_SECONDS = 3 * 86400
DAY_ONE = date(2025, 9, 4)
DAY_TWO = date(2025, 9, 5)


@pytest.fixture
def watched_device(device, state, log_tree, clock):
    session = FakeSNMPSession(uptime=INITIAL_UPTIME_SECONDS * TICKS_PER_SECOND)
    task = VersionTask(device=device, state=state, tree=log_tree, snmp=session, clock=clock)

    def wait(seconds):
        clock.advance(seconds)
        session.uptime += seconds * TICKS_PER_SECOND

    async def poll(times=1):
        for _ in range(times):
            await task.run()
            wait(POLL_INTERVAL_SECONDS)

    def reboot(uptime_seconds=60, descr=None, reason="reload"):
        session.uptime = uptime_seconds * TICKS_PER_SECOND
        session.why_reload = reason
        if descr is not None:
            session.system.descr = descr

    return SimpleNamespace(poll=poll, reboot=reboot, wait=wait, snmp=session)


class TestTwoDaysInOneDevicesLife:
    async def test_should_report_an_upgrade_and_then_a_crash(self, watched_device, log_tree, clock):
        # Day one: a quiet morning, then an upgrade in the afternoon
        await watched_device.poll(times=6)
        watched_device.reboot(descr=CISCO_DESCR_UPGRADED, reason="reload")
        await watched_device.poll(times=6)

        assert "15.2(4)S8" in current_versions(log_tree)

        first_report = rotate_and_report(log_tree, DAY_ONE)
        assert "Upgraded routers:" in first_report
        assert "from Cisco-IOS 15.2(4)S7" in first_report
        assert "to Cisco-IOS 15.2(4)S8" in first_report
        assert "Restarted/crashed routers:" not in first_report  # the upgrade explains the reboot

        # Day two: the same device crashes
        watched_device.wait(int(datetime(2025, 9, 5, 8, 0, 0).timestamp() - clock()))
        await watched_device.poll(times=3)
        watched_device.reboot(reason="watchdog timeout")
        await watched_device.poll(times=3)

        second_report = rotate_and_report(log_tree, DAY_TWO)
        assert "Upgraded routers:" not in second_report  # it is still on 15.2(4)S8
        assert "example-gw      (1 total) current version Cisco-IOS 15.2(4)S8" in second_report
        assert "restart reason: watchdog timeout" in second_report

        # And the month sees the one upgrade, plus every restart with a reason
        assert month_upgrade_report(log_tree, "2025-09").splitlines() == [
            "Sep 04 example-gw      from Cisco-IOS 15.2(4)S7 C7200-ADVENTERPRISEK9-M "
            "to Cisco-IOS 15.2(4)S8 C7200-ADVENTERPRISEK9-M"
        ]
        restarts = month_restart_report(log_tree, "2025-09").splitlines()
        assert [line.split(": ")[-1] for line in restarts] == ["reload", "watchdog timeout"]

    async def test_should_reproduce_a_rotated_days_report_on_demand(self, watched_device, log_tree):
        await watched_device.poll()
        watched_device.reboot(descr=CISCO_DESCR_UPGRADED)
        await watched_device.poll()

        rotated = rotate_and_report(log_tree, DAY_ONE)

        assert day_report(log_tree, "2025-09", "04") == rotated

    async def test_when_a_device_just_stays_up_then_it_should_keep_quiet(self, watched_device, log_tree):
        await watched_device.poll(times=20)

        assert current_versions(log_tree).strip().endswith("Cisco-IOS 15.2(4)S7 C7200-ADVENTERPRISEK9-M")
        assert rotate_and_report(log_tree, DAY_ONE) == ""

    async def test_when_a_device_restarted_unwatched_then_it_should_be_noticed(self, watched_device, log_tree):
        await watched_device.poll(times=3)

        # vwatcher is down for an hour, during which the device reboots
        watched_device.wait(3600)
        watched_device.reboot(uptime_seconds=120, reason="power-on")
        await watched_device.poll()

        report = rotate_and_report(log_tree, DAY_ONE)
        assert "example-gw      (1 total)" in report
        assert "restart reason: power-on" in report
