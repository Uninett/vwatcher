"""Tests for mailing a report"""

import subprocess

import pytest

from vwatcher.config import Mail
from vwatcher.report.mail import send_report


@pytest.fixture
def sendmail(monkeypatch):
    calls = []

    def fake_run(command, input=None, **kwargs):
        calls.append((command, input))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


@pytest.fixture
def settings() -> Mail:
    return Mail(sender="vwatcher@example.org", recipient="testuser@example.org")


class TestSendReport:
    def test_should_give_the_report_to_sendmail(self, sendmail, settings):
        assert send_report("Daily report", "example-gw restarted\n", settings)

        command, message = sendmail[0]
        assert command == [settings.sendmail, "testuser@example.org"]
        assert message.endswith("example-gw restarted\n")

    def test_should_address_the_mail(self, sendmail, settings):
        send_report("Daily report", "a report\n", settings)

        _command, message = sendmail[0]
        assert "From: vwatcher@example.org" in message
        assert "To: testuser@example.org" in message
        assert "Subject: Daily report" in message

    def test_should_separate_the_headers_from_the_body(self, sendmail, settings):
        send_report("Daily report", "a report\n", settings)

        _command, message = sendmail[0]
        assert message.split("\n\n", maxsplit=1)[1] == "a report\n"

    def test_should_not_mail_empty_report(self, sendmail, settings):
        assert not send_report("Daily report", "", settings)
        assert sendmail == []

    def test_when_there_is_no_recipient_then_it_should_not_mail(self, sendmail):
        assert not send_report("Daily report", "a report\n", Mail())
        assert sendmail == []

    def test_when_sendmail_is_missing_then_it_should_log_and_not_raise(self, tmp_path, caplog):
        settings = Mail(recipient="testuser@example.org", sendmail=str(tmp_path / "no-sendmail"))

        assert not send_report("Daily report", "a report\n", settings)
        assert "Could not mail report" in caplog.text

    def test_when_sendmail_fails_then_it_should_log_and_not_raise(self, caplog):
        # `false` takes any arguments, reads nothing and exits 1
        settings = Mail(recipient="testuser@example.org", sendmail="false")

        assert not send_report("Daily report", "a report\n", settings)
        assert "Could not mail report" in caplog.text
