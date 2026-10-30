"""Mailing a report, via the local sendmail"""

import logging
import subprocess
from typing import Optional

from vwatcher.config import Mail

_log = logging.getLogger(__name__)


def send_report(subject: str, body: str, settings: Mail) -> bool:
    if not body:
        _log.debug("Nothing to report, no mail sent")
        return False
    if not settings.recipient:
        _log.warning("No mail recipient configured, not mailing %r", subject)
        return False

    message = _format_message(subject, body, settings.sender, settings.recipient)
    try:
        subprocess.run([settings.sendmail, settings.recipient], input=message, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        _log.error("Could not mail report to %s: %s", settings.recipient, error)
        return False
    return True


def _format_message(subject: str, body: str, sender: Optional[str], recipient: str) -> str:
    headers = [f"To: {recipient}", f"Subject: {subject}"]
    if sender:
        headers.insert(0, f"From: {sender}")
    return "\n".join(headers) + "\n\n" + body
