# vwatcher
[![build badge](https://img.shields.io/github/actions/workflow/status/Uninett/vwatcher/tests.yml?branch=main)](https://github.com/Uninett/vwatcher/actions)
[![codecov badge](https://codecov.io/gh/Uninett/vwatcher/branch/main/graph/badge.svg)](https://codecov.io/gh/Uninett/vwatcher)
[![Code style: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/release/python-31116/)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-31214/)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/release/python-31315/)

This is the modern Python re-implementation of the software version watcher (vwatcher),
first implemented in Tcl/Scotty and Perl at Uninett in the 1990s.

Development of vwatcher which is a part of the Zino 2.0 project is fully sponsored by
[NORDUnet](https://nordu.net/), on behalf of the nordic NRENs.

## Table of contents

- [What is vwatcher?](#what-is-vwatcher)
- [Installing](#installing-vwatcher)
- [Configuring](#configuring-vwatcher)
- [Using](#using-vwatcher)
- [Contributing](#developing-vwatcher)

## What is vwatcher?

vwatcher monitors the software versions and restarts of network devices, and
reports on them daily and monthly.  The daemon polls `sysUpTime` for each device,
when the reported uptime falls meaningfully short of what it
should have been given the previous poll, the device is presumed to have restarted.
In that case, vwatcher asks it for `sysDescr`, and on Cisco gear `whyReload`, and
writes what it finds to the event log. A software upgrade is assumed to require a reboot.

The daemon does not decide if a version changed, it only records what it
sees. The reports replay the log against the version each device was running at
the start of the day and draw relevant conclusions.
This means that reports can be re-run over old logs.

```
ver-watch/logs/today/ver-watch.log      today's events
ver-watch/logs/today/descs/<device>     the version as of the start of today
ver-watch/logs/today/uptime/<device>    the last sysUpTime seen
ver-watch/logs/YYYY-MM/DD/              the same three, rotated at the end of day
ver-watch/logs/YYYY-MM/DD/report        that day's report
```

`descs` is the baseline the reports diff against.  The daemon writes a device's
file only when it is missing or empty, so rotating the day away allows for a new baseline to be set.

### Differences from the original version

* The daily and monthly reports list devices in name order, was random in the original.
* Monthly reports walk a month's days in date order instead of the original `readdir`-based order.
* JunOS release types (`R`, `S`, `F`, `X`) are ordered as strings, the original ordering was broken.
* Mail settings come from `vwatcher.toml`, not from `~/.zino-mail`.
* `hcounters: no` forces SNMPv1 as before, otherwise a device's `snmpversion` decides.
* `vw-month-upgr` baselines a month from its first rotated day, the old version needed a `01` directory.

## Installing vwatcher

```console
$ pip install .
```

## Configuring vwatcher

`polldevs.cf` is the same file Zino reads, read with Zino's own parser, so one
pollfile can serve both.  vwatcher's own settings live in `vwatcher.toml` - see
[vwatcher.toml.example](vwatcher.toml.example) - and its `[polling]` and
`[snmp]` sections are the ones from `zino.toml`.  This replaces the legacy
`~/.zino-mail` and the `%TOPDIR%`/`%CONFDIR%` substitutions the Makefile used
to perform.

## Using vwatcher

| Command | What it does |
| --- | --- |
| `vwatcher` | The daemon.  Polls every device in `polldevs.cf`, re-reading the file when it changes. |
| `vw-daily` | Rotates today's logs into `YYYY-MM/DD`, then reports on and mails the day just ended.  Run from cron just after midnight. |
| `vw-day-report YYYY-MM DD` | Re-prints one rotated day's report: who was upgraded, who restarted or crashed. |
| `vw-month-upgr YYYY-MM` | Every upgrade of a month, one line per version step. |
| `vw-month-rst YYYY-MM` | Every restart of a month for which the device reported a reason. |
| `cur-vers` | The version every device is running now, ordered by version. |
| `uptimes [-u]` | The same, with uptimes.  `-u` orders by uptime instead. |

All of them take `--config-file` (default `vwatcher.toml`) and `--debug`.

## Developing vwatcher

Contributions are welcome, at this point especially bug reports (patches welcome), missing documentation, how to's and usage tips!


### Linting and formatting

vwatcher uses [ruff](https://docs.astral.sh/ruff/) as its formatter and
linter.  It comes with the development tools:

```console
$ pip install --group dev
```

A pre-commit hook will format new code automatically before committing.
To enable this pre-commit hook, run

```console
$ pre-commit install
```

### Running tests

If you have installed `tox`, the following command will test vwatcher code
against several Python versions and automatically compute code coverage.

```console
$ tox
```

Coverage and test results land in `reports/`.
Refer to the [tox.ini](tox.ini) file for further options.
