"""
Dify Knowledge Base Memory MCP Server

Provides persistent, cross-session memory for AI agents using a Dify
knowledge base as the vector store backend. Dify is a fork of Dify and
speaks the same `/datasets` Service API, so this server works against
either product.

Architecture:
  Agent <-> MCP stdio <-> server.py <-> Dify KB API <-> Vector Store
                                                     <-> Embedding
                                                     <-> Rerank

Tools:
  - add_memory: Store a new memory (fact/decision/preference), supports permanent flag
  - search_memory: Semantic search across all memories (short-term + long-term)
  - update_memory: Update an existing memory by segment_id (auto-detects document)
  - delete_memory: Delete a memory by segment_id (auto-detects document)
  - list_memories: List memories with pagination (supports short_term/long_term/all)
  - decay_memories: Remove cold short-term memories (hit_count=0, older than N days)
  - promote_memory: Move a short-term memory to permanent storage

Configuration (environment variables):
  DIFY_API_KEY              - Dify dataset API key (required)
  DIFY_DATASET_ID           - Knowledge base ID (required)
  DIFY_DOCUMENT_ID          - Short-term memory document ID (required)
  DIFY_PERMANENT_DOCUMENT_ID - Long-term memory document ID (optional, enables permanent features)
  DIFY_BASE_URL             - API base URL (default: http://127.0.0.1/v1)
                              DIFY_BASE_URL is accepted as a legacy alias.
  MEMORY_DECAY_DAYS         - Default decay threshold in days (default: 30)
  MEMORY_*                  - Retrieval tuning, see runtime_config.build_retrieval_model
"""

import asyncio
import ctypes
import json
import re
import time
import os
import sys
import threading
import uuid
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, Resource, TextResourceContents
from local_store import LocalStore
from runtime_config import build_retrieval_model, dify_base_url
from memory_model import (
    content_hash,
    create_audit,
    pack_record,
    unpack_record,
    update_audit,
)
from observability import log_event, metric_increment, metrics_snapshot
from openclaw_session import extract_openclaw_sessions
from privacy import sanitize_memory_content
import memory_service

# ─── Configuration ───────────────────────────────────────────────────────────
# Credential resolution order: process environment first (populated by the
# "environment" block of opencode.json), then the .env file next to this
# script. The .env fallback keeps the server alive when external tooling
# rewrites opencode.json and drops its "environment" block.
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _load_dotenv(path: str = _ENV_PATH) -> list[str]:
    """Load KEY=VALUE pairs from .env into os.environ (real env vars win).

    Special case for DIFY_API_KEY: the Dify KB API only accepts dataset-
    format keys. If the process environment carries a foreign-format key
    (e.g. an sk- gateway key injected by other tooling), it must not shadow
    the valid dataset- key in .env - using it yields HTTP 401 for every call.
    """
    loaded = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if not (key and value):
                    continue
                current = os.environ.get(key)
                if (
                    key == "DIFY_API_KEY"
                    and current
                    and not current.startswith("dataset-")
                ):
                    os.environ[key] = value
                    loaded.append(key)
                    continue
                if current:
                    continue
                os.environ[key] = value
                loaded.append(key)
    except OSError:
        pass
    return loaded


_load_dotenv()
memory_service.CLIENT.configure_from_env()

# Session archive module (optional - graceful degradation if not present)
try:
    from session_store import (
        save_session,
        list_sessions,
        search_session,
        get_session_detail,
        delete_session,
        close_http_client as close_session_http_client,
    )

    SESSION_TOOLS_AVAILABLE = True
except ImportError:
    SESSION_TOOLS_AVAILABLE = False

    async def close_session_http_client() -> None:
        return None

# ─── Configuration ───────────────────────────────────────────────────────────
# No hardcoded fallback values — env vars MUST be set by install.py or .env
DIFY_API_KEY = os.environ.get("DIFY_API_KEY", "")
DIFY_BASE_URL = dify_base_url()
DATASET_ID = os.environ.get("DIFY_DATASET_ID", "")
DOCUMENT_ID = os.environ.get("DIFY_DOCUMENT_ID", "")  # short-term (decayable)
PERMANENT_DOCUMENT_ID = os.environ.get(
    "DIFY_PERMANENT_DOCUMENT_ID", ""
)  # long-term (never decayed)
DECAY_THRESHOLD_DAYS = int(os.environ.get("MEMORY_DECAY_DAYS", "30"))
SERVICE_VERSION = "1.10.1"
LOCAL_STORE = LocalStore()


def _idle_timeout_seconds() -> float:
    # Stdio hosts may keep a healthy connection idle without reconnecting on exit.
    raw = os.environ.get("DIFY_MEMORY_MCP_IDLE_TIMEOUT_SECONDS", "0")
    try:
        value = float(raw)
    except ValueError:
        return 0.0
    return value if 0 <= value < float("inf") else 0.0


def _parent_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if not process:
            return ctypes.windll.kernel32.GetLastError() == 5
        try:
            result = ctypes.windll.kernel32.WaitForSingleObject(process, 0)
            return result == 0x00000102
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class _ActivityReadStream:
    def __init__(self, inner: Any, on_activity: Any) -> None:
        self._inner = inner
        self._on_activity = on_activity

    async def receive(self) -> Any:
        item = await self._inner.receive()
        self._on_activity()
        return item

    def receive_nowait(self) -> Any:
        item = self._inner.receive_nowait()
        self._on_activity()
        return item

    async def __anext__(self) -> Any:
        item = await self._inner.__anext__()
        self._on_activity()
        return item

    def __aiter__(self) -> "_ActivityReadStream":
        return self

    async def __aenter__(self) -> "_ActivityReadStream":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._inner.aclose()

    def close(self) -> None:
        self._inner.close()

    @property
    def extra_attributes(self) -> Any:
        return self._inner.extra_attributes

    @property
    def statistics(self) -> Any:
        return self._inner.statistics


def _watch_lifecycle(
    last_activity: list[float],
    idle_timeout: float,
    parent_pid: int,
) -> str:
    interval = max(0.05, min(5.0, idle_timeout / 4 if idle_timeout else 5.0))
    while True:
        if not _parent_is_alive(parent_pid):
            return "parent_exited"
        if idle_timeout and time.monotonic() - last_activity[0] >= idle_timeout:
            return "idle_timeout"
        time.sleep(interval)


def _exit_when_stale(
    last_activity: list[float],
    idle_timeout: float,
    parent_pid: int,
) -> None:
    if os.environ.get("DIFY_MEMORY_MCP_LIFECYCLE_DEBUG") == "1":
        print(
            f"[dify-memory] lifecycle monitor idle={idle_timeout} parent={parent_pid}",
            file=sys.stderr,
            flush=True,
        )
    reason = _watch_lifecycle(last_activity, idle_timeout, parent_pid)
    print(f"[dify-memory] MCP stdio closing: {reason}", file=sys.stderr, flush=True)
    os._exit(0)


def _missing_config() -> list[str]:
    missing = []
    if not DIFY_API_KEY:
        missing.append("DIFY_API_KEY")
    if not DATASET_ID:
        missing.append("DIFY_DATASET_ID")
    if not DOCUMENT_ID:
        missing.append("DIFY_DOCUMENT_ID")
    return missing


def _build_config_error(missing: list[str]) -> str:
    if not missing:
        return ""
    return (
        f"dify-memory 凭证缺失：{', '.join(missing)}，记忆服务暂不可用。\n"
        "请提示用户提供这些值：用户可以把值直接发给我，由我写入 "
        f"{_ENV_PATH}；或由用户手动运行 `python install.py --api-key <KEY>`。\n"
        "注意：凭证写入 .env 即可，不要写进 opencode.json 的 environment（该文件会被自动重写工具冲掉）。"
    )


# Non-empty => server runs in degraded mode; every tool call returns the prompt.
CONFIG_ERROR = _build_config_error(_missing_config())


# ─── Dify API Client ────────────────────────────────────────────────────────
# Module-level shared client — reuses TCP connections across all requests
_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Get or create the shared httpx AsyncClient."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {DIFY_API_KEY}",
                "Content-Type": "application/json",
            },
        )
    return _http_client


async def dify_request(method: str, path: str, body: dict | None = None) -> dict:
    """Make HTTP request to Dify API using shared client."""
    url = f"{DIFY_BASE_URL}{path}"
    client = _get_client()
    try:
        if method == "GET":
            resp = await client.get(url, params=body)
        elif method == "POST":
            resp = await client.post(url, json=body)
        elif method == "DELETE":
            resp = await client.delete(url)
            if resp.status_code == 204:
                return {"success": True}
        elif method == "PATCH":
            resp = await client.patch(url, json=body)
        else:
            raise ValueError(f"Unsupported method: {method}")

        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}: {resp.text}"}
        if not resp.text:
            return {"success": True}
        parsed = resp.json()
        # Dify DELETE sometimes returns body "204\n" which parses as int 204
        if not isinstance(parsed, dict):
            return {"success": True}
        return parsed
    except httpx.TimeoutException:
        return {"error": "Request timed out (30s). Check network or try again."}
    except httpx.ConnectError as e:
        return {"error": f"Connection failed: {e}. Check intranet connectivity."}


# ─── Memory Operations ───────────────────────────────────────────────────────
def _strip_private_blocks(content: str) -> str:
    """Remove <private>...</private> blocks, including unclosed trailing blocks."""
    without_closed = re.sub(
        r"<private\b[^>]*>.*?</private>", " ", content, flags=re.IGNORECASE | re.DOTALL
    )
    return re.sub(
        r"<private\b[^>]*>.*$", " ", without_closed, flags=re.IGNORECASE | re.DOTALL
    )


def _prepare_memory_content(content: str) -> str | None:
    """Sanitize memory text before it can be persisted."""
    cleaned = _strip_private_blocks(content)
    cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()
    return cleaned or None


async def add_memory(
    content: str, keywords: list[str] | None = None, permanent: bool = False
) -> dict:
    """Add a new memory segment to short-term or long-term storage."""
    content = _prepare_memory_content(content)
    if content is None:
        return {
            "success": True,
            "skipped": True,
            "reason": "content only contained private text",
        }

    target_doc = PERMANENT_DOCUMENT_ID if permanent else DOCUMENT_ID
    if permanent and not PERMANENT_DOCUMENT_ID:
        return {
            "error": "Long-term memory not configured. Re-run install.py to create permanent document."
        }
    path = f"/datasets/{DATASET_ID}/documents/{target_doc}/segments"
    segment = {"content": content}
    if keywords:
        segment["keywords"] = keywords
    body = {"segments": [segment]}
    result = await dify_request("POST", path, body)
    if "data" in result and result["data"]:
        seg = result["data"][0]
        return {
            "success": True,
            "segment_id": seg["id"],
            "content": seg["content"],
            "permanent": permanent,
            "status": seg.get("status", "unknown"),
        }
    return result


async def search_memory(query: str, top_k: int = 5) -> dict:
    """Search memories by semantic similarity."""
    path = f"/datasets/{DATASET_ID}/retrieve"
    body = {
        "query": query,
        "retrieval_model": build_retrieval_model(top_k),
    }
    result = await dify_request("POST", path, body)
    if "records" in result:
        memories = []
        for record in result["records"]:
            seg = record.get("segment", {})
            doc_id = record.get("document_id", seg.get("document_id", ""))
            is_permanent = (
                (doc_id == PERMANENT_DOCUMENT_ID) if PERMANENT_DOCUMENT_ID else False
            )
            memories.append(
                {
                    "segment_id": seg.get("id", ""),
                    "content": seg.get("content", ""),
                    "score": record.get("score", 0),
                    "hit_count": seg.get("hit_count", 0),
                    "keywords": seg.get("keywords", []),
                    "permanent": is_permanent,
                    "created_at": seg.get("created_at", 0),
                }
            )
        return {"memories": memories, "count": len(memories)}
    return result


async def update_memory(
    segment_id: str, content: str, keywords: list[str] | None = None
) -> dict:
    """Update an existing memory segment. Auto-detects which document it belongs to."""
    content = _prepare_memory_content(content)
    if content is None:
        return {
            "success": True,
            "skipped": True,
            "reason": "content only contained private text",
            "segment_id": segment_id,
        }

    segment = {"content": content, "enabled": True}
    if keywords:
        segment["keywords"] = keywords
    body = {"segment": segment}

    # Try short-term first
    path = f"/datasets/{DATASET_ID}/documents/{DOCUMENT_ID}/segments/{segment_id}"
    result = await dify_request("POST", path, body)
    if "data" in result:
        return {
            "success": True,
            "segment_id": segment_id,
            "content": content,
            "permanent": False,
        }

    # Try long-term if short-term failed and permanent doc exists
    if PERMANENT_DOCUMENT_ID and "error" in result:
        path = f"/datasets/{DATASET_ID}/documents/{PERMANENT_DOCUMENT_ID}/segments/{segment_id}"
        result = await dify_request("POST", path, body)
        if "data" in result:
            return {
                "success": True,
                "segment_id": segment_id,
                "content": content,
                "permanent": True,
            }

    return result


async def _find_segment_in_document(segment_id: str, document_id: str) -> dict:
    """Check whether segment_id exists in the given document by paginating /segments.

    Returns {"found": bool} or {"error": str}.
    Uses a safety cap of 50 pages (5000 segments) to prevent runaway loops.
    """
    page = 1
    max_pages = 50
    while page <= max_pages:
        path = f"/datasets/{DATASET_ID}/documents/{document_id}/segments"
        result = await dify_request("GET", path, {"page": page, "limit": 100})
        if "error" in result:
            return {"error": result["error"]}
        data = result.get("data", [])
        for seg in data:
            if seg.get("id") == segment_id:
                return {"found": True}
        total = result.get("total", 0)
        if total and page * 100 >= total:
            break
        if len(data) < 100:
            break
        page += 1
    return {"found": False}


async def delete_memory(segment_id: str) -> dict:
    """Delete a memory segment. Auto-detects which document it belongs to.

    Verifies that the segment exists before issuing DELETE, because the Dify
    DELETE endpoint returns success=True even for non-existent segments.
    """
    # Try short-term first
    short_lookup = await _find_segment_in_document(segment_id, DOCUMENT_ID)
    if "error" in short_lookup:
        return {"error": f"Failed to check short-term memory: {short_lookup['error']}"}
    if short_lookup.get("found"):
        path = f"/datasets/{DATASET_ID}/documents/{DOCUMENT_ID}/segments/{segment_id}"
        result = await dify_request("DELETE", path)
        if result.get("success"):
            return {"success": True, "segment_id": segment_id, "permanent": False}
        return result

    # Try long-term if permanent doc exists
    if PERMANENT_DOCUMENT_ID:
        long_lookup = await _find_segment_in_document(segment_id, PERMANENT_DOCUMENT_ID)
        if "error" in long_lookup:
            return {
                "error": f"Failed to check long-term memory: {long_lookup['error']}"
            }
        if long_lookup.get("found"):
            path = f"/datasets/{DATASET_ID}/documents/{PERMANENT_DOCUMENT_ID}/segments/{segment_id}"
            result = await dify_request("DELETE", path)
            if result.get("success"):
                return {"success": True, "segment_id": segment_id, "permanent": True}
            return result

    return {"error": f"Segment {segment_id} not found"}


async def list_memories(
    page: int = 1, limit: int = 20, keyword: str = "", storage: str = "all"
) -> dict:
    """List memory segments with pagination. storage: 'short_term', 'long_term', or 'all'."""

    def _format_segments(data: list, permanent: bool) -> list:
        return [
            {
                "segment_id": seg.get("id", ""),
                "content": seg.get("content", ""),
                "hit_count": seg.get("hit_count", 0),
                "keywords": seg.get("keywords", []),
                "enabled": seg.get("enabled", True),
                "permanent": permanent,
                "created_at": seg.get("created_at", 0),
            }
            for seg in data
        ]

    memories = []
    total = 0
    params = {"page": page, "limit": limit}
    if keyword:
        params["keyword"] = keyword

    # Short-term memories
    if storage in ("short_term", "all"):
        path = f"/datasets/{DATASET_ID}/documents/{DOCUMENT_ID}/segments"
        result = await dify_request("GET", path, params)
        if "error" not in result and "data" in result:
            memories.extend(_format_segments(result["data"], permanent=False))
            total += result.get("total", len(result["data"]))

    # Long-term memories
    if storage in ("long_term", "all") and PERMANENT_DOCUMENT_ID:
        path = f"/datasets/{DATASET_ID}/documents/{PERMANENT_DOCUMENT_ID}/segments"
        result = await dify_request("GET", path, params)
        if "error" not in result and "data" in result:
            memories.extend(_format_segments(result["data"], permanent=True))
            total += result.get("total", len(result["data"]))

    result = {"memories": memories, "total": total, "page": page, "storage": storage}
    if storage == "all" and PERMANENT_DOCUMENT_ID:
        result["note"] = (
            "When storage='all', page/limit applies to each document independently."
        )
    return result


async def decay_memories(days: int | None = None) -> dict:
    """Remove cold memories (hit_count=0, older than threshold)."""
    threshold_days = days or DECAY_THRESHOLD_DAYS
    cutoff_time = int(time.time()) - (threshold_days * 86400)

    # Phase 1: Collect all segments with pagination
    all_segments = []
    page = 1
    max_pages = 50  # Safety cap to avoid infinite loop
    while page <= max_pages:
        path = f"/datasets/{DATASET_ID}/documents/{DOCUMENT_ID}/segments"
        result = await dify_request("GET", path, {"page": page, "limit": 100})
        if "error" in result:
            break
        data = result.get("data", [])
        if not data:
            break
        all_segments.extend(data)
        # Use total field for reliable termination
        total = result.get("total", 0)
        if total and len(all_segments) >= total:
            break
        if len(data) < 100:
            break
        page += 1

    # Phase 2: Filter cold memories
    # Skip container document descriptions (both old and new format)
    CONTAINER_PREFIXES = (
        "This document serves as",
        "Short-term memory container",
        "Long-term memory container",
    )
    cold_memories = []
    for seg in all_segments:
        if seg.get("hit_count", 0) == 0 and seg.get("created_at", 0) < cutoff_time:
            content = seg.get("content", "")
            if any(content.startswith(prefix) for prefix in CONTAINER_PREFIXES):
                continue
            cold_memories.append(seg)

    # Phase 3: Delete with rate limiting (max 5 concurrent, 200ms between batches)
    deleted = []
    failed = []
    batch_size = 5
    for i in range(0, len(cold_memories), batch_size):
        batch = cold_memories[i : i + batch_size]
        for seg in batch:
            path = (
                f"/datasets/{DATASET_ID}/documents/{DOCUMENT_ID}/segments/{seg['id']}"
            )
            result = await dify_request("DELETE", path)
            if result.get("success"):
                deleted.append(seg["id"])
            else:
                failed.append(
                    {"id": seg["id"], "error": result.get("error", "unknown")}
                )
        # Rate limit: pause between batches
        if i + batch_size < len(cold_memories):
            await asyncio.sleep(0.2)

    response = {
        "scanned": len(all_segments),
        "cold_found": len(cold_memories),
        "deleted": len(deleted),
        "threshold_days": threshold_days,
    }
    if failed:
        response["failed"] = failed
    return response


async def promote_memory(segment_id: str) -> dict:
    """Promote a short-term memory to long-term (permanent) storage.
    Copies the segment to permanent doc and deletes from short-term doc.
    """
    if not PERMANENT_DOCUMENT_ID:
        return {
            "error": "Long-term memory not configured. Re-run install.py to create permanent document."
        }

    # Step 1: Find the segment in short-term doc (with pagination)
    source_seg = None
    page = 1
    while page <= 20:  # safety cap
        path = f"/datasets/{DATASET_ID}/documents/{DOCUMENT_ID}/segments"
        result = await dify_request("GET", path, {"page": page, "limit": 100})
        if "error" in result:
            return result
        data = result.get("data", [])
        if not data:
            break
        for seg in data:
            if seg.get("id") == segment_id:
                source_seg = seg
                break
        if source_seg:
            break
        if len(data) < 100:
            break
        page += 1

    if not source_seg:
        return {
            "error": f"Segment {segment_id} not found in short-term memory. It may already be in long-term storage."
        }

    # Step 2: Create in permanent doc
    perm_path = f"/datasets/{DATASET_ID}/documents/{PERMANENT_DOCUMENT_ID}/segments"
    body = {
        "segments": [
            {
                "content": source_seg["content"],
                "keywords": source_seg.get("keywords", []),
            }
        ]
    }
    create_result = await dify_request("POST", perm_path, body)
    if "error" in create_result:
        return {
            "error": f"Failed to create in permanent storage: {create_result['error']}"
        }

    new_id = create_result["data"][0]["id"] if create_result.get("data") else "unknown"

    # Step 3: Delete from short-term doc (check result)
    del_path = f"/datasets/{DATASET_ID}/documents/{DOCUMENT_ID}/segments/{segment_id}"
    del_result = await dify_request("DELETE", del_path)

    response = {
        "success": True,
        "old_segment_id": segment_id,
        "new_segment_id": new_id,
        "content": source_seg["content"],
        "message": "Memory promoted to long-term storage. It will never be deleted by decay.",
    }
    if not del_result.get("success"):
        response["warning"] = (
            "Promoted successfully but failed to delete from short-term. Memory exists in both documents."
        )

    return response


# ─── MCP Server ─────────────────────────────────────────────────────────────
app = Server("dify-memory")


@app.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(
            uri="memory://search",
            name="Search Memories",
            description=(
                "Semantic search across all memories (short-term + long-term). "
                "Read as a resource with a ?q= query parameter, e.g. memory://search?q=Creo+compile"
            ),
            mimeType="application/json",
        ),
        Resource(
            uri="memory://list",
            name="List Memories",
            description=(
                "List stored memories with pagination. "
                "Read as a resource with optional ?page=1&limit=20&keyword=&storage=all"
            ),
            mimeType="application/json",
        ),
        Resource(
            uri="memory://sessions",
            name="List Archived Sessions",
            description="Browse archived session index (local index only). Optional ?limit=20&keyword=",
            mimeType="application/json",
        ),
        Resource(
            uri="memory://health",
            name="Memory Service Health",
            description="Configuration, Dify connectivity, cache, queue, and metrics status.",
            mimeType="application/json",
        ),
    ]


@app.read_resource()
async def read_resource(uri: str) -> str:
    from urllib.parse import urlparse, parse_qs

    uri = str(uri)
    parsed = urlparse(uri)
    qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    if parsed.scheme != "memory":
        raise ValueError(f"Unsupported URI scheme: {parsed.scheme}")
    if parsed.hostname == "search":
        query = qs.get("q", "")
        if not query:
            raise ValueError("Missing required query parameter: ?q=<search text>")
        result = await memory_service.search_memory(
            query=query, top_k=int(qs.get("top_k", "5"))
        )
    elif parsed.hostname == "list":
        result = await memory_service.list_memories(
            page=int(qs.get("page", "1")),
            limit=int(qs.get("limit", "20")),
            keyword=qs.get("keyword", ""),
            storage=qs.get("storage", "all"),
        )
    elif parsed.hostname == "sessions":
        if not SESSION_TOOLS_AVAILABLE:
            raise ValueError("Session archive module not available")
        result = await list_sessions(
            limit=int(qs.get("limit", "20")),
            keyword=qs.get("keyword", ""),
        )
    elif parsed.hostname == "health":
        result = await memory_service.get_health(check_dify=qs.get("check", "1") == "1")
    else:
        raise ValueError(f"Unknown resource path: {parsed.hostname}")
    return json.dumps(result, ensure_ascii=False, indent=2)


@app.list_tools()
async def list_tools() -> list[Tool]:
    tools = [
        Tool(
            name="add_memory",
            description=(
                "Store a NEW fact/decision/preference. WRITE RULES: (1) 15-80 self-contained words; "
                "ground relative time to absolute YYYY-MM-DD; keep proper nouns/paths/versions — "
                "'a memory without the name is unfindable'. (2) SAME FACT that changed → call update_memory "
                "with expected_content_hash, do NOT add a duplicate. (3) Optional fact_key='fact:xxx' auto-upserts "
                "onto the existing memory with that tag. Set permanent=true only for durable prefs/decisions. "
                "Trigger: user says '记住这个/记下来/别忘了/永远记住', or agent discovers important info worth persisting."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Self-contained memory text (15-80 words; absolute dates; keep proper nouns)",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2-6 tags; use fact:<slug> as a stable identity key for upsert",
                    },
                    "permanent": {
                        "type": "boolean",
                        "description": "If true, store as long-term memory (never deleted by decay). Use for critical preferences, decisions, and facts.",
                        "default": False,
                    },
                    "fact_key": {
                        "type": "string",
                        "description": "Stable identity like fact:user-cad-tool. If a memory already has this tag, update it instead of adding.",
                    },
                    "event_date": {
                        "type": "string",
                        "description": "When the fact became true (YYYY-MM-DD). Used for temporal ranking.",
                    },
                    "expiration_date": {
                        "type": "string",
                        "description": "Hide after this date (YYYY-MM-DD). Expired memories are hidden from search/list unless show_expired=true.",
                    },
                    "created_by": {"type": "string", "description": "Creator identity"},
                    "source_agent": {"type": "string", "description": "Originating agent"},
                    "source_device": {"type": "string", "description": "Originating device"},
                    "change_summary": {"type": "string", "description": "Optional creation/change summary"},
                    "idempotency_key": {"type": "string", "description": "Stable key preventing duplicate writes during retries"},
                },
                "required": ["content"],
            },
        ),
        Tool(
            name="search_memory",
            description=(
                "Semantic recall for association/brainstorm. NOT for asserting current state: for that use "
                "list_memories (newest first) or get_memory and compare audit.last_modified_at. "
                "Results are re-ranked with recency boost so fresher facts surface first. "
                "Default use_cache=false to avoid stale answers. Trigger: user asks '你还记得吗/之前说过什么/回忆一下'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (natural language). Include time words (当前/2026-09) to activate temporal boost.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results (default: 5)",
                        "default": 5,
                    },
                    "sort": {
                        "type": "string",
                        "enum": ["relevance", "updated_at", "created_at"],
                        "default": "relevance",
                        "description": "relevance=fused score+recency; updated_at/created_at=chronological",
                    },
                    "prefer": {
                        "type": "string",
                        "enum": ["", "current", "history"],
                        "default": "",
                        "description": "current=stronger recency boost for 'what is true now'",
                    },
                    "explain": {
                        "type": "boolean",
                        "default": False,
                        "description": "Return score_details (dify_score/recency/temporal/final)",
                    },
                    "reference_date": {
                        "type": "string",
                        "description": "Treat this YYYY-MM-DD as 'now' for recency/temporal scoring",
                    },
                    "show_expired": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include memories past their expiration_date",
                    },
                    "created_by": {"type": "string", "description": "Filter by creator"},
                    "source_agent": {"type": "string", "description": "Filter by source agent"},
                    "source_device": {"type": "string", "description": "Filter by source device"},
                    "permanent": {"type": "boolean", "description": "Filter by lifetime"},
                    "keyword": {"type": "string", "description": "Filter content and keywords"},
                    "use_cache": {
                        "type": "boolean",
                        "default": False,
                        "description": "Use 5-minute local cache. Keep false when freshness matters.",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="update_memory",
            description=(
                "Overwrite an existing memory in place (single-latest-record model). "
                "REQUIRED whenever a known fact changes — do not add_memory a second copy. "
                "Pass expected_content_hash from get_memory/search_memory unless allow_unsafe_overwrite=true."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "segment_id": {
                        "type": "string",
                        "description": "The segment ID of the memory to update",
                    },
                    "content": {
                        "type": "string",
                        "description": "New content for the memory (same write style as add_memory)",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional updated keywords",
                    },
                    "expected_content_hash": {"type": "string", "description": "Current record hash; required unless allow_unsafe_overwrite=true"},
                    "allow_unsafe_overwrite": {"type": "boolean", "default": False},
                    "event_date": {"type": "string", "description": "Updated event date (YYYY-MM-DD)"},
                    "expiration_date": {"type": "string", "description": "Updated expiration date (YYYY-MM-DD)"},
                    "updated_by": {"type": "string", "description": "Editor identity"},
                    "source_agent": {"type": "string", "description": "Editing agent"},
                    "source_device": {"type": "string", "description": "Editing device"},
                    "change_summary": {"type": "string", "description": "Optional change summary"},
                },
                "required": ["segment_id", "content"],
            },
        ),
        Tool(
            name="delete_memory",
            description="Delete a memory by its segment_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "segment_id": {
                        "type": "string",
                        "description": "The segment ID of the memory to delete",
                    },
                },
                "required": ["segment_id"],
            },
        ),
        Tool(
            name="list_memories",
            description=(
                "Browse memories sorted by last_modified_at DESC. Preferred tool for 'what is the latest state' — "
                "read audit.last_modified_at / content_hash before update_memory. Use search_memory only for association."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "page": {
                        "type": "integer",
                        "description": "Page number (default: 1)",
                        "default": 1,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Items per page (default: 20, max: 100)",
                        "default": 20,
                    },
                    "keyword": {
                        "type": "string",
                        "description": "Filter by keyword (e.g. fact:user-cad-tool)",
                        "default": "",
                    },
                    "storage": {
                        "type": "string",
                        "description": "Which storage to list: 'short_term', 'long_term', or 'all' (default: 'all')",
                        "default": "all",
                        "enum": ["short_term", "long_term", "all"],
                    },
                    "show_expired": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include memories past their expiration_date",
                    },
                    "created_by": {"type": "string", "description": "Filter by creator"},
                    "source_agent": {"type": "string", "description": "Filter by source agent"},
                    "source_device": {"type": "string", "description": "Filter by source device"},
                },
            },
        ),
        Tool(
            name="decay_memories",
            description="Remove cold memories that have never been retrieved (hit_count=0) and are older than the threshold. Only affects short-term memories; permanent memories are never touched.",
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Age threshold in days (default: 30)",
                        "default": 30,
                    },
                },
            },
        ),
        Tool(
            name="promote_memory",
            description="Promote a short-term memory to long-term (permanent) storage. The memory will be moved and will never be deleted by decay. Use when a short-term memory proves repeatedly valuable, or user says '永远记住/这个很重要'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "segment_id": {
                        "type": "string",
                        "description": "The segment ID of the short-term memory to promote",
                    },
                },
                "required": ["segment_id"],
            },
        ),
        Tool(
            name="get_memory",
            description=(
                "Authoritative current record with audit fields and content_hash. "
                "ALWAYS use this (or list_memories) before claiming current state or calling update_memory."
            ),
            inputSchema={"type": "object", "properties": {"segment_id": {"type": "string"}}, "required": ["segment_id"]},
        ),
        Tool(
            name="flush_offline_queue",
            description="Retry queued memory writes after connectivity is restored.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_health",
            description="Check configuration, Dify connectivity, cache, queue, and metrics.",
            inputSchema={"type": "object", "properties": {"check_dify": {"type": "boolean", "default": True}}},
        ),
    ]

    # ─── Session Archive Tools (conditionally added) ────────────────
    if SESSION_TOOLS_AVAILABLE:
        tools.extend(
            [
                Tool(
                    name="save_session",
                    description=(
                        "Archive a full conversation session. Extracts content from OpenCode DB (or accepts manual input), "
                        "uploads full text to Dify sessions KB, and writes a lightweight index locally. "
                        "Use at end of important sessions to enable cross-tool context sharing. "
                        "Supports incremental append: subsequent calls only upload new messages."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "session_id": {
                                "type": "string",
                                "description": "OpenCode session ID to archive (omit for most recent session)",
                            },
                            "source": {
                                "type": "string",
                                "enum": ["configured", "opencode", "openclaw", "manual"],
                                "description": "Source: MCPorter config, OpenCode DB, OpenClaw JSONL, or manual content",
                                "default": "configured",
                            },
                            "content": {
                                "type": "string",
                                "description": "Session content (required if source='manual')",
                            },
                            "title": {
                                "type": "string",
                                "description": "Override session title",
                            },
                            "created_by": {"type": "string", "description": "Creator identity"},
                            "source_agent": {"type": "string", "description": "Source agent"},
                            "source_device": {"type": "string", "description": "Source device"},
                        },
                    },
                ),
                Tool(
                    name="list_sessions",
                    description=(
                        "Browse archived session index (local, lightweight, no API calls). "
                        "Shows session titles, dates, sources, and summaries. "
                        "Use this first to find relevant sessions before calling search_session. "
                        "Indexes are stored in Dify cloud, accessible from all tools sharing this MCP."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "limit": {
                                "type": "integer",
                                "description": "Max results (default: 20)",
                                "default": 20,
                            },
                            "keyword": {
                                "type": "string",
                                "description": "Filter by keyword in index content",
                                "default": "",
                            },
                        },
                    },
                ),
                Tool(
                    name="search_session",
                    description=(
                        "Deep semantic search across FULL archived session content in Dify KB. "
                        "Unlike list_sessions (local index only), this searches the complete conversation text. "
                        "Use when you need to find specific details from past sessions across tools."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query (natural language)",
                            },
                            "top_k": {
                                "type": "integer",
                                "description": "Number of results (default: 5)",
                                "default": 5,
                            },
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="get_session_detail",
                    description="Read the full content of a specific archived session segment by its Dify segment ID.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "segment_id": {
                                "type": "string",
                                "description": "Dify segment ID from search_session results",
                            },
                            "document_id": {
                                "type": "string",
                                "description": "Optional. When provided, scans only that document (fast path). Omit to search all session documents.",
                            },
                        },
                        "required": ["segment_id"],
                    },
                ),
                Tool(
                    name="delete_session",
                    description="Delete an archived session completely (full Document + index entry). Irreversible.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "session_id": {
                                "type": "string",
                                "description": "Session ID to delete (from list_sessions output)",
                            },
                        },
                        "required": ["session_id"],
                    },
                ),
            ]
        )

    return tools


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if CONFIG_ERROR:
        return [
            TextContent(
                type="text",
                text=json.dumps({"error": CONFIG_ERROR}, ensure_ascii=False, indent=2),
            )
        ]
    try:
        if name == "add_memory":
            result = await memory_service.add_memory(**arguments)
            # Auto-sync: if current session is already archived, incrementally append new messages
            if SESSION_TOOLS_AVAILABLE:
                try:
                    sync_result = await save_session(source="configured")
                    if (
                        sync_result.get("mode") == "incremental"
                        and sync_result.get("new_messages", 0) > 0
                    ):
                        result["session_synced"] = sync_result["new_messages"]
                except Exception:
                    pass  # Session sync failure should never block memory operations
        elif name == "search_memory":
            result = await memory_service.search_memory(**arguments)
        elif name == "update_memory":
            result = await memory_service.update_memory(**arguments)
        elif name == "delete_memory":
            result = await delete_memory(segment_id=arguments["segment_id"])
        elif name == "list_memories":
            result = await memory_service.list_memories(**arguments)
        elif name == "decay_memories":
            result = await decay_memories(days=arguments.get("days"))
        elif name == "promote_memory":
            result = await promote_memory(segment_id=arguments["segment_id"])
        elif name == "get_memory":
            result = await memory_service.get_memory(**arguments)
        elif name == "flush_offline_queue":
            result = await memory_service.flush_offline_queue()
        elif name == "get_health":
            result = await memory_service.get_health(**arguments)
        # Session Archive tools
        elif name == "save_session" and SESSION_TOOLS_AVAILABLE:
            content = arguments.get("content")
            if arguments.get("source", "configured") == "manual" and content is not None:
                content = _prepare_memory_content(content)
                if content is None:
                    result = {
                        "success": True,
                        "skipped": True,
                        "reason": "content only contained private text",
                    }
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(result, ensure_ascii=False, indent=2),
                        )
                    ]
            result = await save_session(
                session_id=arguments.get("session_id"),
                source=arguments.get("source", "configured"),
                content=content,
                title=arguments.get("title"),
                created_by=arguments.get("created_by"),
                source_agent=arguments.get("source_agent"),
                source_device=arguments.get("source_device"),
            )
        elif name == "list_sessions" and SESSION_TOOLS_AVAILABLE:
            result = await list_sessions(
                limit=arguments.get("limit", 20),
                keyword=arguments.get("keyword", ""),
            )
        elif name == "search_session" and SESSION_TOOLS_AVAILABLE:
            result = await search_session(
                query=arguments["query"],
                top_k=arguments.get("top_k", 5),
            )
        elif name == "get_session_detail" and SESSION_TOOLS_AVAILABLE:
            result = await get_session_detail(
                segment_id=arguments["segment_id"],
                document_id=arguments.get("document_id"),
            )
        elif name == "delete_session" and SESSION_TOOLS_AVAILABLE:
            result = await delete_session(session_id=arguments["session_id"])
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        result = {"error": str(e)}

    return [
        TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))
    ]


def main():
    last_activity = [time.monotonic()]
    parent_pid = os.getppid()
    idle_timeout = _idle_timeout_seconds()
    threading.Thread(
        target=_exit_when_stale,
        args=(last_activity, idle_timeout, parent_pid),
        daemon=True,
    ).start()

    if CONFIG_ERROR:
        print(f"[dify-memory] ERROR: {CONFIG_ERROR}", file=sys.stderr)
        print(
            "[dify-memory] Starting in degraded mode: tool calls will return the configuration prompt above.",
            file=sys.stderr,
        )
    elif not PERMANENT_DOCUMENT_ID:
        print(
            "[dify-memory] WARNING: DIFY_PERMANENT_DOCUMENT_ID not set. Long-term memory disabled.",
            file=sys.stderr,
        )
        print(
            "[dify-memory] Re-run install.py to create the permanent memory document.",
            file=sys.stderr,
        )

    async def run():
        try:
            async with stdio_server() as (read_stream, write_stream):
                activity_stream = _ActivityReadStream(
                    read_stream,
                    lambda: last_activity.__setitem__(0, time.monotonic()),
                )
                await app.run(
                    activity_stream,
                    write_stream,
                    app.create_initialization_options(),
                )
        finally:
            if _http_client and not _http_client.is_closed:
                await _http_client.aclose()
            await close_session_http_client()

    asyncio.run(run())


if __name__ == "__main__":
    main()
