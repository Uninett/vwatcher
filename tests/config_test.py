"""Tests for reading `vwatcher.toml`"""

import pytest
from pydantic import ValidationError
from zino.config import InvalidConfigurationError

from vwatcher.config import ALLOWED_OFFSET, Configuration, read_configuration


class TestReadConfiguration:
    def test_should_read_the_settings_from_the_file(self, vwatcher_config, polldevs_config):
        config = read_configuration(vwatcher_config)

        assert config.polling.file == str(polldevs_config)
        assert config.mail.recipient == "testuser@example.org"

    def test_should_fall_back_to_the_defaults(self, empty_vwatcher_config):
        config = read_configuration(empty_vwatcher_config)

        assert config == Configuration()
        assert config.detection.allowed_offset == ALLOWED_OFFSET

    def test_should_default_to_the_snmp_backend_zino_defaults_to(self, empty_vwatcher_config):
        assert read_configuration(empty_vwatcher_config).snmp.backend == "netsnmp"

    def test_when_the_file_is_missing_then_it_should_raise(self, tmp_path):
        with pytest.raises(OSError):
            read_configuration(tmp_path / "absent.toml")

    def test_should_raise_on_invalid_toml(self, tmp_path):
        path = tmp_path / "vwatcher.toml"
        path.write_text("this is not toml")

        with pytest.raises(InvalidConfigurationError):
            read_configuration(path)

    def test_when_key_is_misspelled_then_it_should_raise(self, tmp_path):
        path = tmp_path / "vwatcher.toml"
        path.write_text("[detection]\nallowed_offsett = 10\n")

        with pytest.raises(ValidationError) as excinfo:
            read_configuration(path)

        assert "Extra inputs are not permitted" in str(excinfo.value)

    def test_when_pollfile_cannot_be_read_then_it_should_raise(self, tmp_path):
        path = tmp_path / "vwatcher.toml"
        path.write_text(f'[polling]\nfile = "{tmp_path / "absent.cf"}"\n')

        with pytest.raises(ValidationError) as excinfo:
            read_configuration(path)

        assert "absent.cf" in str(excinfo.value)
