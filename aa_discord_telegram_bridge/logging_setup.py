"""Logging setup for the Discord-Telegram Bridge.

Alliance Auth configures handlers for its own loggers (``allianceauth``,
``extensions``, ``esi``, ``mumble_authenticator``) but nothing for third-party
apps. Without a handler of its own, every DTB record falls through to
``logging.lastResort``, which emits **WARNING and above only, as a bare line
with no timestamp**. Two consequences that hurt in practice:

* the INFO messages that actually identify a kick ("Kicked user X from
  Telegram group Y", "no longer has the DTB access permission, kicking from
  Telegram", "Unlinked Telegram for user X") are dropped in every process, so
  the log never says who was removed and why;
* what does survive (warnings, errors) arrives without a timestamp, so it
  cannot be placed on a timeline.

This module attaches a rotating, timestamped file handler so the plugin has a
real audit trail in every process that loads it: gunicorn, the celery workers,
the bot and the management commands. It is deliberately self-contained and
overridable through Django settings:

``DTB_LOG_DIR``   directory for the log file (default ``<BASE_DIR>/log``)
``DTB_LOG_FILE``  full path, wins over ``DTB_LOG_DIR``
``DTB_LOG_LEVEL`` level for the file (default ``INFO``)
"""
import logging
import os
from logging.handlers import RotatingFileHandler

LOGGER_NAME = 'aa_discord_telegram_bridge'
DEFAULT_FILENAME = 'dtb-bridge.log'
DEFAULT_LEVEL = 'INFO'
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5


def _setting(name, default=''):
    try:
        from django.conf import settings

        value = getattr(settings, name, default)
    except Exception:
        return default
    return value if value else default


def log_dir():
    path = _setting('DTB_LOG_DIR')
    if not path:
        path = _setting('LOG_DIR')
    if not path:
        path = os.path.join(str(_setting('BASE_DIR')), 'log')
    return str(path)


def log_file():
    return str(_setting('DTB_LOG_FILE') or os.path.join(log_dir(), DEFAULT_FILENAME))


def _level():
    raw = str(_setting('DTB_LOG_LEVEL', DEFAULT_LEVEL)).upper()
    resolved = logging.getLevelName(raw)
    return resolved if isinstance(resolved, int) else logging.INFO


def formatter():
    return logging.Formatter(
        fmt='%(asctime)s %(levelname)-8s %(name)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )


def configure():
    """Attach our handlers once per process.

    Idempotent: ``ready()`` runs again on autoreload and in every worker, and a
    duplicated handler would write every line twice.
    """
    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, '_dtb_handlers_installed', False):
        return logger

    level = _level()
    logger.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(formatter())
    console.setLevel(level)
    logger.addHandler(console)

    path = log_file()
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=int(_setting('DTB_LOG_MAX_BYTES', DEFAULT_MAX_BYTES)),
            backupCount=int(_setting('DTB_LOG_BACKUP_COUNT', DEFAULT_BACKUP_COUNT)),
            encoding='utf-8',
        )
        handler.setFormatter(formatter())
        handler.setLevel(level)
        logger.addHandler(handler)
    except (OSError, ValueError) as e:
        # Logging must never be the reason a deploy fails: console output and
        # the rest of the handlers stay in place.
        logger.warning('DTB: cannot write log file %s (%s)', path, e)

    # Keep DTB out of AA's handlers: it has its own file, and propagating would
    # duplicate every line into gunicorn/celery output.
    logger.propagate = False
    logger._dtb_handlers_installed = True
    return logger
