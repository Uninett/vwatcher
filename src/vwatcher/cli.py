"""
vwatcher application commands.

`vwatcher` is the daemon; the rest are the reports that used to be separate Perl
scripts.  They all read the same `vwatcher.toml`.
"""

import argparse
import asyncio
import logging
import sys
from datetime import date
from typing import Optional, Sequence

from pydantic import ValidationError
from zino.config import InvalidConfigurationError, format_validation_error

from vwatcher.collect import Poller, load_backend
from vwatcher.config import Configuration, read_configuration
from vwatcher.report import reports, send_report
from vwatcher.store import LogTree

_log = logging.getLogger("vwatcher")

DEFAULT_CONFIG_FILE = "vwatcher.toml"


def vwatcher(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser("Monitor software versions and restarts of network devices")
    parser.add_argument("--polldevs", help="Override the pollfile named in the config file")
    args = _parse_args(parser, argv)
    config = load_config(args)
    if args.polldevs:
        config.polling.file = args.polldevs
    _log.info("Watching %s, logging to %s", config.polling.file, config.log_directory)
    load_backend(config.snmp.backend)
    try:
        asyncio.run(Poller(config).run())
    except KeyboardInterrupt:
        _log.info("Shutting down")
    return 0


def vw_daily(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser("Rotate the vwatcher logs and mail the daily report")
    parser.add_argument("--date", type=date.fromisoformat, help="The day to rotate into (default: today)")
    parser.add_argument("--no-mail", action="store_true", help="Print the report instead of mailing it")
    args = _parse_args(parser, argv)
    config = load_config(args)
    when = args.date or date.today()

    report = reports.rotate_and_report(_tree(config), when)
    if args.no_mail:
        sys.stdout.write(report)
    else:
        send_report(f"Device upgrade/restart report {when:%Y-%m-%d}", report, config.mail)
    return 0


def vw_day_report(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser("Report the upgrades and restarts of one day")
    parser.add_argument("month", help="Month of the day to report on, as YYYY-MM")
    parser.add_argument("day", help="Day of month to report on, as DD")
    args = _parse_args(parser, argv)
    tree = _tree(load_config(args))
    return _write(lambda: reports.day_report(tree, args.month, args.day))


def vw_month_upgr(argv: Optional[Sequence[str]] = None) -> int:
    return _month_report("Report the upgrades of one month", reports.month_upgrade_report, argv)


def vw_month_rst(argv: Optional[Sequence[str]] = None) -> int:
    return _month_report("Report the restarts of one month", reports.month_restart_report, argv)


def _month_report(description: str, build_report, argv: Optional[Sequence[str]]) -> int:
    """Run whichever of the two monthly reports was asked for"""
    parser = _parser(description)
    parser.add_argument("month", help="Month to report on, as YYYY-MM")
    args = _parse_args(parser, argv)
    tree = _tree(load_config(args))
    return _write(lambda: build_report(tree, args.month))


def cur_vers(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(_parser("Report the current software version of every device"), argv)
    tree = _tree(load_config(args))
    return _write(lambda: reports.current_versions(tree))


def uptimes(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser("Report the current software version and uptime of every device")
    parser.add_argument("-u", "--by-uptime", action="store_true", help="Sort by uptime instead of by version")
    args = _parse_args(parser, argv)
    tree = _tree(load_config(args))
    return _write(lambda: reports.uptime_report(tree, by_uptime=args.by_uptime))


def _parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config-file", help=f"Path to the configuration file (default: {DEFAULT_CONFIG_FILE})")
    parser.add_argument("--debug", action="store_true", help="Log at debug level")
    return parser


def _parse_args(parser: argparse.ArgumentParser, argv: Optional[Sequence[str]]) -> argparse.Namespace:
    """Parse the command line and set up logging"""
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    return args


def load_config(args: argparse.Namespace) -> Configuration:
    """Load the configuration file, exiting the process if there are config errors.

    Returns the defaults if no config file was named or none exists.
    """
    config_file = args.config_file or DEFAULT_CONFIG_FILE
    try:
        return read_configuration(config_file)
    except OSError as error:
        if args.config_file:
            _log.fatal("Could not read the config file %s: %s", config_file, error)
            sys.exit(1)
        return Configuration()
    except InvalidConfigurationError as error:
        _log.fatal("Configuration file %s is not valid TOML: %s", config_file, error)
        sys.exit(1)
    except ValidationError as error:
        # Zino's formatter names the offending key, and suggests a spelling
        _log.fatal("%s", "\n".join(format_validation_error(error, Configuration)))
        sys.exit(1)


def _tree(config: Configuration) -> LogTree:
    return LogTree(config.log_directory)


def _write(build_report) -> int:
    """Print a report, exiting the process if the logs cannot be read"""
    try:
        sys.stdout.write(build_report())
    except OSError as error:
        _log.fatal("Could not read the logs: %s", error)
        sys.exit(1)
    return 0
