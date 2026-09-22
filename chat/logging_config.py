"""Unified logging configuration for Quest.

Provides a single log format for both application loggers (chat.*)
and uvicorn loggers (uvicorn.error, uvicorn.access).

Every log line includes:
  - Colored level label (via uvicorn's ColourizedFormatter)
  - Timestamp (YYYY-MM-DD HH:MM:SS)
  - Process ID (PID) in square brackets
  - Logger/module name (padded to 20 chars for alignment)
  - Message
"""

import logging.config

from config.paths import LOG_DIR

# Dedicated log directory for large tool result logs.
_LARGE_TOOL_LOG_PATH = LOG_DIR / "large_tool_results.jsonl"


# Unified format string.
# %(levelprefix)s is provided by uvicorn's ColourizedFormatter and includes
# the colorized level name + colon + padding (e.g., "INFO:     ").
_UNIFIED_FMT = "%(levelprefix)s %(asctime)s [%(process)d] %(name)-20s %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# Access log format -- same prefix, but with structured HTTP fields
# provided by uvicorn's AccessFormatter.
_ACCESS_FMT = '%(levelprefix)s %(asctime)s [%(process)d] %(name)-20s %(client_addr)s - "%(request_line)s" %(status_code)s'


def get_log_config(use_colors: bool | None = None) -> dict:
    """Return a logging dictConfig that unifies all log formats.

    This config is designed to be passed to uvicorn as ``log_config``.
    It configures:
      - uvicorn.error (server messages)
      - uvicorn.access (HTTP access logs)
      - chat (application logs from chat.gemini_api, chat.route_dispatch, etc.)
      - auth (OAuth and credential logs from the auth submodule)
      - quest (proxy logs from the main quest module)

    Args:
        use_colors: Force color on/off. None = auto-detect from TTY.

    Returns:
        A dict suitable for ``logging.config.dictConfig()`` or
        ``uvicorn.run(log_config=...)``.
    """
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": _UNIFIED_FMT,
                "datefmt": _DATE_FMT,
                "use_colors": use_colors,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": _ACCESS_FMT,
                "datefmt": _DATE_FMT,
                "use_colors": use_colors,
            },
            "large_tool_results": {
                "format": "%(message)s",
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "large_tool_results_file": {
                "formatter": "large_tool_results",
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(_LARGE_TOOL_LOG_PATH),
                "maxBytes": 50 * 1024 * 1024,  # 50 MB
                "backupCount": 3,
                "encoding": "utf-8",
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.error": {
                "level": "INFO",
            },
            "uvicorn.access": {
                "handlers": ["access"],
                "level": "INFO",
                "propagate": False,
            },
            "chat": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False,
            },
            "auth": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False,
            },
            "quest": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False,
            },
            "large_tool_results": {
                "handlers": ["large_tool_results_file"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }
    return config


def configure_logging(use_colors: bool | None = None) -> None:
    """Apply the unified logging configuration.

    This should be called once at application startup, before uvicorn
    begins processing. When using ``uvicorn.run()``, prefer passing
    ``log_config=get_log_config()`` instead so uvicorn applies it
    at the right time.

    Args:
        use_colors: Force color on/off. None = auto-detect from TTY.
    """
    logging.config.dictConfig(get_log_config(use_colors))
