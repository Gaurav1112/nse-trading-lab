"""Centralized logging for nse_backtest.

Usage:
    from nse_backtest._logging import get_logger
    log = get_logger(__name__)
    log.info("event", extra={"symbol": "RELIANCE"})

The default level is INFO. Override with NSE_LOG_LEVEL env var (DEBUG/INFO/WARNING/ERROR).
A rotating file handler writes to ~/.nse_trading_lab_cache/nse.log when
NSE_LOG_FILE=1 (default in CLI; off in Streamlit by default to avoid duplicate handlers).
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import threading
from pathlib import Path

_INITIALISED = False
_INIT_LOCK = threading.Lock()


def _init() -> None:
    global _INITIALISED
    # Fast path without locking once initialised.
    if _INITIALISED:
        return
    # Re-check under lock to prevent two threads installing duplicate handlers.
    with _INIT_LOCK:
        if _INITIALISED:
            return
        level_name = os.environ.get("NSE_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)

        root = logging.getLogger("nse_backtest")
        root.setLevel(level)
        root.propagate = False

        fmt = logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # Console handler (always on)
        if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
            ch = logging.StreamHandler()
            ch.setFormatter(fmt)
            root.addHandler(ch)

        # Rotating file handler (opt-in) — guard against duplicate handlers
        # on Streamlit reruns / repeated module imports, which would otherwise
        # accumulate one RotatingFileHandler per rerun and (a) duplicate every
        # log line N× and (b) leak file descriptors until quota exceeded.
        if os.environ.get("NSE_LOG_FILE", "0") == "1":
            log_dir = Path.home() / ".nse_trading_lab_cache"
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
                    fh = logging.handlers.RotatingFileHandler(
                        log_dir / "nse.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
                    )
                    fh.setFormatter(fmt)
                    root.addHandler(fh)
            except Exception:  # pragma: no cover - non-fatal
                pass

        _INITIALISED = True


def get_logger(name: str = "nse_backtest") -> logging.Logger:
    _init()
    if not name.startswith("nse_backtest"):
        name = f"nse_backtest.{name.split('.')[-1]}"
    return logging.getLogger(name)


__all__ = ["get_logger"]
