"""
Replaying the event log, and the reports built from it.

The daemon only records what it saw; every conclusion is drawn here by replaying a day, or a
month, of events on top of the start-of-day version baseline.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Optional

from vwatcher.report.software import pretty_desc, version_sort_key
from vwatcher.store import LogEntry, LogTree, eventlog, parse_timestamp

# Two reload timestamps this close together describe the same reboot
RESTART_DEDUPE_SECONDS = 5
# A restart with one of these reasons is unremarkable if the device was upgraded
EXPECTED_RESTART_REASON = re.compile(r"power-on|reload")


@dataclass
class DeviceHistory:
    """
    What replaying the log said about one device:
    the version it was on at the start of the period, the one it ended on, and every step in between.
    """

    name: str
    baseline: str = ""
    current: str = ""
    upgrades: list[str] = field(default_factory=list)
    restarts: Optional[int] = None
    last_reload: Optional[str] = None
    reason: Optional[str] = None
    uptime: Optional[str] = None
    restarted: bool = False

    @property
    def restart_count(self) -> int:
        return self.restarts or 0

    @property
    def upgraded(self) -> bool:
        return bool(self.upgrades)

    def upgrade_steps(self) -> Iterator[tuple[str, str]]:
        """Yield the (from, to) pair of every step in the upgrade chain"""
        previous = self.baseline
        for version in self.upgrades:
            yield previous, version
            previous = version


@dataclass
class RestartEvent:
    """A restart whose reason the device reported"""

    device: str
    reload_date: str
    reason: str


class Replay:
    def __init__(self, baseline: Optional[Mapping[str, str]] = None):
        self.devices: dict[str, DeviceHistory] = {}
        self.restart_events: list[RestartEvent] = []
        self._reload_pending = False
        for name, descr in (baseline or {}).items():
            device = self.get(name)
            device.baseline = device.current = descr

    def get(self, name: str) -> DeviceHistory:
        if name not in self.devices:
            self.devices[name] = DeviceHistory(name=name)
        return self.devices[name]

    def sorted_devices(self) -> list[DeviceHistory]:
        return [self.devices[name] for name in sorted(self.devices)]

    def replay(self, entries: Iterable[LogEntry]) -> "Replay":
        for entry in entries:
            self.apply(entry)
        return self

    def apply(self, entry: LogEntry) -> None:
        if entry.event == eventlog.RELOADED:
            self._reloaded(entry)
        elif entry.event == eventlog.UPTIME:
            self.get(entry.device).uptime = entry.value
        elif entry.event == eventlog.SOFTWARE:
            self.observe_software(entry.device, entry.value)
        elif entry.event == eventlog.RESTART_REASON:
            self._restart_reason(entry)

    def observe_software(self, device_name: str, descr: str) -> None:
        device = self.get(device_name)
        if device.current and device.current != descr:
            device.upgrades.append(descr)
        device.current = descr

    def observe_descrs(self, descrs: Mapping[str, str]) -> None:
        """
        Treat a day's version baseline as observations.

        A device can be upgraded without vwatcher catching the reboot, comparing
        consecutive days' baselines finds those too.
        """
        for name in sorted(descrs):
            self.observe_software(name, descrs[name])

    def flush_upgrades(self, label: str = "") -> list[str]:
        """
        Format and clear the upgrades seen so far, then update the baseline.

        This is how the monthly report attributes each upgrade to its own day,
        with `label` as the report's first column, e.g. `Sep 04`.
        """
        lines = []
        for device in self.sorted_devices():
            lines += [
                f"{label:<6} {device.name:<15} from {pretty_desc(previous):<23} to {pretty_desc(version):<23}"
                for previous, version in device.upgrade_steps()
            ]
            device.upgrades.clear()
        for device in self.devices.values():
            device.baseline = device.current
        return lines

    def _reloaded(self, entry: LogEntry) -> None:
        """
        Count a restart, unless the device booted on some earlier day.

        The log re-states a device's boot time every time vwatcher restarts, so
        only a boot dated the same day as the log line is news, and two boot
        times seconds apart are the same reboot seen twice.
        """
        device = self.get(entry.device)
        boot_time = parse_timestamp(entry.value)
        if boot_time and entry.timestamp and boot_time.date() == entry.timestamp.date():
            device.restarted = True
            if device.restarts is None:
                device.restarts = 1
                self._reload_pending = True
            elif device.last_reload and not _close_times(entry.value, device.last_reload):
                device.restarts += 1
                self._reload_pending = True
        device.last_reload = entry.value

    def _restart_reason(self, entry: LogEntry) -> None:
        device = self.get(entry.device)
        device.reason = entry.value
        if self._reload_pending:
            self.restart_events.append(
                RestartEvent(device=device.name, reload_date=device.last_reload or "", reason=entry.value)
            )
        self._reload_pending = False


def _close_times(one: str, other: str, seconds: int = RESTART_DEDUPE_SECONDS) -> bool:
    first, second = parse_timestamp(one), parse_timestamp(other)
    if not first or not second:
        return False
    return abs((first - second).total_seconds()) <= seconds


def format_uptime(centiseconds: Optional[int]) -> str:
    """Format a sysUpTime value as `0d  0:00:00.00`"""
    if centiseconds is None:
        return ""
    hundredths, seconds = centiseconds % 100, centiseconds // 100
    minutes, seconds = seconds // 60, seconds % 60
    hours, minutes = minutes // 60, minutes % 60
    days, hours = hours // 24, hours % 24
    return f"{days}d {hours:2d}:{minutes:02d}:{seconds:02d}.{hundredths:02d}"


def day_report(tree: LogTree, month: str, day: str) -> str:
    """Report on one rotated day, e.g. `day_report(tree, "2026-09", "04")`"""
    day_dir = tree.day_dir(month, day)
    replay = Replay(tree.descrs(day_dir)).replay(tree.entries(day_dir))
    return format_day_report(replay)


def format_day_report(replay: Replay) -> str:
    """
    Format the upgrades and the restarts of a single day.

    Returns the empty string if neither happened, to prevent the mailing of an empty report.
    """
    lines: list[str] = []
    devices = replay.sorted_devices()

    if upgraded := [device for device in devices if device.upgraded]:
        lines += ["Upgraded routers:", ""]
        for device in upgraded:
            lines += [
                f"{device.name:<15} from {pretty_desc(previous):>15} to {pretty_desc(version):>15} "
                f"({device.restart_count})"
                for previous, version in device.upgrade_steps()
            ]
        lines.append("")

    if restarted := [device for device in devices if device.restarted and not _restart_is_boring(device)]:
        lines += ["Restarted/crashed routers:", ""]
        for device in restarted:
            lines.append(
                f"{device.name:<15} ({device.restart_count} total) current version {pretty_desc(device.current)}"
            )
            lines.append(f"{device.name:<15} last restart {device.last_reload}")
            if device.reason:
                lines.append(f"{device.name:<15} restart reason: {device.reason}")
    return "\n".join(lines) + "\n" if lines else ""


def _restart_is_boring(device: DeviceHistory) -> bool:
    """Decide whether a restart is just the reboot an upgrade needed"""
    return device.upgraded and (not device.reason or bool(EXPECTED_RESTART_REASON.search(device.reason)))


def month_upgrade_report(tree: LogTree, month: str) -> str:
    """Report every upgrade of one month, one line per version step, in date order"""
    day_dirs = tree.day_dirs(month)
    if not day_dirs:
        return ""
    lines: list[str] = []
    replay = Replay(tree.descrs(day_dirs[0]))
    for day_dir in day_dirs:
        replay.observe_descrs(tree.descrs(day_dir))
        replay.replay(tree.entries(day_dir))
        lines += replay.flush_upgrades(label=_day_label(month, day_dir.name))
    return "".join(line + "\n" for line in lines)


def month_restart_report(tree: LogTree, month: str) -> str:
    """Report every restart of one month for which a device reported a reason"""
    replay = Replay()
    for day_dir in tree.day_dirs(month):
        replay.replay(tree.entries(day_dir))
    lines = []
    for event in replay.restart_events:
        fields = event.reload_date.split()
        when = " ".join(fields[1:4]) if len(fields) >= 4 else event.reload_date
        lines.append(f"{when} {event.device:<15}: {event.reason}")
    return "".join(line + "\n" for line in lines)


def _todays_devices(tree: LogTree) -> list[DeviceHistory]:
    """Replay today, giving every device the version it now runs"""
    return list(Replay(tree.descrs()).replay(tree.entries()).devices.values())


def _by_version(device: DeviceHistory) -> tuple:
    return version_sort_key(device.name, pretty_desc(device.current))


def current_versions(tree: LogTree) -> str:
    """Report today's software version per device, ordered by version"""
    devices = sorted(_todays_devices(tree), key=_by_version)
    return "".join(f"{device.name:<25} {pretty_desc(device.current)}\n" for device in devices)


def uptime_report(tree: LogTree, by_uptime: bool = False) -> str:
    """Report today's software version and uptime per device"""
    devices = _todays_devices(tree)
    uptimes = tree.uptimes()
    devices.sort(key=(lambda device: uptimes.get(device.name, 0)) if by_uptime else _by_version)
    return "".join(
        f"{device.name:<25} {pretty_desc(device.current)[:36]:<36} {format_uptime(uptimes.get(device.name)):>16}\n"
        for device in devices
    )


def _day_label(month: str, day: str) -> str:
    """Turn `("2026-09", "04")` into `Sep 04`, the label the monthly report uses"""
    try:
        when = datetime.strptime(f"{month}-{day}", "%Y-%m-%d")
    except ValueError:
        return day
    return f"{eventlog.MONTH_NAMES[when.month - 1]} {when.day:02d}"


def rotate_and_report(tree: LogTree, when=None) -> str:
    """
    Rotate today's logs away and report on the day just ended.

    The report is also written into the rotated day's directory, as `report`.
    """
    day_dir: Path = tree.rotate(when)
    report = format_day_report(Replay(tree.descrs(day_dir)).replay(tree.entries(day_dir)))
    (day_dir / "report").write_text(report)
    return report
