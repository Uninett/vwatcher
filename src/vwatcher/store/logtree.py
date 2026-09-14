"""
The on-disk layout of vwatcher's logs and state.

    <root>/today/ver-watch.log      today's events
    <root>/today/descs/<device>     the device's software version as of today
    <root>/today/uptime/<device>    the device's last seen sysUpTime
    <root>/YYYY-MM/DD/...           the same three, rotated at the end of a day

`descs` serves as the start-of-day baseline
"""

import shutil
import time
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Iterator, Optional, Union

from vwatcher.store.eventlog import EventLog, LogEntry, normalize_descr

LOG_NAME = "ver-watch.log"
DESCS_DIR = "descs"
UPTIME_DIR = "uptime"
TODAY_DIR = "today"


class LogTree:
    def __init__(self, root: Union[str, Path], clock=time.time):
        self.root = Path(root)
        self.log = EventLog(self.log_file, clock=clock)

    @property
    def path_today(self) -> Path:
        return self.root / TODAY_DIR

    @property
    def log_file(self) -> Path:
        return self.path_today / LOG_NAME

    def get_descr_file(self, device: str) -> Path:
        """Locate where today's software version for a device is recorded"""
        return self.path_today / DESCS_DIR / device

    def get_uptime_file(self, device: str) -> Path:
        """Locate where today's last seen sysUpTime for a device is recorded"""
        return self.path_today / UPTIME_DIR / device

    def get_day_dir(self, year_month: str, day: str) -> Path:
        """Locate one rotated day, e.g. `get_day_dir("2026-09", "04")`"""
        return self.root / year_month / day

    def get_day_dirs(self, year_month: str) -> list[Path]:
        """
        List one month's rotated day directories, in date order

        :raises OSError: If the month was never rotated into.
        """
        return sorted(path for path in (self.root / year_month).iterdir() if path.is_dir())

    # State written by the poll task

    def ensure_today(self) -> None:
        """Create today's directories and log file, unless they already exist"""
        (self.path_today / DESCS_DIR).mkdir(parents=True, exist_ok=True)
        (self.path_today / UPTIME_DIR).mkdir(parents=True, exist_ok=True)
        self.log_file.touch()

    def has_baseline_version(self, device: str) -> bool:
        """
        Decide whether today's baseline version for a device is recorded.

        An empty file counts as unrecorded, so a failed write is retried.
        """
        path = self.get_descr_file(device)
        return path.exists() and path.stat().st_size > 0

    def save_version(self, device: str, descr: str) -> None:
        """Record the software version a device reports as today's baseline"""
        self.get_descr_file(device).write_text(normalize_descr(descr) + "\n")

    def save_uptime(self, device: str, uptime: int) -> None:
        """Record the last sysUpTime seen for a device in centiseconds"""
        self.get_uptime_file(device).write_text(f"{uptime}\n")

    # State read by the reports

    def get_descrs(self, day: Optional[Path] = None) -> dict[str, str]:
        """Read the start-of-day software version per device, defaulting to today"""
        return {
            path.name: normalize_descr(path.read_text(errors="replace"))
            for path in _files_in((day or self.path_today) / DESCS_DIR)
        }

    def get_uptimes(self, day: Optional[Path] = None) -> dict[str, int]:
        """Read the last sysUpTime per device in centiseconds, defaulting to today"""
        uptimes = {}
        for path in _files_in((day or self.path_today) / UPTIME_DIR):
            with suppress(ValueError):
                uptimes[path.name] = int(path.read_text().strip())
        return uptimes

    def get_entries(self, day: Optional[Path] = None) -> Iterator[LogEntry]:
        """Read the events logged on a day, defaulting to today"""
        return EventLog((day or self.path_today) / LOG_NAME).get_entries()

    def rotate(self, when: Optional[date] = None) -> Path:
        """
        Move path_today's log into a folder for the specific date on the `YYYY-MM/DD` format.
        Afterwards it starts a fresh day.
        """
        when = when or date.today()
        target = self.get_day_dir(f"{when:%Y-%m}", f"{when:%d}")
        target.mkdir(parents=True, exist_ok=True)
        for item in self.path_today.iterdir():
            shutil.move(str(item), str(target / item.name))
        self.ensure_today()
        return target


def _files_in(directory: Path) -> Iterator[Path]:
    """Yield the files of a directory, or nothing if it does not exist"""
    if not directory.is_dir():
        return
    for path in sorted(directory.iterdir()):
        if path.is_file():
            yield path
