"""
Restart and upgrade detection, core of the vwatcher daemon.

Every poll asks a device for `sysUpTime` only.  A restart is inferred when the
reported uptime falls meaningfully short of what it should have been, given the
previous poll.  Only then are `sysDescr` (and, on Cisco gear, the restart
reason) fetched and logged, on the assumption that a software upgrade requires
a reboot.  Deciding whether a version changed is left to the reports, which
diff the logged versions against the start-of-day baseline.
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from zino.config.models import PollDevice

from vwatcher.collect.snmp import SnmpError, SnmpSession, SystemInfo
from vwatcher.config import ALLOWED_OFFSET
from vwatcher.store import LogTree, eventlog

_logger = logging.getLogger(__name__)

# sysUpTime is a 32-bit centisecond counter, and wraps after ~497 days
COUNTER32_MAX = 0xFFFFFFFF
TICKS_PER_SECOND = 100


@dataclass
class DeviceState:
    """
    What the last poll of one device saw.

    None of it is persisted; losing it makes the next poll count as a restart,
    which re-logs the device's software version.
    """

    uptime: int = 0
    last_poll: Optional[float] = None


class VersionTask:
    """One poll of one device"""

    def __init__(
        self,
        device: PollDevice,
        state: defaultdict[str, DeviceState],
        tree: LogTree,
        snmp: SnmpSession,
        allowed_offset: int = ALLOWED_OFFSET,
        clock=time.time,
    ):
        self.device = device
        self.state = state
        self.tree = tree
        self.snmp = snmp
        self.allowed_offset = allowed_offset
        self.clock = clock

    @property
    def device_state(self) -> DeviceState:
        return self.state[self.device.name]

    async def run(self) -> None:
        """Poll the device's uptime, logging a restart and its cause if it restarted."""
        name = self.device.name
        device_state = self.device_state
        try:
            uptime = await self.snmp.get_uptime()
        except SnmpError as error:
            self.tree.log.write(name, f"uptime poll returned {error}")
            return

        now = self.clock()
        system = None
        if not self._uptime_is_expected(uptime, device_state, now):
            self.tree.log.write_event(name, eventlog.RELOADED, self._boot_time(uptime, now))
            self.tree.log.write_event(name, eventlog.UPTIME, str(uptime))
            system = await self._log_software()

        device_state.uptime = uptime
        device_state.last_poll = now
        await self._save_baseline_descr(system)
        self.tree.save_uptime(name, uptime)

    def _uptime_is_expected(self, uptime: int, device_state: DeviceState, now: float) -> bool:
        """
        Decide whether a reported uptime is consistent with the previous poll.

        A device we have never polled, or whose uptime we do not know, counts as
        restarted, so that a fresh daemon records everyone's version.
        """
        if device_state.last_poll is None:
            return False

        estimated = device_state.uptime + TICKS_PER_SECOND * int(now - device_state.last_poll)
        if estimated > COUNTER32_MAX - self.allowed_offset:
            # The counter has wrapped, or is about to: we cannot tell a restart
            # from a wraparound, so assume the device stayed up.
            self.tree.log.write(
                f"{self.device.name}: uptime wraparound (or close), estup {estimated} "
                f"lastuptime {device_state.uptime} lastpoll {int(device_state.last_poll)} time {int(now)}"
            )
            return True

        if device_state.uptime == 0:
            return False
        return uptime >= estimated - self.allowed_offset

    def _boot_time(self, uptime: int, now: float) -> str:
        return eventlog.format_timestamp(datetime.fromtimestamp(int(now) - uptime // TICKS_PER_SECOND))

    async def _log_software(self) -> Optional[SystemInfo]:
        """
        Log the software version, and the restart reason on Cisco gear.
        :returns what the device answered.
        """
        name = self.device.name
        try:
            system = await self.snmp.get_system()
        except SnmpError as error:
            self.tree.log.write(name, f"poll returned {error}")
            return None

        self.tree.log.write_event(name, eventlog.SOFTWARE, eventlog.normalize_descr(system.descr))
        if system.is_cisco:
            await self._log_restart_reason()
        return system

    async def _log_restart_reason(self) -> None:
        name = self.device.name
        try:
            reason = await self.snmp.get_why_reload()
        except SnmpError as error:
            self.tree.log.write(name, f"whyReload poll returned {error}")
            return
        self.tree.log.write_event(name, eventlog.RESTART_REASON, eventlog.normalize_descr(reason))

    async def _save_baseline_descr(self, system: Optional[SystemInfo] = None) -> None:
        """
        Record today's baseline version, unless it is already recorded.

        Rotation empties `descs`, so this costs at most one extra GET per device
        per day. `system` is what the device has already answered this poll, if
        a restart made us ask, so that we do not ask twice.
        """
        name = self.device.name
        if self.tree.has_baseline_version(name):
            return
        if system is None:
            try:
                system = await self.snmp.get_system()
            except SnmpError as error:
                _logger.debug("%s baseline descr poll failed: %s", name, error)
                return
        self.tree.save_version(name, system.descr)
