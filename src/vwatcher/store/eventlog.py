"""Reading and writing `ver-watch.log`"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional, Union

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTH_NUMBERS = {name: number for number, name in enumerate(MONTH_NAMES, start=1)}

_logger = logging.getLogger(__name__)

# The events the reports care about.  Anything else in the log is diagnostics.
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


def format_timestamp(timestamp: datetime) -> str:
    """
    Format a naive local timestamp the way the log spells it.

    Built by hand rather than with `strftime`, so that the C locale cannot
    change how the log reads.
    """
    return (
        f"{DAY_NAMES[when.weekday()]} {MONTH_NAMES[when.month - 1]} {when.day:02d} "
        f"{when.hour:02d}:{when.minute:02d}:{when.second:02d} {when.year}"
    )


def parse_timestamp(value: str) -> Optional[datetime]:
    """
    Parse a log timestamp, ignoring any time zone in it.

    Accepts both `Thu Sep 04 09:45:01 2026` and the older
    `Mon Jul 19 12:40:53 MET DST 1996`, which is what `unctime.pl` did.
    Returns None for anything it cannot read as a date.
    """
    fields = value.split()
    if len(fields) < 5 or fields[1] not in MONTH_NUMBERS:
        return None
    try:
        hour, minute, second = (int(part) for part in fields[3].split(":"))
        return datetime(
            year=int(fields[-1]),
            month=MONTH_NUMBERS[fields[1]],
            day=int(fields[2]),
            hour=hour,
            minute=minute,
            second=second,
        )
    except ValueError:
        return None


def normalize_descr(value: str) -> str:
    """Clean up a device string so two sightings of one version compare equal"""
    return value.replace("\r", "").strip("\n")


def parse_log(lines: Iterable[str]) -> Iterator[LogEntry]:
    """
    Parse an event log, yielding the recognized events and skipping noise.

    An entry whose timestamp will not parse is still yielded, so that the
    version it reports is not lost.
    """
    lines = iter(lines)
    for line in lines:
        if not (match := _LINE.match(line.rstrip("\n"))):
            continue
        value = match.group("value")
        if value == MULTILINE_MARKER:
            value = "\n".join(_read_block(lines))
        timestamp = parse_timestamp(match.group("timestamp"))
        if timestamp is None:
            _logger.warning("Unreadable timestamp in %s: %r", match.group("device"), match.group("timestamp"))
        yield LogEntry(
            timestamp=timestamp,
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
    """
    Append events to `ver-watch.log`.

    The file is opened per message, so that rotation
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
        """Append one of the `EVENTS` the reports read, f.ex `reloaded`"""
        self.write(f"{device} {event}", value)

    def entries(self) -> Iterator[LogEntry]:
        """Read the log back, as the reports do, skipping what they ignore"""
        if not self.path.exists():
            return
        with open(self.path, "r", errors="replace") as log:
            yield from parse_log(log)
