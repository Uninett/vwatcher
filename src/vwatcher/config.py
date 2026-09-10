"""Covers vwatcher configuration models, and reading them from `vwatcher.toml`"""

from pathlib import Path
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict
from zino.config import InvalidConfigurationError
from zino.config.models import Polling

try:
    from tomllib import TOMLDecodeError, load
except ImportError:  # Python < 3.11
    from tomli import TOMLDecodeError, load

LOG_DIRECTORY = "ver-watch/logs"
#: How far a device's uptime may lag the estimate, in centiseconds, before it counts as a restart
UPTIME_SLOP = 100 * 60 * 5


class Detection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uptime_slop: int = UPTIME_SLOP


class Logs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory: str = LOG_DIRECTORY


class Mail(BaseModel):
    """Where to send the daily report.  Replaces the legacy `~/.zino-mail`."""

    model_config = ConfigDict(extra="forbid")

    sender: Optional[str] = None
    recipient: Optional[str] = None
    sendmail: str = "/usr/sbin/sendmail"


class Scheduler(BaseModel):
    """What the scheduler does with a poll that runs late"""

    model_config = ConfigDict(extra="forbid")

    misfire_grace_time: int = 10  # Seconds


class Snmp(BaseModel):
    """Which of Zino's SNMP back-ends to poll with"""

    model_config = ConfigDict(extra="forbid")

    backend: Literal["pysnmp", "netsnmp"] = "netsnmp"


class Configuration(BaseModel):
    """Everything `vwatcher.toml` can set"""

    model_config = ConfigDict(extra="forbid")

    detection: Detection = Detection()
    logs: Logs = Logs()
    mail: Mail = Mail()
    polling: Polling = Polling()
    scheduler: Scheduler = Scheduler()
    snmp: Snmp = Snmp()

    @property
    def log_directory(self) -> Path:
        return Path(self.logs.directory)


def read_configuration(config_file: Union[str, Path]) -> Configuration:
    """Read and validate a `vwatcher.toml`, defaulting whatever it leaves out"""
    with open(config_file, "rb") as handle:
        try:
            settings = load(handle)
        except TOMLDecodeError as error:
            raise InvalidConfigurationError(str(error)) from error
    return Configuration.model_validate(settings, strict=True)
