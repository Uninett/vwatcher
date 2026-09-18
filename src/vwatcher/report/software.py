"""
Making sense of a device's software: what it is, and which is newer.

`pretty_desc` condenses a `sysDescr` down to the part that identifies the
software: each vendor rule is tried in turn against the result of the previous one,
 and anything unrecognized is passed through.
`version_sort_key` then picks that apart, so that `cur-vers` and `uptimes` can
order by version.
"""

import re
from typing import Optional, Pattern

Rule = tuple[Pattern, str]


def _rules(*patterns: tuple[str, str]) -> list[Rule]:
    return [(re.compile(pattern), template) for pattern, template in patterns]


CISCO_RULES = _rules(
    (r"NX-OS\(tm\) ([^,]*), .*Version ([^,]*), .*", r"Cisco-NX-OS \2 \1"),
    (r"IOS XR.*Version ([^\[]*).*", r"Cisco-IOS-XR \1"),
    (r"IOS.*\(([^)]+)\), Version ([^,\n]+),", r"Cisco-IOS \2 \1"),
    (r"IOS.*\(([^)]+)\),.*Version ([^ ]+) ", r"Cisco-IOS \2 \1"),
    (r"GS.*\(([^)]+)\), Version ([^,]+),", r"Cisco-IOS \2 \1"),
    (r"Cisco ONS ([^ ]+) ([^ ]+) .*", r"Cisco \2 ONS-\1"),
    (r".*Cisco Catalyst Operating.*, Version (\d+.\d+\(\d+\)).*", r"Cisco CatOS \1"),
)

# Matched in order; group 1 is the platform, group 2 the JUNOS version
JUNOS_PATTERNS = (
    re.compile(r"Juniper Networks, Inc\. \S+ \[([^ ]+)\] .* kernel JUNOS ([^ ,]+)[, ].*"),
    re.compile(r"Juniper Networks, Inc\. ([^ ]+) .*kernel JUNOS ([^ ,]+)[, ].*"),
)

ARISTA_RULES = _rules(
    (
        r"Arista Networks EOS version ([^ ]+) running on an Arista Networks ([^ ]+)",
        r"Arista-EOS \1 \2",
    ),
)

HP_RULES = _rules(
    (r"(HP[0-9]+[A-Z]) .*\(([^)]+)\), revision ([^,]+),", r"\3 \1 \2"),
)

PROCURVE_RULES = _rules(
    (r"ProCurve ([^ ]+) [^ ]+ ([^ ]+), revision ([^ ]+), ROM ([^ ]+) .*", r"HP-ProCurve \1 \2 \3 ROM \4"),
    (r"^HP ([^ ]+) ([^ ]+) Switch, revision ([^ ,]+), ROM ([^ ]+).*", r"HP-ProCurve \1 \2 \3 ROM \4"),
)


def pretty_desc(descr: str) -> str:
    """Condense a `sysDescr` to `<os> <version> <platform>` where recognized"""
    descr = pretty_cisco(descr)
    descr = pretty_junos(descr)
    descr = _apply(ARISTA_RULES, descr)
    descr = _apply(HP_RULES, descr)
    descr = _apply(PROCURVE_RULES, descr)
    return descr


def pretty_cisco(descr: str) -> str:
    """
    Condense Cisco IOS/IOS-XR/NX-OS/CatOS/ONS descriptions

    RouterOS wraps its descr, and every rule below needs single-line input
    """
    return _apply(CISCO_RULES, descr).replace("\n", "")


def pretty_junos(descr: str) -> str:
    for pattern in JUNOS_PATTERNS:
        if match := pattern.search(descr):
            return f"JunOS {match.group(2)} {match.group(1).lower()}"
    return descr


def _apply(rules: list[Rule], descr: str) -> str:
    """Apply each rule that matches, in order, to the running result"""
    for pattern, template in rules:
        if match := pattern.search(descr):
            descr = match.expand(template)
    return descr


CISCO_VERSION = re.compile(r"(\d+)\.(\d+)\(([^)]+)\)([A-Z]*)(\d*)")

# Release type (taken from the `type` group where it is None), the amount a
# service release outranks a plain minor revision by, and the pattern
JUNOS_VERSIONS = (
    # 20.4X75-D30.6
    ("X", 0, re.compile(r"(?P<major>\d+)\.(?P<minor>\d+)X(?P<revision>\d+)-D(?P<service>\d+)\.(?P<sub>\d+)")),
    # 20.4R3-S2.1 and 20.4R3-S2
    ("RS", 100, re.compile(r"(?P<major>\d+)\.(?P<minor>\d+)R(?P<revision>\d+)-S(?P<service>\d+)(?:\.(?P<sub>\d+))?")),
    # 20.4R3.8, 20.4F1.2, 20.4S1.3
    (None, 0, re.compile(r"(?P<major>\d+)\.(?P<minor>\d+)(?P<type>[FRS])(?P<revision>\d+)\.(?P<minor_revision>\d+)")),
)

# Same shape for every family, so that keys stay comparable
VersionFields = tuple[float, float, str, float, float]


def version_sort_key(device: str, pretty_descr: str) -> tuple:
    """Order devices by OS, then by version, then by name"""
    os_name, version = split_pretty(pretty_descr)
    return (os_name, version_fields(os_name, version), device)


def split_pretty(descr: str) -> tuple[str, str]:
    """
    Split a prettified description into its OS name and version.

    Both are empty where no rule recognized the description, which is how the
    reports fall back to ordering by name.
    """
    fields = descr.split()
    if len(fields) < 2:
        return "", ""
    return fields[0], fields[1]


def version_fields(os_name: str, version: str) -> VersionFields:
    if os_name == "JunOS":
        fields = dissect_junos_version(version)
    else:
        fields = dissect_cisco_version(version)
    if fields is None:
        return (leading_number(version), 0.0, "", 0.0, 0.0)
    return fields


def dissect_cisco_version(version: str) -> Optional[VersionFields]:
    """Split e.g. `15.2(4)S7` into major, minor, train, revision, train revision"""
    if not (match := CISCO_VERSION.search(version)):
        return None
    major, minor, revision, train, train_revision = match.groups()
    return (float(major), float(minor), train, leading_number(revision), leading_number(train_revision))


def dissect_junos_version(version: str) -> Optional[VersionFields]:
    """Split e.g. `20.4R3-S2.1` into major, minor, type, revision, minor revision"""
    for release_type, offset, pattern in JUNOS_VERSIONS:
        if not (match := pattern.search(version)):
            continue
        parts = match.groupdict()
        minor_revision = parts.get("minor_revision")
        if (service := parts.get("service")) is not None:
            minor_revision = f"{int(service) + offset}.{parts.get('sub') or 0}"
        return (
            float(parts["major"]),
            float(parts["minor"]),
            release_type or parts["type"],
            leading_number(parts["revision"]),
            leading_number(minor_revision),
        )
    return None


def leading_number(value: str) -> float:
    """Read the leading number of a string, or 0"""
    match = re.match(r"\s*(\d+(?:\.\d+)?)", value or "")
    return float(match.group(1)) if match else 0.0
