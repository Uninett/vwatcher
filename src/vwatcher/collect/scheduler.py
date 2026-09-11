"""
Scheduling of device polls, and reloading of `polldevs.cf`.

Polls are spread evenly across the default poll interval so that a large
pollfile does not fire every device at once, and the pollfile is re-read
whenever its mtime changes.
"""

import asyncio
import logging
import operator
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from zino.config.models import DEFAULT_INTERVAL_MINUTES, PollDevice
from zino.config.polldevs import InvalidConfiguration, read_polldevs

from vwatcher.collect.snmp import SnmpSession, open_session
from vwatcher.collect.versiontask import DeviceState, VersionTask
from vwatcher.config import Configuration
from vwatcher.store import LogTree

_log = logging.getLogger(__name__)
RELOAD_JOB_ID = "vwatcher.reload_polldevs"


class Poller:
    """Keep a poll job running for every device in `polldevs.cf`"""

    def __init__(
        self,
        config: Configuration,
        tree: Optional[LogTree] = None,
        state: Optional[defaultdict[str, DeviceState]] = None,
        scheduler=None,
        session_factory=open_session,
    ):
        self.config = config
        self.tree = tree if tree is not None else LogTree(config.log_directory)
        self.state = state if state is not None else defaultdict(DeviceState)
        self.session_factory = session_factory
        self.devices: dict[str, PollDevice] = {}
        self.sessions: dict[str, SnmpSession] = {}
        self._scheduler = scheduler
        self._pollfile_mtime: Optional[float] = None

    @property
    def scheduler(self):
        """The job scheduler, created on first use so it finds a running loop"""
        if self._scheduler is None:
            self._scheduler = AsyncIOScheduler(
                event_loop=asyncio.get_event_loop(),
                job_defaults={
                    # Don't overlap with the next poller job
                    "max_instances": 1,
                    "misfire_grace_time": self.config.scheduler.misfire_grace_time,
                },
            )
        return self._scheduler

    async def run(self) -> None:
        """Start polling, and continue doing so until cancelled"""
        self.tree.ensure()
        self.scheduler.add_job(
            func=self.reload_polldevs,
            trigger="interval",
            minutes=self.config.polling.period,
            next_run_time=datetime.now(),
            id=RELOAD_JOB_ID,
            name=RELOAD_JOB_ID,
        )
        self.scheduler.start()
        await asyncio.Event().wait()

    def reload_polldevs(self) -> None:
        """Re-read the pollfile, and schedule what changed in it"""
        new, deleted, changed, defaults = self.load_polldevs()
        self.deschedule_devices(deleted | changed)
        self.schedule_devices(new | changed, stagger=_stagger_interval(defaults))

    def load_polldevs(self) -> tuple[set[str], set[str], set[str], dict]:
        """Load `polldevs.cf` if it changed since the last time it was read

        :return: The new, deleted and changed device names, and the pollfile's
            defaults.  All four are empty if nothing was read.
        """
        nothing: tuple[set[str], set[str], set[str], dict] = (set(), set(), set(), {})
        try:
            mtime = Path(self.config.polling.file).stat().st_mtime
        except OSError as error:
            _log.error("%s", error)
            return nothing
        if mtime == self._pollfile_mtime:
            return nothing
        try:
            devices, defaults = read_polldevs(self.config.polling.file)
        except (InvalidConfiguration, OSError) as error:
            _log.error("%s", error)
            return nothing

        new = set(devices) - set(self.devices)
        deleted = set(self.devices) - set(devices)
        changed = {name for name in set(self.devices) & set(devices) if devices[name] != self.devices[name]}
        if new:
            _log.info("loaded new devices: %r", sorted(new))
        if deleted:
            _log.info("deleted devices: %r", sorted(deleted))
        if changed:
            _log.info("changed devices: %r", sorted(changed))

        self.devices = devices
        self._pollfile_mtime = mtime
        for name in deleted:
            self.state.pop(name, None)
        return new, deleted, changed, defaults

    def schedule_devices(self, names: Iterable[str], stagger: int = DEFAULT_INTERVAL_MINUTES) -> None:
        """Schedule a recurring poll per device, spread across `stagger` minutes"""
        devices = sorted((self.devices[name] for name in names), key=operator.attrgetter("priority"), reverse=True)
        if not devices:
            return
        _log.debug("Scheduling %s devices", len(devices))
        spread = (stagger * 60) / len(devices)
        for index, device in enumerate(devices):
            self.scheduler.add_job(
                func=self.poll_device,
                trigger="interval",
                minutes=device.interval,
                args=(device.name,),
                next_run_time=datetime.now() + timedelta(seconds=index * spread),
                id=device.name,
                name=device.name,
            )

    def deschedule_devices(self, names: Iterable[str]) -> None:
        """Stop polling the given devices, and drop their SNMP sessions"""
        for name in names:
            self.sessions.pop(name, None)
            try:
                self.scheduler.remove_job(job_id=name)
            except JobLookupError:
                _log.debug("Job for device %s could not be found", name)

    async def poll_device(self, name: str) -> None:
        """Run one `VersionTask` against one device, if it is still configured"""
        device = self.devices.get(name)
        if device is None:
            _log.debug("Device %s is no longer configured", name)
            return
        if name not in self.sessions:
            self.sessions[name] = self.session_factory(device)
        task = VersionTask(
            device=device,
            state=self.state,
            tree=self.tree,
            snmp=self.sessions[name],
            allowed_offset=self.config.detection.allowed_offset,
        )
        await task.run()


def _stagger_interval(defaults: dict) -> int:
    """Read the pollfile's default interval, which polls are spread across"""
    try:
        return int(defaults.get("interval", DEFAULT_INTERVAL_MINUTES))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_MINUTES
