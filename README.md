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

TBD

## Installing vwatcher

TBD

## Configuring vwatcher

TBD

## Using vwatcher

TBD

## Developing vwatcher

Contributions are welcome, at this point especially bug reports (patches
welcome), missing documentation, how to's and usage tips!

### Linting and formatting

vwatcher uses [ruff](https://docs.astral.sh/ruff/) as a Python source code
formatter and linter. Ruff can be installed by running

```console
$ uv install ruff
```

A pre-commit hook will format new code automatically before committing.
To enable this pre-commit hook, run

```console
$ pre-commit install
```

### Running tests

If you have installed `tox`, the following command will
test vwatcher code against several Django versions, several Python versions, and
automatically compute code coverage.

```console
$ tox
```

An [HTML coverage report](htmlcov/index.html) will be generated.
Refer to the [tox.ini](tox.ini) file for further options.
