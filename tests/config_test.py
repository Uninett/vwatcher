"""Tests for reading `vwatcher.toml`"""

import pytest
from pydantic import ValidationError
from zino.config import InvalidConfigurationError

from vwatcher.config import Configuration, read_configuration


class TestReadConfiguration:
    def test_should_read_the_settings_from_the_file(self, vwatcher_conf, polldevs_conf):
        config = read_configuration(vwatcher_conf)

        assert config.polling.file == str(polldevs_conf)
        assert config.mail.recipient == "testuser@example.org"

    def test_should_fall_back_to_the_defaults(self, tmp_path):
        path = tmp_path / "vwatcher.toml"
        path.write_text("")
        config = read_configuration(path)

        assert config == Configuration()
        assert config.detection.allowed_offset == 5 * 60 * 100

    def test_should_default_to_the_snmp_backend_zino_defaults_to(self, tmp_path):
        path = tmp_path / "vwatcher.toml"
        path.write_text("")

        assert read_configuration(path).snmp.backend == "netsnmp"

    def test_when_the_file_is_missing_then_it_should_raise(self, tmp_path):
        with pytest.raises(OSError):
            read_configuration(tmp_path / "absent.toml")

    def test_should_raise_on_invalid_toml(self, tmp_path):
        path = tmp_path / "vwatcher.toml"
        path.write_text("this is not toml")

        with pytest.raises(InvalidConfigurationError):
            read_configuration(path)

    def test_when_a_key_is_misspelled_then_it_should_raise(self, tmp_path):
        path = tmp_path / "vwatcher.toml"
        path.write_text("[detection]\nallowed_offsett = 10\n")

        with pytest.raises(ValidationError) as excinfo:
            read_configuration(path)

        assert "Extra inputs are not permitted" in str(excinfo.value)

    def test_when_a_pollfile_cannot_be_read_then_it_should_raise(self, tmp_path):
        path = tmp_path / "vwatcher.toml"
        path.write_text(f'[polling]\nfile = "{tmp_path / "absent.cf"}"\n')

        with pytest.raises(ValidationError) as excinfo:
            read_configuration(path)

        assert "absent.cf" in str(excinfo.value)
