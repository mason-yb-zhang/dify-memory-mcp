"""Small local cache and idempotent offline write queue."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import runtime_config

DEFAULT_CACHE_TTL_SECONDS = 300


def default_state_dir() -> Path:
    return runtime_config.state_dir()


class LocalStore:
    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = state_dir or default_state_dir()
        self.cache_path = self.state_dir / "cache.json"
        self.queue_path = self.state_dir / "offline-queue.json"

    def _read(self, path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def _write(self, path: Path, value: Any) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=self.state_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.remove(temp_name)

    def cache_get(self, key: str) -> Any:
        entry = self._read(self.cache_path, {}).get(key)
        if entry is None:
            return None
        # Legacy entries stored the payload bare; treat as expired so callers
        # always revalidate after the TTL introduction.
        if not (isinstance(entry, dict) and "_cached_at" in entry):
            return None
        ttl = entry.get("_ttl", DEFAULT_CACHE_TTL_SECONDS)
        if ttl >= 0 and time.time() - float(entry["_cached_at"]) > ttl:
            return None
        return entry.get("value")

    def cache_set(self, key: str, value: Any, ttl: int | None = None) -> None:
        cache = self._read(self.cache_path, {})
        cache[key] = {
            "_cached_at": time.time(),
            "_ttl": DEFAULT_CACHE_TTL_SECONDS if ttl is None else ttl,
            "value": value,
        }
        self._write(self.cache_path, cache)

    def cache_status(self) -> dict[str, Any]:
        cache = self._read(self.cache_path, {})
        return {
            "path": str(self.cache_path),
            "keys": sorted(cache),
            "updated_at": self.cache_path.stat().st_mtime if self.cache_path.exists() else None,
        }

    def enqueue(self, operation: dict[str, Any]) -> None:
        queue = self._read(self.queue_path, [])
        queue.append(operation)
        self._write(self.queue_path, queue)

    def pending(self) -> list[dict[str, Any]]:
        return self._read(self.queue_path, [])

    def resolve(self, operation_id: str) -> None:
        queue = [item for item in self.pending() if item.get("operation_id") != operation_id]
        self._write(self.queue_path, queue)

    def queue_status(self) -> dict[str, Any]:
        queue = self.pending()
        return {
            "path": str(self.queue_path),
            "pending": len(queue),
            "operation_ids": [item.get("operation_id", "") for item in queue],
        }
