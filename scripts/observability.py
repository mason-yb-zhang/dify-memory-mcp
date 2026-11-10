"""Structured logging and lightweight in-process metrics."""

from __future__ import annotations

import json
import sys
import threading
import time
from collections import Counter
from typing import Any


_METRICS: Counter[str] = Counter()
_LOCK = threading.Lock()


def log_event(event: str, level: str = "info", **fields: Any) -> None:
    payload = {
        "timestamp": time.time(),
        "level": level,
        "event": event,
        **{key: value for key, value in fields.items() if key not in {"content", "api_key"}},
    }
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), file=sys.stderr, flush=True)


def metric_increment(name: str, amount: int = 1) -> None:
    with _LOCK:
        _METRICS[name] += amount


def metrics_snapshot() -> dict[str, int]:
    with _LOCK:
        return dict(_METRICS)
