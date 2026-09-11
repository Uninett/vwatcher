"""Tests for log replay and the reports built on it"""

from datetime import datetime

import pytest

from tests.conftest import CISCO_DESCR, CISCO_DESCR_UPGRADED
from vwatcher.report.reports import (
    Replay,
    current_versions,
    day_report,
    format_day_report,
    format_uptime,
    month_restart_report,
    month_upgrade_report,
    rotate_and_report,
    uptime_report,
)
from vwatcher.store import RELOADED, RESTART_REASON, SOFTWARE, UPTIME, LogEntry, format_timestamp

DAY = datetime(2025, 9, 4, 12, 0, 0)
BOOTED = datetime(2025, 9, 4, 11, 0, 0)


def entry(event, value, device="example-gw", when=DAY):
    return LogEntry(timestamp=when, device=device, event=event, value=value)


def restart(booted=BOOTED, device="example-gw", when=DAY, reason=None, software=None):
    """Logged entries when a restart occurs"""
    entries = [
        entry(RELOADED, format_timestamp(booted), device, when),
        entry(UPTIME, str(int((when - booted).total_seconds()) * 100), device, when),
    ]
    if software:
        entries.append(entry(SOFTWARE, software, device, when))
    if reason:
        entries.append(entry(RESTART_REASON, reason, device, when))
    return entries


def write_day(tree, month, day, entries=(), descrs=None):
    """Populates an already rotated day directory, stamping each entry with its own time"""
    day_dir = tree.day_dir(month, day)
    (day_dir / "descs").mkdir(parents=True, exist_ok=True)
    (day_dir / "uptime").mkdir(parents=True, exist_ok=True)
    for name, descr in (descrs or {}).items():
        (day_dir / "descs" / name).write_text(descr + "\n")
    (day_dir / "ver-watch.log").write_text(
        "".join(f"{format_timestamp(item.timestamp)}  {item.device} {item.event}: {item.value}\n" for item in entries)
    )
    return day_dir


class TestReplayRestarts:
    def test_when_a_restart_happened_today_then_it_should_be_counted(self):
        replay = Replay().replay(restart())

        assert replay.get("example-gw").restart_count == 1
        assert replay.get("example-gw").restarted

    def test_should_not_count_a_restart_from_an_earlier_day(self):
        replay = Replay().replay(restart(booted=datetime(2025, 9, 3, 23, 0, 0)))

        assert replay.get("example-gw").restart_count == 0
        assert not replay.get("example-gw").restarted

    def test_should_count_two_distinct_restarts_separately(self):
        replay = Replay().replay(restart(booted=BOOTED) + restart(booted=datetime(2025, 9, 4, 11, 30, 0)))

        assert replay.get("example-gw").restart_count == 2

    def test_should_treat_boot_times_seconds_apart_as_one_restart(self):
        # Compute boot times may differ by a second or two
        replay = Replay().replay(restart(booted=BOOTED) + restart(booted=BOOTED.replace(second=3)))

        assert replay.get("example-gw").restart_count == 1

    def test_when_a_boot_time_cannot_be_parsed_then_it_should_not_dedupe(self):
        replay = Replay().replay(restart(booted=BOOTED) + [entry(RELOADED, "who knows when")] + restart(booted=BOOTED))

        assert replay.get("example-gw").restart_count == 2

    def test_should_record_the_reported_restart_reason(self):
        replay = Replay().replay(restart(reason="power-on"))

        assert replay.get("example-gw").reason == "power-on"

    def test_should_record_the_last_uptime_seen(self):
        replay = Replay().replay(restart())

        assert replay.get("example-gw").uptime == "360000"


class TestReplayUpgrades:
    def test_should_detect_a_version_change_against_the_baseline(self):
        replay = Replay({"example-gw": CISCO_DESCR}).replay([entry(SOFTWARE, CISCO_DESCR_UPGRADED)])

        device = replay.get("example-gw")
        assert device.upgraded
        assert device.upgrades == [CISCO_DESCR_UPGRADED]

    def test_should_not_report_an_unchanged_version_as_an_upgrade(self):
        replay = Replay({"example-gw": CISCO_DESCR}).replay([entry(SOFTWARE, CISCO_DESCR)])

        assert not replay.get("example-gw").upgraded

    def test_should_not_report_a_first_seen_version_as_an_upgrade(self):
        replay = Replay().replay([entry(SOFTWARE, CISCO_DESCR)])

        assert not replay.get("example-gw").upgraded
        assert replay.get("example-gw").current == CISCO_DESCR

    def test_should_chain_repeated_upgrades(self):
        replay = Replay({"example-gw": CISCO_DESCR}).replay(
            [entry(SOFTWARE, CISCO_DESCR_UPGRADED), entry(SOFTWARE, CISCO_DESCR)]
        )

        assert replay.get("example-gw").upgrades == [CISCO_DESCR_UPGRADED, CISCO_DESCR]

    def test_should_detect_an_upgrade_between_two_days_baselines(self):
        replay = Replay({"example-gw": CISCO_DESCR})
        replay.observe_descrs({"example-gw": CISCO_DESCR_UPGRADED})

        assert replay.get("example-gw").upgraded


class TestDayReport:
    def test_should_report_an_upgrade(self):
        replay = Replay({"example-gw": CISCO_DESCR}).replay(restart(reason="reload", software=CISCO_DESCR_UPGRADED))
        report = format_day_report(replay)

        assert "Upgraded routers:" in report
        assert "Cisco-IOS 15.2(4)S7" in report
        assert "Cisco-IOS 15.2(4)S8" in report
        assert "(1)" in report

    def test_should_report_one_line_per_version_step(self):
        upgraded_again = CISCO_DESCR_UPGRADED.replace("S8", "S9")
        replay = Replay({"example-gw": CISCO_DESCR}).replay(
            [entry(SOFTWARE, CISCO_DESCR_UPGRADED), entry(SOFTWARE, upgraded_again)]
        )

        steps = [line for line in format_day_report(replay).splitlines() if " from " in line]

        assert len(steps) == 2
        assert "to Cisco-IOS 15.2(4)S8" in steps[0]
        assert "from Cisco-IOS 15.2(4)S8" in steps[1]
        assert "to Cisco-IOS 15.2(4)S9" in steps[1]

    def test_should_report_a_crash(self):
        replay = Replay({"example-gw": CISCO_DESCR}).replay(restart(reason="watchdog timeout"))
        report = format_day_report(replay)

        assert "Restarted/crashed routers:" in report
        assert "example-gw      (1 total) current version Cisco-IOS 15.2(4)S7" in report
        assert "restart reason: watchdog timeout" in report

    def test_should_not_repeat_the_reboot_an_upgrade_needed(self):
        replay = Replay({"example-gw": CISCO_DESCR}).replay(restart(reason="reload", software=CISCO_DESCR_UPGRADED))

        assert "Restarted/crashed routers:" not in format_day_report(replay)

    def test_when_a_crashed_device_was_also_upgraded_then_both_should_be_reported(self):
        replay = Replay({"example-gw": CISCO_DESCR}).replay(
            restart(reason="watchdog timeout", software=CISCO_DESCR_UPGRADED)
        )
        report = format_day_report(replay)

        assert "Upgraded routers:" in report
        assert "Restarted/crashed routers:" in report

    def test_when_the_day_was_quiet_then_it_should_be_empty(self):
        assert format_day_report(Replay({"example-gw": CISCO_DESCR})) == ""

    def test_should_report_devices_in_name_order(self):
        replay = Replay({"gw-b": CISCO_DESCR, "gw-a": CISCO_DESCR}).replay(
            restart(device="gw-b", reason="crash") + restart(device="gw-a", reason="crash")
        )
        report = format_day_report(replay)

        assert report.index("gw-a") < report.index("gw-b")

    def test_should_read_a_rotated_day_off_disk(self, log_tree):
        write_day(
            log_tree,
            "2025-09",
            "04",
            entries=restart(reason="power-on", software=CISCO_DESCR_UPGRADED),
            descrs={"example-gw": CISCO_DESCR},
        )

        assert "Upgraded routers:" in day_report(log_tree, "2025-09", "04")


class TestMonthUpgradeReport:
    def test_should_report_one_line_per_upgrade_dated_by_day(self, log_tree):
        write_day(log_tree, "2025-09", "01", descrs={"example-gw": CISCO_DESCR})
        write_day(
            log_tree,
            "2025-09",
            "02",
            entries=restart(software=CISCO_DESCR_UPGRADED),
            descrs={"example-gw": CISCO_DESCR},
        )
        report = month_upgrade_report(log_tree, "2025-09")

        assert report.splitlines() == [
            "Sep 02 example-gw      from Cisco-IOS 15.2(4)S7 C7200-ADVENTERPRISEK9-M "
            "to Cisco-IOS 15.2(4)S8 C7200-ADVENTERPRISEK9-M"
        ]
        assert report.endswith("\n")

    def test_should_pad_each_column_the_way_old_zino_did(self, log_tree):
        write_day(log_tree, "2025-09", "01", descrs={"example-gw": "JunOS 20.4R3.8 mx480"})
        write_day(log_tree, "2025-09", "02", descrs={"example-gw": "JunOS 21.2R3.8 mx480"})

        assert month_upgrade_report(log_tree, "2025-09") == (
            f"{'Sep 02':<6} {'example-gw':<15} from {'JunOS 20.4R3.8 mx480':<23} to {'JunOS 21.2R3.8 mx480':<23}\n"
        )

    def test_should_catch_an_upgrade_vwatch_only_saw_in_the_baselines(self, log_tree):
        write_day(log_tree, "2025-09", "01", descrs={"example-gw": CISCO_DESCR})
        write_day(log_tree, "2025-09", "02", descrs={"example-gw": CISCO_DESCR_UPGRADED})
        report = month_upgrade_report(log_tree, "2025-09")

        assert len(report.splitlines()) == 1
        assert report.startswith("Sep 02")

    def test_when_a_months_days_were_quiet_then_it_should_report_nothing(self, log_tree):
        write_day(log_tree, "2025-09", "01", descrs={"example-gw": CISCO_DESCR})
        write_day(log_tree, "2025-09", "02", descrs={"example-gw": CISCO_DESCR})

        assert month_upgrade_report(log_tree, "2025-09") == ""

    def test_when_a_month_has_no_rotated_days_then_it_should_report_nothing(self, log_tree):
        (log_tree.root / "2025-09").mkdir(parents=True)

        assert month_upgrade_report(log_tree, "2025-09") == ""

    def test_when_a_month_was_never_logged_then_it_should_raise(self, log_tree):
        with pytest.raises(OSError):
            month_upgrade_report(log_tree, "1999-12")

    def test_should_label_an_unparseable_day_with_its_directory_name(self, log_tree):
        write_day(log_tree, "2025-09", "01", descrs={"example-gw": CISCO_DESCR})
        write_day(log_tree, "2025-09", "bogus", descrs={"example-gw": CISCO_DESCR_UPGRADED})

        assert month_upgrade_report(log_tree, "2025-09").startswith("bogus ")


class TestMonthRestartReport:
    def test_should_report_every_restart_with_a_reason(self, log_tree):
        write_day(log_tree, "2025-09", "01", entries=restart(reason="power-on"))
        write_day(
            log_tree,
            "2025-09",
            "02",
            entries=restart(booted=datetime(2025, 9, 2, 3, 0, 0), when=datetime(2025, 9, 2, 12, 0), reason="crash"),
        )
        report = month_restart_report(log_tree, "2025-09")

        assert report.splitlines() == [
            "Sep 04 11:00:00 example-gw     : power-on",
            "Sep 02 03:00:00 example-gw     : crash",
        ]

    def test_should_not_report_a_restart_it_saw_twice(self, log_tree):
        write_day(
            log_tree,
            "2025-09",
            "01",
            entries=restart(reason="power-on") + restart(booted=BOOTED.replace(second=2), reason="power-on"),
        )

        assert len(month_restart_report(log_tree, "2025-09").splitlines()) == 1

    def test_when_a_month_was_never_logged_then_it_should_raise(self, log_tree):
        with pytest.raises(OSError):
            month_restart_report(log_tree, "1999-12")


class TestCurrentVersions:
    def test_should_report_todays_baseline(self, log_tree):
        log_tree.save_descr("example-gw", CISCO_DESCR)

        assert current_versions(log_tree) == f"{'example-gw':<25} Cisco-IOS 15.2(4)S7 C7200-ADVENTERPRISEK9-M\n"

    def test_should_prefer_a_version_logged_today(self, log_tree):
        log_tree.save_descr("example-gw", CISCO_DESCR)
        log_tree.log.write_event("example-gw", SOFTWARE, CISCO_DESCR_UPGRADED)

        assert "15.2(4)S8" in current_versions(log_tree)

    def test_should_order_by_version(self, log_tree):
        log_tree.save_descr("gw-new", CISCO_DESCR_UPGRADED)
        log_tree.save_descr("gw-old", CISCO_DESCR)
        report = current_versions(log_tree)

        assert report.index("gw-old") < report.index("gw-new")


class TestUptimeReport:
    def test_should_report_the_version_and_the_uptime(self, log_tree):
        log_tree.save_descr("example-gw", CISCO_DESCR)
        log_tree.save_uptime("example-gw", 8640000)

        name, descr, uptime = "example-gw", "Cisco-IOS 15.2(4)S7 C7200-ADVENTERPR", "1d  0:00:00.00"
        assert uptime_report(log_tree) == f"{name:<25} {descr:<36} {uptime:>16}\n"

    def test_when_an_uptime_is_unknown_then_it_should_be_left_blank(self, log_tree):
        log_tree.save_descr("example-gw", CISCO_DESCR)

        assert uptime_report(log_tree).rstrip().endswith("C7200-ADVENTERPR")

    def test_should_be_orderable_by_uptime(self, log_tree):
        log_tree.save_descr("gw-old", CISCO_DESCR)
        log_tree.save_uptime("gw-old", 8640000)
        log_tree.save_descr("gw-new", CISCO_DESCR_UPGRADED)
        log_tree.save_uptime("gw-new", 100)
        report = uptime_report(log_tree, by_uptime=True)

        assert report.index("gw-new") < report.index("gw-old")


class TestFormatUptime:
    @pytest.mark.parametrize(
        "centiseconds, expected",
        [
            (0, "0d  0:00:00.00"),
            (100, "0d  0:00:01.00"),
            (8640000, "1d  0:00:00.00"),
            (360012, "0d  1:00:00.12"),
            (None, ""),
        ],
    )
    def test_should_format_centiseconds_as_days_and_time(self, centiseconds, expected):
        assert format_uptime(centiseconds) == expected


class TestRotateAndReport:
    def test_should_rotate_the_day_away_and_report_on_it(self, log_tree, clock):
        log_tree.save_descr("example-gw", CISCO_DESCR)
        for item in restart(reason="watchdog timeout", when=datetime.fromtimestamp(clock())):
            log_tree.log.write_event(item.device, item.event, item.value)

        report = rotate_and_report(log_tree, datetime.fromtimestamp(clock()).date())

        assert "Restarted/crashed routers:" in report
        assert log_tree.log_file.read_text() == ""
        assert (log_tree.day_dir("2025-09", "04") / "report").read_text() == report
