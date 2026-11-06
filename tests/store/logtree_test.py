"""Tests for the log directory layout and its rotation"""

from datetime import date

import pytest

from vwatcher.store.logtree import LogTree


class TestEnsure:
    def test_should_create_the_skeleton(self, tmp_path):
        tree = LogTree(tmp_path / "logs")
        tree.ensure_today()

        assert (tree.path_today / "descs").is_dir()
        assert (tree.path_today / "uptime").is_dir()
        assert tree.log_file.is_file()

    def test_should_not_disturb_existing_log(self, log_tree):
        log_tree.log.write("an event")
        log_tree.ensure_today()

        assert log_tree.log_file.read_text().endswith("an event\n")


class TestDescrs:
    def test_should_round_trip_version(self, log_tree):
        log_tree.save_version("example-gw", "IOS 15.2\r\n")

        assert log_tree.get_descrs() == {"example-gw": "IOS 15.2"}

    def test_should_report_recorded_baseline_as_present(self, log_tree):
        log_tree.save_version("example-gw", "IOS 15.2")

        assert log_tree.has_baseline_version("example-gw")

    def test_when_baseline_is_missing_then_it_should_be_reported_absent(self, log_tree):
        assert not log_tree.has_baseline_version("example-gw")

    def test_should_report_empty_baseline_as_absent(self, log_tree):
        log_tree.get_descr_file("example-gw").touch()

        assert not log_tree.has_baseline_version("example-gw")

    def test_when_the_directory_is_gone_then_it_should_be_empty(self, tmp_path):
        assert LogTree(tmp_path / "nowhere").get_descrs() == {}


class TestUptimes:
    def test_should_round_trip_uptime(self, log_tree):
        log_tree.save_uptime("example-gw", 360000)

        assert log_tree.get_uptimes() == {"example-gw": 360000}

    def test_should_skip_unreadable_uptime(self, log_tree):
        log_tree.get_uptime_file("example-gw").write_text("not a number\n")

        assert log_tree.get_uptimes() == {}


class TestRotate:
    def test_should_move_the_day_into_its_own_directory(self, log_tree):
        log_tree.log.write("an event")
        log_tree.save_version("example-gw", "IOS 15.2")
        log_tree.save_uptime("example-gw", 360000)

        day_dir = log_tree.rotate(date(2025, 9, 4))

        assert day_dir == log_tree.get_day_dir("2025-09", "04")
        assert (day_dir / "ver-watch.log").read_text().endswith("an event\n")
        assert log_tree.get_descrs(day_dir) == {"example-gw": "IOS 15.2"}
        assert log_tree.get_uptimes(day_dir) == {"example-gw": 360000}

    def test_should_start_fresh_day(self, log_tree):
        log_tree.log.write("an event")
        log_tree.save_version("example-gw", "IOS 15.2")

        log_tree.rotate(date(2025, 9, 4))

        assert log_tree.log_file.read_text() == ""
        assert log_tree.get_descrs() == {}
        assert log_tree.get_uptimes() == {}

    def test_should_refuse_when_day_was_already_rotated_into(self, log_tree):
        log_tree.log.write("first event")
        day_dir = log_tree.rotate(date(2025, 9, 4))
        log_tree.log.write("second event")

        with pytest.raises(FileExistsError):
            log_tree.rotate(date(2025, 9, 4))

        assert (day_dir / "ver-watch.log").read_text().endswith("first event\n")
        assert log_tree.log_file.read_text().endswith("second event\n")
        assert not (day_dir / "descs" / "descs").exists()

    def test_should_default_to_today(self, log_tree):
        assert log_tree.rotate() == log_tree.get_day_dir(f"{date.today():%Y-%m}", f"{date.today():%d}")


class TestDayDirs:
    def test_should_list_months_days_in_order(self, log_tree):
        for day in ("03", "01", "02"):
            log_tree.get_day_dir("2025-09", day).mkdir(parents=True)

        assert [path.name for path in log_tree.get_day_dirs("2025-09")] == ["01", "02", "03"]

    def test_when_month_was_never_logged_then_it_should_raise(self, log_tree):
        with pytest.raises(OSError):
            log_tree.get_day_dirs("1999-12")
