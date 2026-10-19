"""Logging setup for the Discord-Telegram Bridge.

The plugin logs through ``allianceauth.services.hooks.get_extension_logger``,
so every record is a child of AA's ``extensions`` logger — the tree Alliance
Auth actually configures handlers for. Before that change every DTB record
fell through to ``logging.lastResort``, which emits **WARNING and above only,
as a bare line with no timestamp**. Two consequences that hurt in practice:

* the INFO messages that actually identify a kick ("Kicked user X from
  Telegram group Y", "no longer has the DTB access permission, kicking from
  Telegram", "Unlinked Telegram for user X") were dropped in every process, so
  the log never says who was removed and why;
* what did survive (warnings, errors) arrived without a timestamp, so it
  could not be placed on a timeline.

This module attaches a rotating, timestamped file handler so the plugin has a
real audit trail in every process that loads it: gunicorn, the celery workers,
the bot and the management commands. Propagation stays off, so the same lines
are not duplicated into AA's ``extensions.log`` and gunicorn output. It is
deliberately self-contained and overridable through Django settings:

``DTB_LOG_DIR``   directory for the log file (default ``<BASE_DIR>/log``)
``DTB_LOG_FILE``  full path, wins over ``DTB_LOG_DIR``
``DTB_LOG_LEVEL`` level for the file (default ``INFO``)
"""
import logging
import os
from logging.handlers import RotatingFileHandler

# get_extension_logger() registers its logger under 'extensions.<name>' and
# only then renames the object, so this is the key our handlers must live on
# for the whole plugin tree (views, tasks, manager, ...) to reach them.
LOGGER_NAME = 'extensions.aa_discord_telegram_bridge'
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


def _close(handler):
    try:
        handler.close()
    except Exception:
        pass


def _reset(logger):
    """Drop our handlers so a later configure() can install a fresh set."""
    for handler in list(logger.handlers):
        if getattr(handler, '_dtb_ours', False):
            logger.removeHandler(handler)
            _close(handler)
    logger._dtb_signature = None


def _adopt_descendants(base):
    """Re-point DTB child loggers that were created before ``base``.

    ``get_extension_logger`` resolves a logger's parent once, at creation
    time. A module imported before ``configure()`` ran (signals, models) ends
    up parented directly to AA's ``extensions`` logger and would then bypass
    our file handler for the whole life of the process. Children created
    later find ``base`` in the logger dict on their own.
    """
    prefix = LOGGER_NAME + '.'
    for name, candidate in list(logging.Logger.manager.loggerDict.items()):
        if name.startswith(prefix) and isinstance(candidate, logging.Logger):
            candidate.parent = base


def configure(force=False):
    """Attach our handlers once per process.

    Idempotent for the common case: ``ready()`` runs again under autoreload and
    in every worker, and duplicated handlers would write every line twice. The
    installed configuration is remembered by signature, so a settings override
    (``DTB_LOG_FILE`` in a test, or in a settings module loaded later) is
    picked up instead of being silently ignored; ``force=True`` re-installs.
    """
    logger = logging.getLogger(LOGGER_NAME)
    _adopt_descendants(logger)

    path = log_file()
    level = _level()
    signature = (path, level)
    if not force and getattr(logger, '_dtb_signature', None) == signature:
        return logger

    _reset(logger)

    console = logging.StreamHandler()
    console.setFormatter(formatter())
    console.setLevel(level)
    console._dtb_ours = True
    logger.addHandler(console)

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
        handler._dtb_ours = True
        logger.addHandler(handler)
    except (OSError, ValueError) as e:
        # Logging must never be the reason a deploy fails: console output
        # stays in place and the logger keeps working.
        logger.warning('DTB: cannot write log file %s (%s)', path, e)

    logger.setLevel(level)
    # Keep DTB out of AA's handlers: it has its own file, and propagating would
    # duplicate every line into gunicorn/celery output.
    logger.propagate = False
    logger._dtb_signature = signature
    return logger
