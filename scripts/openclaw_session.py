"""OpenClaw JSONL session extraction."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import runtime_config


def default_session_root() -> Path | None:
    return runtime_config.openclaw_sessions_root()


def _text_parts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [{"type": "text", "text": value}] if value.strip() else []
    if isinstance(value, list):
        parts: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                parts.append({"type": "text", "text": item})
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append({"type": "text", "text": str(text)})
        return parts
    return []


def _record_to_message(record: dict[str, Any]) -> dict[str, Any] | None:
    role = str(record.get("role") or record.get("type") or "unknown").lower()
    if role in {"human", "user"}:
        role = "user"
    elif role in {"assistant", "ai"}:
        role = "assistant"
    payload = record.get("message") or record.get("content") or record.get("parts")
    parts = _text_parts(payload)
    if not parts:
        return None
    return {
        "role": role,
        "time": record.get("time") or record.get("timestamp") or record.get("created_at") or 0,
        "parts": parts,
    }


def extract_openclaw_sessions(
    session_id: str | None = None,
    last_n: int = 1,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    root = root or default_session_root()
    if root is None or not root.exists():
        return []
    candidates = list(root.glob("*/sessions/*.jsonl"))
    if not candidates:
        candidates = list(root.rglob("*.jsonl"))
    if session_id:
        candidates = [path for path in candidates if session_id in (path.stem, path.name)]
    if not candidates:
        return []
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    sessions: list[dict[str, Any]] = []
    for path in candidates[: max(1, last_n)]:
        messages: list[dict[str, Any]] = []
        first_timestamp = 0
        title = path.stem
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = _record_to_message(record)
            if not message:
                continue
            if not first_timestamp and message["time"]:
                first_timestamp = message["time"]
            messages.append(message)
        sessions.append(
            {
                "id": path.stem,
                "title": title,
                "time_created": first_timestamp,
                "model": {"id": "openclaw"},
                "tokens_input": 0,
                "tokens_output": 0,
                "messages": messages,
                "metadata": {
                    "source_agent": "openclaw",
                    "source_device": os.environ.get("COMPUTERNAME") or "unknown",
                    "created_by": os.environ.get("USERNAME") or "unknown",
                },
            }
        )
    return sessions
