"""Tests for pollfile loading and poll scheduling"""

import os
from datetime import datetime
from types import SimpleNamespace

import pytest
from apscheduler.jobstores.base import JobLookupError

from tests.conftest import FakeSNMPSession
from vwatcher.collect.scheduler import RELOAD_JOB_ID, Poller
from vwatcher.store import RELOADED


class FakeScheduler:
    def __init__(self):
        self.jobs = {}
        self.started = False

    def add_job(self, func, trigger=None, id=None, **kwargs):
        self.jobs[id] = SimpleNamespace(func=func, trigger=trigger, id=id, **kwargs)

    def remove_job(self, job_id):
        if job_id not in self.jobs:
            raise JobLookupError(job_id)
        del self.jobs[job_id]

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def start(self):
        self.started = True


@pytest.fixture
def scheduler() -> FakeScheduler:
    return FakeScheduler()


@pytest.fixture
def poller(config, log_tree, state, scheduler) -> Poller:
    return Poller(
        config=config,
        tree=log_tree,
        state=state,
        scheduler=scheduler,
        session_factory=lambda device: FakeSNMPSession(uptime=360000),
    )


# The defaults from the `polldevs_conf` fixture, so that a rewrite only changes
# what a test means to change.
DEFAULTS = "default interval: 5\ndefault community: foobar\ndefault domain: uninett.no\ndefault statistics: yes\n\n"


def rewrite(path, body, mtime=2_000_000_000):
    """Rewrites the pollfile with a new mtime, so the poller notices"""
    path.write_text(DEFAULTS + body)
    os.utime(path, (mtime, mtime))


class TestLoadPolldevs:
    def test_should_report_every_device_as_new_on_the_first_read(self, poller, polldevs_conf):
        new, deleted, changed, defaults = poller.load_polldevs(polldevs_conf)

        assert new == {"example-gw", "example-gw2"}
        assert not deleted and not changed
        assert defaults["interval"] == "5"

    def test_when_the_file_is_untouched_then_it_should_report_no_changes(self, poller, polldevs_conf):
        poller.load_polldevs(polldevs_conf)

        assert poller.load_polldevs(polldevs_conf) == (set(), set(), set(), {})

    def test_should_report_a_removed_device_as_deleted(self, poller, polldevs_conf):
        poller.load_polldevs(polldevs_conf)
        rewrite(polldevs_conf, "name: example-gw\naddress: 10.0.42.1\n")

        new, deleted, changed, _ = poller.load_polldevs(polldevs_conf)

        assert deleted == {"example-gw2"}
        assert not new and not changed

    def test_should_forget_the_state_of_a_deleted_device(self, poller, polldevs_conf, state):
        poller.load_polldevs(polldevs_conf)
        state["example-gw2"]
        rewrite(polldevs_conf, "name: example-gw\naddress: 10.0.42.1\n")

        poller.load_polldevs(polldevs_conf)

        assert "example-gw2" not in state

    def test_should_report_an_edited_device_as_changed(self, poller, polldevs_conf):
        poller.load_polldevs(polldevs_conf)
        rewrite(polldevs_conf, "name: example-gw\naddress: 10.0.42.1\ninterval: 10\n")

        new, deleted, changed, _ = poller.load_polldevs(polldevs_conf)

        assert changed == {"example-gw"}
        assert poller.devices["example-gw"].interval == 10

    def test_when_the_file_is_missing_then_it_should_report_nothing(self, poller, polldevs_conf):
        polldevs_conf.unlink()

        assert poller.load_polldevs(polldevs_conf) == (set(), set(), set(), {})

    def test_when_the_file_turns_invalid_then_it_should_keep_the_devices_it_had(self, poller, polldevs_conf):
        poller.load_polldevs(polldevs_conf)
        rewrite(polldevs_conf, "this is not a setting\n")

        assert poller.load_polldevs(polldevs_conf) == (set(), set(), set(), {})
        assert set(poller.devices) == {"example-gw", "example-gw2"}


class TestScheduling:
    def test_should_schedule_a_job_per_device(self, poller, scheduler):
        poller.reload_polldevs()

        assert set(scheduler.jobs) == {"example-gw", "example-gw2"}

    def test_should_poll_a_device_at_its_own_interval(self, poller, scheduler):
        poller.reload_polldevs()

        assert scheduler.jobs["example-gw"].minutes == 5

    def test_should_spread_the_first_polls_out(self, poller, scheduler):
        poller.reload_polldevs()
        first, second = (scheduler.jobs[name].next_run_time for name in ("example-gw", "example-gw2"))

        assert first != second

    def test_should_poll_the_highest_priority_device_first(self, poller, scheduler):
        poller.reload_polldevs()

        assert scheduler.jobs["example-gw2"].next_run_time < scheduler.jobs["example-gw"].next_run_time

    def test_should_deschedule_a_deleted_device(self, poller, scheduler, polldevs_conf):
        poller.reload_polldevs()
        rewrite(polldevs_conf, "name: example-gw\naddress: 10.0.42.1\n")

        poller.reload_polldevs()

        assert set(scheduler.jobs) == {"example-gw"}

    def test_should_reschedule_a_changed_device(self, poller, scheduler, polldevs_conf):
        poller.reload_polldevs()
        rewrite(polldevs_conf, "name: example-gw\naddress: 10.0.42.1\ninterval: 10\n")

        poller.reload_polldevs()

        assert scheduler.jobs["example-gw"].minutes == 10

    def test_should_drop_the_session_of_a_descheduled_device(self, poller):
        poller.reload_polldevs()
        poller.sessions["example-gw"] = FakeSNMPSession()

        poller.deschedule_devices(["example-gw"])

        assert "example-gw" not in poller.sessions

    def test_when_a_device_was_never_scheduled_then_descheduling_should_be_tolerated(self, poller):
        poller.deschedule_devices(["never-seen"])


class TestPollDevice:
    async def test_should_poll_a_configured_device(self, poller, polldevs_conf, log_tree):
        poller.load_polldevs(polldevs_conf)

        await poller.poll_device("example-gw")

        assert [entry.event for entry in log_tree.get_entries()][0] == RELOADED

    async def test_should_reuse_one_session_per_device(self, poller, polldevs_conf):
        poller.load_polldevs(polldevs_conf)

        await poller.poll_device("example-gw")
        session = poller.sessions["example-gw"]
        await poller.poll_device("example-gw")

        assert poller.sessions["example-gw"] is session

    async def test_when_a_device_is_gone_then_it_should_do_nothing(self, poller, log_tree):
        await poller.poll_device("never-seen")

        assert list(log_tree.get_entries()) == []


class TestRun:
    async def test_should_start_the_scheduler_and_schedule_a_pollfile_reload(self, poller, scheduler, monkeypatch):
        async def stop_immediately():
            raise KeyboardInterrupt

        monkeypatch.setattr("asyncio.Event.wait", lambda self: stop_immediately())
        with pytest.raises(KeyboardInterrupt):
            await poller.run()

        assert scheduler.started
        # Due at once, so its first run reads the pollfile and schedules the devices
        assert scheduler.jobs[RELOAD_JOB_ID].next_run_time <= datetime.now()


class TestScheduler:
    @pytest.fixture
    def real_poller(self, config, log_tree) -> Poller:
        return Poller(config=config, tree=log_tree, session_factory=lambda device: FakeSNMPSession())

    async def test_should_give_a_poll_job_the_configured_defaults(self, real_poller, config):
        config.scheduler.misfire_grace_time = 42
        scheduler = real_poller.scheduler
        # Paused, so that the jobs land in the job store without ever running
        scheduler.start(paused=True)
        try:
            real_poller.reload_polldevs()
            job = scheduler.get_job("example-gw")

            assert job.misfire_grace_time == 42
            assert job.max_instances == 1  # ensure no poll overlap
        finally:
            scheduler.shutdown(wait=False)
