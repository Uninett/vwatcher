"""Tests for condensing a `sysDescr` and ordering the versions in it"""

import pytest

from vwatcher.report.software import (
    dissect_cisco_version,
    dissect_junos_version,
    leading_number,
    pretty_desc,
    split_pretty,
    version_sort_key,
)

CASES = [
    (
        "Cisco IOS Software, 7200 Software (C7200-ADVENTERPRISEK9-M), Version 15.2(4)S7, RELEASE SOFTWARE (fc4)",
        "Cisco-IOS 15.2(4)S7 C7200-ADVENTERPRISEK9-M",
    ),
    (
        "Cisco NX-OS(tm) n9000, Software (n9000-dk9), Version 9.3(5), RELEASE SOFTWARE Copyright (c)",
        "Cisco-NX-OS 9.3(5) n9000",
    ),
    (
        "Cisco IOS XR Software (Cisco ASR9K Series), Version 6.5.3[Default] Copyright (c) 2019",
        "Cisco-IOS-XR 6.5.3",
    ),
    (
        "Cisco Catalyst Operating System Software, Version 8.5(8)",
        "Cisco CatOS 8.5(8)",
    ),
    (
        "Juniper Networks, Inc. mx480 internet router, kernel JUNOS 20.4R3-S2.1, Build date: 2021-06-01",
        "JunOS 20.4R3-S2.1 mx480",
    ),
    (
        "Juniper Networks, Inc. ex4300-48t [EX-4300] , kernel JUNOS 18.4R3.3, Build date: 2020-02-02",
        "JunOS 18.4R3.3 ex-4300",
    ),
    (
        "Juniper Networks, Inc. qfx5100-48s-6q Ethernet Switch, kernel JUNOS 18.4R3.3, Build date: 2020-02-02",
        "JunOS 18.4R3.3 qfx5100-48s-6q",
    ),
    (
        "Arista Networks EOS version 4.28.3M running on an Arista Networks DCS-7280SR-48C6",
        "Arista-EOS 4.28.3M DCS-7280SR-48C6",
    ),
    (
        "ProCurve J9147A E2910al-48G Switch, revision W.15.10.0006, ROM W.15.05 (/sw/code/build/sbm)",
        "HP-ProCurve J9147A Switch W.15.10.0006 ROM W.15.05",
    ),
    (
        "HP J9773A 2530-24G-PoEP Switch, revision YA.16.02.0012, ROM YA.15.20 (/ws/swbuildm)",
        "HP-ProCurve J9773A 2530-24G-PoEP YA.16.02.0012 ROM YA.15.20",
    ),
]


class TestPrettyDesc:
    @pytest.mark.parametrize("descr, expected", CASES)
    def test_should_condense_known_description(self, descr, expected):
        assert pretty_desc(descr) == expected

    def test_should_pass_unknown_description_through(self):
        assert pretty_desc("Some Vendor Router") == "Some Vendor Router"

    def test_should_remove_newlines_from_wrapped_description(self):
        assert pretty_desc("RouterOS\nv6.48\n") == "RouterOSv6.48"

    def test_should_be_idempotent(self):
        for descr, expected in CASES:
            assert pretty_desc(expected) == expected


class TestSplitPretty:
    def test_should_split_condensed_description(self):
        assert split_pretty("Cisco-IOS 15.2(4)S7 C7200") == ("Cisco-IOS", "15.2(4)S7")

    def test_when_description_cannot_be_split_then_it_should_return_empty_fields(self):
        assert split_pretty("Router") == ("", "")


class TestDissectCiscoVersion:
    @pytest.mark.parametrize(
        "version, expected",
        [
            ("15.2(4)S7", (15.0, 2.0, "S", 4.0, 7.0)),
            ("15.2(4)S", (15.0, 2.0, "S", 4.0, 0.0)),
            ("12.4(25e)", (12.0, 4.0, "", 25.0, 0.0)),
        ],
    )
    def test_should_split_version(self, version, expected):
        assert dissect_cisco_version(version) == expected

    def test_when_version_is_unrecognized_then_it_should_return_none(self):
        assert dissect_cisco_version("not-a-version") is None


class TestDissectJunosVersion:
    @pytest.mark.parametrize(
        "version, expected",
        [
            ("20.4R3.8", (20.0, 4.0, "R", 3.0, 8.0)),
            ("20.4R3-S2.1", (20.0, 4.0, "RS", 3.0, 102.1)),
            ("20.4R3-S2", (20.0, 4.0, "RS", 3.0, 102.0)),
            ("20.4X75-D30.6", (20.0, 4.0, "X", 75.0, 30.6)),
        ],
    )
    def test_should_split_version(self, version, expected):
        assert dissect_junos_version(version) == expected

    def test_should_rank_service_release_above_plain_revision(self):
        assert dissect_junos_version("20.4R3-S2.1") > dissect_junos_version("20.4R3.8")

    def test_when_version_is_unrecognized_then_it_should_return_none(self):
        assert dissect_junos_version("21") is None


class TestLeadingNumber:
    @pytest.mark.parametrize("value, expected", [("25e", 25.0), ("", 0.0), ("S", 0.0), ("4.2", 4.2)])
    def test_should_format_like_old_vwatch(self, value, expected):
        assert leading_number(value) == expected


class TestVersionSortKey:
    def test_should_order_by_version(self):
        versions = ["15.2(4)S9", "15.2(4)S10", "15.2(4)S7"]
        ordered = sorted(versions, key=lambda v: version_sort_key("gw", f"Cisco-IOS {v} C7200"))

        assert ordered == ["15.2(4)S7", "15.2(4)S9", "15.2(4)S10"]

    def test_should_group_by_operating_system(self):
        devices = [
            ("juniper2", "JunOS 21.2R3.8 mx480"),
            ("cisco2", "Cisco-IOS 15.2(4)S8 C7200"),
            ("juniper1", "JunOS 20.4R3.8 mx480"),
            ("cisco1", "Cisco-IOS 15.2(4)S7 C7200"),
        ]
        ordered = sorted(devices, key=lambda pair: version_sort_key(*pair))

        assert [name for name, _ in ordered] == ["cisco1", "cisco2", "juniper1", "juniper2"]

    def test_should_fall_back_to_the_device_name_for_equal_versions(self):
        devices = [("gw2", "Cisco-IOS 15.2(4)S7 C7200"), ("gw1", "Cisco-IOS 15.2(4)S7 C7200")]
        ordered = sorted(devices, key=lambda pair: version_sort_key(*pair))

        assert [name for name, _ in ordered] == ["gw1", "gw2"]

    def test_should_order_devices_with_unknown_descriptions(self):
        devices = [("gw2", pretty_desc("Some Vendor")), ("gw1", pretty_desc("Another Vendor"))]
        ordered = sorted(devices, key=lambda pair: version_sort_key(*pair))

        assert [name for name, _ in ordered] == ["gw1", "gw2"]
