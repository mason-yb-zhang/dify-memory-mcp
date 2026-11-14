"""Single-record audit metadata and optimistic-lock helpers."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from datetime import datetime, timezone
from typing import Any


AUDIT_PREFIX = "<!-- dify-memory-audit-v1 "
AUDIT_SUFFIX = " -->"
AUDIT_RE = re.compile(
    re.escape(AUDIT_PREFIX) + r"(.*?)" + re.escape(AUDIT_SUFFIX),
    re.DOTALL,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_actor() -> str:
    return os.environ.get("DIFY_MEMORY_ACTOR") or os.environ.get("USERNAME") or "unknown"


def default_device() -> str:
    return (
        os.environ.get("DIFY_MEMORY_DEVICE_ID")
        or os.environ.get("COMPUTERNAME")
        or platform.node()
        or "unknown"
    )


def default_agent() -> str:
    if os.environ.get("DIFY_MEMORY_AGENT"):
        return os.environ["DIFY_MEMORY_AGENT"]
    if os.environ.get("CODEX_SESSION_ID"):
        return "codex"
    return "unknown"


def content_hash(content: str, audit: dict[str, Any]) -> str:
    payload = json.dumps(
        {"content": content, "audit": audit},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def pack_record(content: str, audit: dict[str, Any]) -> str:
    encoded = json.dumps(audit, ensure_ascii=False, separators=(",", ":"))
    return f"{AUDIT_PREFIX}{encoded}{AUDIT_SUFFIX}\n{content}"


def unpack_record(
    raw_content: str,
    segment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    segment = segment or {}
    match = AUDIT_RE.search(raw_content)
    if match:
        try:
            stored_audit = json.loads(match.group(1))
        except json.JSONDecodeError:
            stored_audit = {}
        content = (raw_content[: match.start()] + raw_content[match.end() :]).strip()
    else:
        stored_audit = {}
        content = raw_content.strip()

    created_at = stored_audit.get("created_at") or _unix_to_iso(
        segment.get("created_at", 0)
    )
    audit = {
        "revision": int(stored_audit.get("revision", 1)),
        "created_by": stored_audit.get("created_by", "unknown"),
        "created_at": created_at,
        "last_modified_by": stored_audit.get("last_modified_by", "unknown"),
        "last_modified_at": stored_audit.get("last_modified_at", created_at),
        "source_agent": stored_audit.get("source_agent", "unknown"),
        "source_device": stored_audit.get("source_device", "unknown"),
        "change_summary": stored_audit.get("change_summary", ""),
    }
    audit["event_date"] = stored_audit.get("event_date", "")
    audit["expiration_date"] = stored_audit.get("expiration_date", "")
    return {
        "memory_id": segment.get("id", stored_audit.get("memory_id", "")),
        "content": content,
        "audit": audit,
        "event_date": audit.get("event_date", ""),
        "expiration_date": audit.get("expiration_date", ""),
        "content_hash": content_hash(content, audit),
    }


def create_audit(
    created_by: str | None = None,
    source_agent: str | None = None,
    source_device: str | None = None,
    change_summary: str | None = None,
    event_date: str | None = None,
    expiration_date: str | None = None,
) -> dict[str, Any]:
    now = utc_now_iso()
    actor = created_by or default_actor()
    return {
        "revision": 1,
        "created_by": actor,
        "created_at": now,
        "last_modified_by": actor,
        "last_modified_at": now,
        "source_agent": source_agent or default_agent(),
        "source_device": source_device or default_device(),
        "change_summary": change_summary or "",
        "event_date": event_date or "",
        "expiration_date": expiration_date or "",
    }


def update_audit(
    current: dict[str, Any],
    modified_by: str | None = None,
    source_agent: str | None = None,
    source_device: str | None = None,
    change_summary: str | None = None,
    event_date: str | None = None,
    expiration_date: str | None = None,
) -> dict[str, Any]:
    updated = dict(current)
    updated["revision"] = int(current.get("revision", 1)) + 1
    updated["last_modified_by"] = modified_by or default_actor()
    updated["last_modified_at"] = utc_now_iso()
    updated["source_agent"] = source_agent or default_agent()
    updated["source_device"] = source_device or default_device()
    updated["change_summary"] = change_summary or ""
    if event_date is not None:
        updated["event_date"] = event_date or ""
    if expiration_date is not None:
        updated["expiration_date"] = expiration_date or ""
    return updated


def _unix_to_iso(value: int | float | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat(
            timespec="seconds"
        )
    except (ValueError, OSError):
        return ""
