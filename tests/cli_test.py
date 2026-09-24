"""Tests for the vwatcher commands"""

from types import SimpleNamespace

import pytest

from tests.conftest import CISCO_DESCR, CISCO_DESCR_UPGRADED
from vwatcher import cli
from vwatcher.config import LOG_DIRECTORY
from vwatcher.store import SOFTWARE, LogTree


@pytest.fixture
def mailed(monkeypatch):
    """Captures the reports the daily command would have mailed"""
    sent = []
    monkeypatch.setattr(cli, "send_report", lambda subject, body, settings: sent.append((subject, body)) or True)
    return sent


@pytest.fixture
def busy_day(log_tree, clock):
    """A day in which one device was upgraded"""
    log_tree.save_version("example-gw", CISCO_DESCR)
    log_tree.save_uptime("example-gw", 360000)
    log_tree.log.write_event("example-gw", SOFTWARE, CISCO_DESCR_UPGRADED)
    return log_tree


class TestVwDaily:
    def test_should_rotate_the_logs_and_mail_the_report(self, vwatcher_config, busy_day, mailed):
        assert cli.vw_daily(["--config-file", str(vwatcher_config), "--date", "2025-09-04"]) == 0

        subject, body = mailed[0]
        assert subject == "Device upgrade/restart report 2025-09-04"
        assert "Upgraded routers:" in body
        assert busy_day.log_file.read_text() == ""

    def test_should_save_the_report_in_the_correct_day(self, vwatcher_config, busy_day, mailed):
        cli.vw_daily(["--config-file", str(vwatcher_config), "--date", "2025-09-04"])

        assert "Upgraded routers:" in (busy_day.get_day_dir("2025-09", "04") / "report").read_text()

    def test_should_print_report_instead_of_mailing_it_when_promted(self, vwatcher_config, busy_day, mailed, capsys):
        cli.vw_daily(["--config-file", str(vwatcher_config), "--date", "2025-09-04", "--no-mail"])

        assert "Upgraded routers:" in capsys.readouterr().out
        assert mailed == []

    def test_should_fail_and_explain_when_day_was_already_rotated_into(self, vwatcher_config, busy_day, mailed, caplog):
        cli.vw_daily(["--config-file", str(vwatcher_config), "--date", "2025-09-04"])

        with pytest.raises(SystemExit):
            cli.vw_daily(["--config-file", str(vwatcher_config), "--date", "2025-09-04"])

        assert "Could not rotate the logs" in caplog.text
        assert len(mailed) == 1


class TestReportCommands:
    # The report commands read rotated days, so vw_daily is run to rotate the first one

    def test_vw_day_report_should_print_rotated_days_report(self, vwatcher_config, busy_day, mailed, capsys):
        cli.vw_daily(["--config-file", str(vwatcher_config), "--date", "2025-09-04"])
        capsys.readouterr()

        assert cli.vw_day_report(["--config-file", str(vwatcher_config), "2025-09", "04"]) == 0
        assert "Upgraded routers:" in capsys.readouterr().out

    def test_vw_month_upgr_should_print_the_months_upgrades(self, vwatcher_config, busy_day, mailed, capsys):
        cli.vw_daily(["--config-file", str(vwatcher_config), "--date", "2025-09-04"])
        capsys.readouterr()

        assert cli.vw_month_upgr(["--config-file", str(vwatcher_config), "2025-09"]) == 0
        assert "Sep 04" in capsys.readouterr().out

    def test_vw_month_rst_should_print_nothing_for_month_without_restarts(
        self, vwatcher_config, busy_day, mailed, capsys
    ):
        cli.vw_daily(["--config-file", str(vwatcher_config), "--date", "2025-09-04"])
        capsys.readouterr()

        assert cli.vw_month_rst(["--config-file", str(vwatcher_config), "2025-09"]) == 0
        assert capsys.readouterr().out == ""

    def test_cur_vers_should_print_every_devices_version(self, vwatcher_config, busy_day, capsys):
        assert cli.cur_vers(["--config-file", str(vwatcher_config)]) == 0

        out = capsys.readouterr().out
        assert "example-gw" in out
        assert "1:00:00.00" not in out

    def test_uptimes_should_print_every_devices_version_and_uptime(self, vwatcher_config, busy_day, capsys):
        assert cli.uptimes(["--config-file", str(vwatcher_config)]) == 0

        out = capsys.readouterr().out
        assert "example-gw" in out
        assert "1:00:00.00" in out

    def test_uptimes_should_order_by_uptime_when_asked(self, vwatcher_config, busy_day, capsys):
        # Younger device, but on the newer version, so the two orderings disagree
        busy_day.save_version("gw-younger", CISCO_DESCR_UPGRADED)
        busy_day.save_uptime("gw-younger", 6000)

        assert cli.uptimes(["--config-file", str(vwatcher_config), "-u"]) == 0

        by_uptime = capsys.readouterr().out
        assert by_uptime.index("gw-younger") < by_uptime.index("example-gw")

    def test_uptimes_should_order_by_version_by_default(self, vwatcher_config, busy_day, capsys):
        busy_day.save_version("gw-younger", CISCO_DESCR_UPGRADED)
        busy_day.save_uptime("gw-younger", 6000)

        assert cli.uptimes(["--config-file", str(vwatcher_config)]) == 0

        by_version = capsys.readouterr().out
        assert by_version.index("example-gw") < by_version.index("gw-younger")

    def test_when_month_was_never_logged_then_it_should_explain_why(self, vwatcher_config, busy_day, caplog):
        with pytest.raises(SystemExit):
            cli.vw_month_upgr(["--config-file", str(vwatcher_config), "1999-12"])

        assert "Could not read the logs" in caplog.text


class TestLoadConfig:
    def test_when_there_is_no_config_file_then_it_should_still_work_falling_back_to_the_defaults(
        self, tmp_path, monkeypatch, capsys
    ):
        # Ensures no config file is found
        monkeypatch.chdir(tmp_path)
        tree = LogTree(LOG_DIRECTORY)
        tree.ensure_today()
        tree.save_version("example-gw", CISCO_DESCR)

        assert cli.cur_vers([]) == 0
        assert "example-gw" in capsys.readouterr().out

    def test_when_named_config_file_is_missing_then_it_should_exit(self, tmp_path, caplog):
        with pytest.raises(SystemExit):
            cli.cur_vers(["--config-file", str(tmp_path / "absent.toml")])

        assert "Could not read the config file" in caplog.text

    def test_when_the_config_file_is_invalid_then_it_should_exit(self, tmp_path, caplog):
        path = tmp_path / "vwatcher.toml"
        path.write_text("this is not toml")

        with pytest.raises(SystemExit):
            cli.cur_vers(["--config-file", str(path)])

        assert "not valid TOML" in caplog.text

    def test_should_name_the_offending_toml_setting(self, tmp_path, caplog):
        path = tmp_path / "vwatcher.toml"
        path.write_text("[detection]\nallowed_offset = 'not a number'\n")

        with pytest.raises(SystemExit):
            cli.cur_vers(["--config-file", str(path)])

        assert "detection.allowed_offset" in caplog.text


class TestVwatcherDaemon:
    @pytest.fixture(autouse=True)
    def backends(self, monkeypatch):
        """Ensure a real SNMP backend is not loaded"""
        loaded = []
        monkeypatch.setattr(cli, "load_backend", loaded.append)
        return loaded

    @pytest.fixture
    def pollers(self, monkeypatch):
        "Stand-in poller"
        started = []
        monkeypatch.setattr(cli, "Poller", lambda config: SimpleNamespace(run=lambda: started.append(config)))
        monkeypatch.setattr(cli.asyncio, "run", lambda coroutine: None)
        return started

    def test_should_run_poller_on_the_configured_pollfile(self, vwatcher_config, polldevs_config, pollers):
        assert cli.vwatcher(["--config-file", str(vwatcher_config)]) == 0
        assert pollers[0].polling.file == str(polldevs_config)

    def test_should_let_argument_override_the_configured_pollfile(self, vwatcher_config, pollers):
        cli.vwatcher(["--config-file", str(vwatcher_config), "--polldevs", "/etc/other-polldevs.cf"])

        assert pollers[0].polling.file == "/etc/other-polldevs.cf"

    def test_should_load_the_configured_snmp_backend(self, vwatcher_config, pollers, backends):
        cli.vwatcher(["--config-file", str(vwatcher_config)])

        assert backends == ["netsnmp"]

    def test_when_interrupted_then_it_should_exit_quietly(self, vwatcher_config, monkeypatch):
        monkeypatch.setattr(cli, "Poller", lambda config: SimpleNamespace(run=lambda: None))
        monkeypatch.setattr(cli.asyncio, "run", _interrupt)

        assert cli.vwatcher(["--config-file", str(vwatcher_config)]) == 0


def _interrupt(coroutine):
    raise KeyboardInterrupt
