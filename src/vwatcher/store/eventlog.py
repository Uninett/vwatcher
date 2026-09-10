"""Reading and writing `ver-watch.log`.

Each event is one line, and a multi-line value (a wrapped `sysDescr`) is
written between `#` sentinels:

    Thu Sep 04 09:45:01 2026  example-gw reloaded: Thu Sep 04 09:12:33 2026

Timestamps are naive local time, formatted without the C locale so that the log
reads the same however the daemon was started.
"""

import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional, Union

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTH_NUMBERS = {name: number for number, name in enumerate(MONTH_NAMES, start=1)}

#: The events the reports care about.  Anything else in the log is diagnostics.
RELOADED = "reloaded"
UPTIME = "uptime"
SOFTWARE = "software"
RESTART_REASON = "restart reason"
EVENTS = (RELOADED, UPTIME, SOFTWARE, RESTART_REASON)

MULTILINE_MARKER = "#"
_TIMESTAMP = r"\w{3} \w{3} \d{2} \d{2}:\d{2}:\d{2} \d{4}"
_LINE = re.compile(rf"^(?P<timestamp>{_TIMESTAMP})  (?P<device>\S+) (?P<event>{'|'.join(EVENTS)}): (?P<value>.*)$")


@dataclass
class LogEntry:
    timestamp: datetime
    device: str
    event: str
    value: str


def format_timestamp(when: datetime) -> str:
    return (
        f"{DAY_NAMES[when.weekday()]} {MONTH_NAMES[when.month - 1]} {when.day:02d} "
        f"{when.hour:02d}:{when.minute:02d}:{when.second:02d} {when.year}"
    )


def parse_timestamp(value: str) -> Optional[datetime]:
    """Parse a log timestamp, ignoring any time zone in it.

    Accepts both `Thu Sep 04 09:45:01 2026` and the older
    `Mon Jul 19 12:40:53 MET DST 1996`, which is what `unctime.pl` did.
    """
    fields = value.split()
    if len(fields) < 5 or fields[1] not in MONTH_NUMBERS:
        return None
    try:
        hour, minute, second = (int(part) for part in fields[3].split(":"))
        return datetime(int(fields[-1]), MONTH_NUMBERS[fields[1]], int(fields[2]), hour, minute, second)
    except ValueError:
        return None


def normalize_descr(value: str) -> str:
    """Clean up a device string so two sightings of one version compare equal."""
    return value.replace("\r", "").strip("\n")


def parse_log(lines: Iterable[str]) -> Iterator[LogEntry]:
    """Parse an event log, yielding the recognized events and skipping noise."""
    lines = iter(lines)
    for line in lines:
        if not (match := _LINE.match(line.rstrip("\n"))):
            continue
        value = match.group("value")
        if value == MULTILINE_MARKER:
            value = "\n".join(_read_block(lines))
        yield LogEntry(
            timestamp=parse_timestamp(match.group("timestamp")),
            device=match.group("device"),
            event=match.group("event"),
            value=normalize_descr(value),
        )


def _read_block(lines: Iterator[str]) -> Iterator[str]:
    for line in lines:
        line = line.rstrip("\n")
        if line == MULTILINE_MARKER:
            return
        yield line


class EventLog:
    """Append events to `ver-watch.log`.

    The file is opened per message, as the Tcl original did, so that rotation
    needs no cooperation from the running daemon.
    """

    def __init__(self, path: Union[str, Path], clock=time.time):
        self.path = Path(path)
        self.clock = clock

    def write(self, text: str, value: str = "") -> None:
        stamp = format_timestamp(datetime.fromtimestamp(self.clock()))
        if "\n" in value:
            message = f"{stamp}  {text}: {MULTILINE_MARKER}\n{value}\n{MULTILINE_MARKER}"
        elif value:
            message = f"{stamp}  {text}: {value}"
        else:
            message = f"{stamp}  {text}"
        with open(self.path, "a") as log:
            log.write(message + "\n")

    def event(self, device: str, event: str, value: str) -> None:
        self.write(f"{device} {event}", value)

    def entries(self) -> Iterator[LogEntry]:
        if not self.path.exists():
            return
        with open(self.path, "r", errors="replace") as log:
            yield from parse_log(log)
