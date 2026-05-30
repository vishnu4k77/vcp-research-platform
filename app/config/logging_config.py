import atexit
import io
import logging
import logging.handlers
import queue
import sys
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_FORMATTER = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Single shared queue — all threads post here, one listener thread writes
_log_queue: queue.Queue = queue.Queue(-1)
_listener: logging.handlers.QueueListener | None = None


def _start_listener() -> None:
    global _listener
    if _listener is not None:
        return

    # Force UTF-8 on Windows — prevents UnicodeEncodeError on cp1252 terminals
    stream = (
        io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        if hasattr(sys.stdout, "buffer")
        else sys.stdout
    )

    console = logging.StreamHandler(stream)
    console.setLevel(logging.INFO)
    console.setFormatter(_FORMATTER)

    rotating = logging.handlers.RotatingFileHandler(
        _LOG_DIR / "pipeline.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    rotating.setLevel(logging.DEBUG)
    rotating.setFormatter(_FORMATTER)

    # QueueListener runs in its own thread — only thread that touches the file
    _listener = logging.handlers.QueueListener(
        _log_queue,
        console,
        rotating,
        respect_handler_level=True,
    )
    _listener.start()
    atexit.register(_listener.stop)


def get_logger(name: str) -> logging.Logger:
    _start_listener()

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    # All threads post records to the queue — never write to file directly
    logger.addHandler(logging.handlers.QueueHandler(_log_queue))
    return logger
