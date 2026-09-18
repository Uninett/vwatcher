"""Tests for the event log format"""

from datetime import datetime

from vwatcher.store.eventlog import (
    RELOADED,
    SOFTWARE,
    EventLog,
    format_timestamp,
    normalize_descr,
    parse_log,
    parse_timestamp,
)

TIMESTAMP = "Thu Sep 04 12:00:00 2025"


class TestFormatTimestamp:
    def test_should_format_the_way_the_log_reader_expects(self):
        assert format_timestamp(datetime(2025, 9, 4, 12, 0, 0)) == TIMESTAMP

    def test_should_zero_pad_the_day_of_month(self):
        assert format_timestamp(datetime(2025, 9, 4)).startswith("Thu Sep 04")

    def test_should_not_depend_on_the_locale(self):
        # Month and day names are ours, not the C library's
        assert "Sep" in format_timestamp(datetime(2025, 9, 4))


class TestParseTimestamp:
    def test_should_parse_log_timestamp(self):
        assert parse_timestamp(TIMESTAMP) == datetime(2025, 9, 4, 12, 0, 0)

    def test_should_ignore_time_zone_in_the_middle(self):
        assert parse_timestamp("Mon Jul 19 12:40:53 MET DST 1996") == datetime(1996, 7, 19, 12, 40, 53)

    def test_should_return_none_for_garbage(self):
        assert parse_timestamp("no timestamp here") is None

    def test_should_round_trip_formatted_timestamp(self):
        when = datetime(2026, 2, 28, 23, 59, 59)
        assert parse_timestamp(format_timestamp(when)) == when


class TestNormalizeDescr:
    def test_should_strip_carriage_returns(self):
        assert normalize_descr("one\r\ntwo\r\n") == "one\ntwo"

    def test_should_strip_surrounding_newlines(self):
        assert normalize_descr("\nversion 1\n") == "version 1"


class TestEventLogWrite:
    def test_should_write_single_line_value_after_colon(self, tmp_path, clock):
        log = EventLog(tmp_path / "ver-watch.log", clock=clock)
        log.write_event("example-gw", RELOADED, TIMESTAMP)

        assert log.path.read_text() == f"{TIMESTAMP}  example-gw {RELOADED}: {TIMESTAMP}\n"

    def test_should_fence_multi_line_value(self, tmp_path, clock):
        log = EventLog(tmp_path / "ver-watch.log", clock=clock)
        log.write_event("example-gw", SOFTWARE, "first line\nsecond line")

        assert log.path.read_text() == (f"{TIMESTAMP}  example-gw {SOFTWARE}: #\nfirst line\nsecond line\n#\n")

    def test_should_write_bare_message_without_colon(self, tmp_path, clock):
        log = EventLog(tmp_path / "ver-watch.log", clock=clock)
        log.write("example-gw: uptime wraparound (or close)")

        assert log.path.read_text() == f"{TIMESTAMP}  example-gw: uptime wraparound (or close)\n"

    def test_should_append_to_existing_log(self, tmp_path, clock):
        log = EventLog(tmp_path / "ver-watch.log", clock=clock)
        log.write("one")
        log.write("two")

        assert len(log.path.read_text().splitlines()) == 2


class TestEventLogRead:
    def test_should_read_back_what_it_wrote(self, tmp_path, clock):
        log = EventLog(tmp_path / "ver-watch.log", clock=clock)
        log.write_event("example-gw", SOFTWARE, "IOS one\nIOS two")

        entry = next(iter(log.get_entries()))
        assert entry.device == "example-gw"
        assert entry.event == SOFTWARE
        assert entry.value == "IOS one\nIOS two"
        assert entry.timestamp == datetime(2025, 9, 4, 12, 0, 0)

    def test_when_the_log_is_missing_then_it_should_yield_nothing(self, tmp_path):
        assert list(EventLog(tmp_path / "absent.log").get_entries()) == []

    def test_should_skip_diagnostic_lines(self):
        lines = [
            f"{TIMESTAMP}  example-gw: uptime poll returned noResponse",
            f"{TIMESTAMP}  example-gw {RELOADED}: {TIMESTAMP}",
            "Config file polldevs.cf updated, rereading",
        ]
        assert [entry.event for entry in parse_log(lines)] == [RELOADED]

    def test_should_not_mistake_fenced_value_for_events(self):
        lines = [
            f"{TIMESTAMP}  example-gw {SOFTWARE}: #",
            f"{TIMESTAMP}  a line that looks like a log line {RELOADED}: nope",
            "#",
            f"{TIMESTAMP}  example-gw {RELOADED}: {TIMESTAMP}",
        ]
        assert [entry.event for entry in parse_log(lines)] == [SOFTWARE, RELOADED]
