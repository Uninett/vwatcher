"""Tests for the restart detection"""

import pytest

from tests.conftest import CISCO_DESCR, JUNIPER_DESCR, JUNIPER_OID, FakeSNMPSession
from vwatcher.collect.snmp import SystemInfo
from vwatcher.collect.versiontask import COUNTER32_MAX, TICKS_PER_SECOND, VersionTask
from vwatcher.store import RELOADED, RESTART_REASON, SOFTWARE, UPTIME, parse_timestamp

ONE_HOUR = 3600 * TICKS_PER_SECOND


@pytest.fixture
def task(device, state, log_tree, session, clock):
    return VersionTask(device=device, state=state, tree=log_tree, snmp=session, clock=clock)


def events(log_tree, event=None):
    return [entry for entry in log_tree.get_entries() if event is None or entry.event == event]


class TestFirstPoll:
    async def test_should_log_a_restart_because_the_device_is_unknown(self, task, log_tree):
        await task.run()
        assert [entry.event for entry in events(log_tree)] == [RELOADED, UPTIME, SOFTWARE, RESTART_REASON]

    async def test_should_log_the_uptime_it_has_read(self, task, log_tree, session):
        await task.run()
        assert events(log_tree, UPTIME)[0].value == str(session.uptime)

    async def test_should_log_when_the_device_booted(self, task, log_tree, session, clock):
        await task.run()
        booted = parse_timestamp(events(log_tree, RELOADED)[0].value)
        assert (clock() - booted.timestamp()) == session.uptime / TICKS_PER_SECOND

    async def test_should_record_the_uptime_state_file(self, task, log_tree, device, session):
        await task.run()
        assert log_tree.get_uptimes() == {device.name: session.uptime}

    async def test_should_record_the_version_baseline(self, task, log_tree, device):
        await task.run()
        assert log_tree.get_descrs() == {device.name: CISCO_DESCR}

    async def test_should_ask_for_the_version_only_once(self, task, session):
        await task.run()

        assert session.calls["get_system"] == 1

    async def test_when_the_version_poll_fails_then_the_baseline_should_be_retried(
        self, device, state, log_tree, clock
    ):
        session = FakeSNMPSession(uptime=ONE_HOUR, errors={"get_system": "noSuchName"})
        task = VersionTask(device, state, log_tree, session, clock=clock)

        await task.run()

        assert session.calls["get_system"] == 2  # once for the log, once for the baseline


class TestSubsequentPolls:
    async def test_when_uptime_grows_as_expected_then_nothing_should_be_logged(self, task, log_tree, session, clock):
        await task.run()
        logged = len(events(log_tree))

        clock.advance(300)
        session.uptime += 300 * TICKS_PER_SECOND
        await task.run()

        assert len(events(log_tree)) == logged

    async def test_when_uptime_dropped_then_a_restart_should_be_logged(self, task, log_tree, session, clock):
        await task.run()
        clock.advance(300)
        session.uptime = 60 * TICKS_PER_SECOND
        await task.run()

        assert [entry.event for entry in events(log_tree)][-4:] == [RELOADED, UPTIME, SOFTWARE, RESTART_REASON]

    async def test_when_uptime_lags_within_the_allowed_offset_then_no_restart_should_be_logged(
        self, task, log_tree, session, clock
    ):
        await task.run()
        logged = len(events(log_tree))

        clock.advance(600)
        session.uptime += 600 * TICKS_PER_SECOND - task.allowed_offset
        await task.run()

        assert len(events(log_tree)) == logged

    async def test_when_uptime_lags_beyond_the_allowed_offset_then_a_restart_should_be_logged(
        self, task, log_tree, session, clock
    ):
        await task.run()

        clock.advance(600)
        session.uptime += 600 * TICKS_PER_SECOND - task.allowed_offset - 1
        await task.run()

        assert len(events(log_tree, RELOADED)) == 2

    async def test_should_keep_the_uptime_state_file_current(self, task, log_tree, device, session, clock):
        await task.run()
        clock.advance(60)
        session.uptime += 60 * TICKS_PER_SECOND
        await task.run()

        assert log_tree.get_uptimes() == {device.name: session.uptime}

    async def test_should_not_rewrite_the_version_baseline(self, task, session, clock):
        await task.run()
        polls = session.calls["get_system"]

        clock.advance(60)
        session.uptime += 60 * TICKS_PER_SECOND
        await task.run()

        assert session.calls["get_system"] == polls

    async def test_when_the_baseline_was_rotated_away_then_it_should_be_rewritten(
        self, task, log_tree, device, session, clock
    ):
        await task.run()
        log_tree.rotate()

        clock.advance(60)
        session.uptime += 60 * TICKS_PER_SECOND
        await task.run()

        assert log_tree.get_descrs() == {device.name: CISCO_DESCR}

    async def test_when_the_device_was_just_booting_then_the_next_poll_should_confirm_the_restart(
        self, task, log_tree, session, clock
    ):
        # An uptime of zero means we do not know when the device came up
        session.uptime = 0
        await task.run()

        clock.advance(60)
        session.uptime = 60 * TICKS_PER_SECOND
        await task.run()

        assert len(events(log_tree, RELOADED)) == 2


class TestCounterWraparound:
    async def test_should_not_be_reported_as_a_restart(self, task, log_tree, session, clock):
        await task.run()
        session.uptime = COUNTER32_MAX - task.allowed_offset
        clock.advance(60)
        session.uptime += 60 * TICKS_PER_SECOND
        await task.run()
        reloads = len(events(log_tree, RELOADED))

        # The counter is now within the allowed offset of wrapping, and does wrap
        clock.advance(60)
        session.uptime = 60 * TICKS_PER_SECOND
        await task.run()

        assert len(events(log_tree, RELOADED)) == reloads

    async def test_should_be_noted_in_the_log(self, task, log_tree, state, device, session, clock):
        state[device.name].uptime = COUNTER32_MAX - task.allowed_offset
        state[device.name].last_poll = clock()
        clock.advance(60)

        await task.run()

        assert "uptime wraparound" in log_tree.log_file.read_text()


class TestRestartReason:
    async def test_should_be_polled_for_cisco_devices(self, task, log_tree, session):
        await task.run()
        assert events(log_tree, RESTART_REASON)[0].value == session.why_reload

    async def test_should_not_be_polled_for_other_vendors(self, device, state, log_tree, clock):
        session = FakeSNMPSession(uptime=ONE_HOUR, system=SystemInfo(object_id=JUNIPER_OID, descr=JUNIPER_DESCR))
        await VersionTask(device, state, log_tree, session, clock=clock).run()

        assert session.calls["get_why_reload"] == 0
        assert events(log_tree, RESTART_REASON) == []

    async def test_when_the_poll_fails_then_the_error_should_be_logged(self, device, state, log_tree, clock):
        session = FakeSNMPSession(uptime=ONE_HOUR, errors={"get_why_reload": "noSuchName"})
        await VersionTask(device, state, log_tree, session, clock=clock).run()

        assert "whyReload poll returned noSuchName" in log_tree.log_file.read_text()


class TestSnmpFailure:
    async def test_when_the_uptime_poll_fails_then_the_error_should_be_logged(self, device, state, log_tree, clock):
        session = FakeSNMPSession(errors={"get_uptime": "noResponse"})
        await VersionTask(device, state, log_tree, session, clock=clock).run()

        assert "uptime poll returned noResponse" in log_tree.log_file.read_text()

    async def test_when_the_uptime_poll_fails_then_no_state_should_be_recorded(self, device, state, log_tree, clock):
        session = FakeSNMPSession(errors={"get_uptime": "noResponse"})
        await VersionTask(device, state, log_tree, session, clock=clock).run()

        assert state[device.name].last_poll is None
        assert log_tree.get_uptimes() == {}

    async def test_when_the_uptime_poll_recovers_then_the_device_is_not_reported_restarted_twice(
        self, device, state, log_tree, clock
    ):
        session = FakeSNMPSession(uptime=ONE_HOUR, errors={"get_uptime": "noResponse"})
        task = VersionTask(device, state, log_tree, session, clock=clock)
        await task.run()

        session.errors = {}
        clock.advance(300)
        session.uptime += 300 * TICKS_PER_SECOND
        await task.run()
        clock.advance(300)
        session.uptime += 300 * TICKS_PER_SECOND
        await task.run()

        assert len(events(log_tree, RELOADED)) == 1

    async def test_when_the_version_poll_fails_then_the_error_should_be_logged(self, device, state, log_tree, clock):
        session = FakeSNMPSession(uptime=ONE_HOUR, errors={"get_system": "noSuchName"})
        await VersionTask(device, state, log_tree, session, clock=clock).run()

        assert "poll returned noSuchName" in log_tree.log_file.read_text()
        assert log_tree.get_descrs() == {}
