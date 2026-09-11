"""Tests for the log directory layout and its rotation"""

from datetime import date

import pytest

from vwatcher.store.logtree import LogTree


class TestEnsure:
    def test_should_create_the_skeleton(self, tmp_path):
        tree = LogTree(tmp_path / "logs")
        tree.ensure()

        assert (tree.today / "descs").is_dir()
        assert (tree.today / "uptime").is_dir()
        assert tree.log_file.is_file()

    def test_should_not_disturb_an_existing_log(self, log_tree):
        log_tree.log.write("an event")
        log_tree.ensure()

        assert log_tree.log_file.read_text().endswith("an event\n")


class TestDescrs:
    def test_should_round_trip_a_version(self, log_tree):
        log_tree.save_descr("example-gw", "IOS 15.2\r\n")

        assert log_tree.descrs() == {"example-gw": "IOS 15.2"}

    def test_should_report_a_recorded_baseline_as_present(self, log_tree):
        log_tree.save_descr("example-gw", "IOS 15.2")

        assert log_tree.has_descr("example-gw")

    def test_when_a_baseline_is_missing_then_it_should_be_reported_absent(self, log_tree):
        assert not log_tree.has_descr("example-gw")

    def test_should_report_an_empty_baseline_as_absent(self, log_tree):
        log_tree.descr_file("example-gw").touch()

        assert not log_tree.has_descr("example-gw")

    def test_when_the_directory_is_gone_then_it_should_be_empty(self, tmp_path):
        assert LogTree(tmp_path / "nowhere").descrs() == {}


class TestUptimes:
    def test_should_round_trip_an_uptime(self, log_tree):
        log_tree.save_uptime("example-gw", 360000)

        assert log_tree.uptimes() == {"example-gw": 360000}

    def test_should_skip_an_unreadable_uptime(self, log_tree):
        log_tree.uptime_file("example-gw").write_text("not a number\n")

        assert log_tree.uptimes() == {}


class TestRotate:
    def test_should_move_the_day_into_its_own_directory(self, log_tree):
        log_tree.log.write("an event")
        log_tree.save_descr("example-gw", "IOS 15.2")
        log_tree.save_uptime("example-gw", 360000)

        day_dir = log_tree.rotate(date(2025, 9, 4))

        assert day_dir == log_tree.day_dir("2025-09", "04")
        assert (day_dir / "ver-watch.log").read_text().endswith("an event\n")
        assert log_tree.descrs(day_dir) == {"example-gw": "IOS 15.2"}
        assert log_tree.uptimes(day_dir) == {"example-gw": 360000}

    def test_should_start_a_fresh_day(self, log_tree):
        log_tree.log.write("an event")
        log_tree.save_descr("example-gw", "IOS 15.2")

        log_tree.rotate(date(2025, 9, 4))

        assert log_tree.log_file.read_text() == ""
        assert log_tree.descrs() == {}
        assert log_tree.uptimes() == {}

    def test_should_default_to_today(self, log_tree):
        assert log_tree.rotate() == log_tree.day_dir(f"{date.today():%Y-%m}", f"{date.today():%d}")


class TestDayDirs:
    def test_should_list_a_months_days_in_order(self, log_tree):
        for day in ("03", "01", "02"):
            log_tree.day_dir("2025-09", day).mkdir(parents=True)

        assert [path.name for path in log_tree.day_dirs("2025-09")] == ["01", "02", "03"]

    def test_when_a_month_was_never_logged_then_it_should_raise(self, log_tree):
        with pytest.raises(OSError):
            log_tree.day_dirs("1999-12")
