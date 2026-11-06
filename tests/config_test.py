"""Tests for the vwatcher configuration model, as read by Zino's `read_configuration()`"""

import pytest
from pydantic import ValidationError
from zino.config import read_configuration

from vwatcher.config import ALLOWED_OFFSET, Configuration


class TestConfiguration:
    def test_should_read_the_settings_from_the_file(self, vwatcher_config, polldevs_config):
        config = read_configuration(vwatcher_config, model=Configuration)

        assert config.polling.file == str(polldevs_config)
        assert config.mail.recipient == "testuser@example.org"

    def test_should_fall_back_to_the_defaults(self, empty_vwatcher_config):
        config = read_configuration(empty_vwatcher_config, model=Configuration)

        assert config == Configuration()
        assert config.detection.allowed_offset == ALLOWED_OFFSET

    @pytest.mark.parametrize("section", ["[snmp.trap]\nsource = 'straps'\n", "[event]\n"])
    def test_should_reject_zino_settings_vwatcher_does_not_use(self, tmp_path, section):
        path = tmp_path / "vwatcher.toml"
        path.write_text(section)

        with pytest.raises(ValidationError):
            read_configuration(path, model=Configuration)

    def test_when_key_is_misspelled_then_it_should_raise(self, tmp_path):
        path = tmp_path / "vwatcher.toml"
        path.write_text("[detection]\nallowed_offsett = 10\n")

        with pytest.raises(ValidationError) as excinfo:
            read_configuration(path, model=Configuration)

        assert "Extra inputs are not permitted" in str(excinfo.value)
