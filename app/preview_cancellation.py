"""Per-worker cancellation without sharing mutable renderer state."""
from contextvars import ContextVar
from concurrent.futures import CancelledError

cancel_event = ContextVar("preview_cancel_event", default=None)


def check_cancelled():
    event = cancel_event.get()
    if event is not None and event.is_set():
        raise CancelledError("预览任务已取消")
