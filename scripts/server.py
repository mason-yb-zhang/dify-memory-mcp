"""
Dify Knowledge Base Memory MCP Server

Provides persistent, cross-session memory for AI agents using Dify's
knowledge base as the vector store backend.

Architecture:
  Agent <-> MCP stdio <-> server.py <-> Dify KB API <-> Vector Store (cloud)
                                                     <-> Embedding (built-in)
                                                     <-> Rerank (built-in)

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
  DIFY_BASE_URL             - API base URL (default: https://api.dify.ai/v1)
  MEMORY_DECAY_DAYS         - Default decay threshold in days (default: 30)
"""

import asyncio
import json
import re
import time
import os
import sys
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

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

# Session archive module (optional - graceful degradation if not present)
try:
    from session_store import (
        save_session,
        list_sessions,
        search_session,
        get_session_detail,
        delete_session,
    )

    SESSION_TOOLS_AVAILABLE = True
except ImportError:
    SESSION_TOOLS_AVAILABLE = False

# ─── Configuration ───────────────────────────────────────────────────────────
# No hardcoded fallback values — env vars MUST be set by install.py or .env
DIFY_API_KEY = os.environ.get("DIFY_API_KEY", "")
DIFY_BASE_URL = os.environ.get(
    "DIFY_BASE_URL", "https://api.dify.ai/v1"
)
DATASET_ID = os.environ.get("DIFY_DATASET_ID", "")
DOCUMENT_ID = os.environ.get("DIFY_DOCUMENT_ID", "")  # short-term (decayable)
PERMANENT_DOCUMENT_ID = os.environ.get(
    "DIFY_PERMANENT_DOCUMENT_ID", ""
)  # long-term (never decayed)
DECAY_THRESHOLD_DAYS = int(os.environ.get("MEMORY_DECAY_DAYS", "30"))


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
                "User-Agent": "dify-memory-mcp/1.6.1",
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
        "retrieval_model": {
            "search_method": "hybrid_search",
            "reranking_enable": False,
            "top_k": top_k,
            "score_threshold_enabled": False,
        },
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


@app.list_tools()
async def list_tools() -> list[Tool]:
    tools = [
        Tool(
            name="add_memory",
            description="Store a new memory (fact, decision, preference, or project knowledge). Use this to remember important information across sessions. Set permanent=true for critical information that must never be auto-deleted. Trigger: user says '记住这个/记下来/别忘了/永远记住', or agent discovers important info worth persisting.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The memory content to store",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional keywords/tags for categorization",
                    },
                    "permanent": {
                        "type": "boolean",
                        "description": "If true, store as long-term memory (never deleted by decay). Use for critical preferences, decisions, and facts.",
                        "default": False,
                    },
                },
                "required": ["content"],
            },
        ),
        Tool(
            name="search_memory",
            description="Search memories by semantic similarity. Use this at the start of a session or when you need to recall past context. Trigger: user asks '你还记得吗/之前说过什么/回忆一下', or when the task involves project-specific knowledge that may have been stored before.",
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
            name="update_memory",
            description="Update an existing memory by its segment_id. Use when existing memory is outdated or inaccurate — update rather than create duplicates.",
            inputSchema={
                "type": "object",
                "properties": {
                    "segment_id": {
                        "type": "string",
                        "description": "The segment ID of the memory to update",
                    },
                    "content": {
                        "type": "string",
                        "description": "New content for the memory",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional updated keywords",
                    },
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
            description="List stored memories with pagination. Use to browse all memories. Supports filtering by storage type (short_term/long_term/all) and keyword.",
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
                        "description": "Filter by keyword",
                        "default": "",
                    },
                    "storage": {
                        "type": "string",
                        "description": "Which storage to list: 'short_term', 'long_term', or 'all' (default: 'all')",
                        "default": "all",
                        "enum": ["short_term", "long_term", "all"],
                    },
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
                                "enum": ["opencode", "manual"],
                                "description": "Source: 'opencode' (auto-extract) or 'manual'. Default: opencode",
                                "default": "opencode",
                            },
                            "content": {
                                "type": "string",
                                "description": "Session content (required if source='manual')",
                            },
                            "title": {
                                "type": "string",
                                "description": "Override session title",
                            },
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
            result = await add_memory(
                content=arguments["content"],
                keywords=arguments.get("keywords"),
                permanent=arguments.get("permanent", False),
            )
            # Auto-sync: if current session is already archived, incrementally append new messages
            if SESSION_TOOLS_AVAILABLE:
                try:
                    sync_result = await save_session(source="opencode")
                    if (
                        sync_result.get("mode") == "incremental"
                        and sync_result.get("new_messages", 0) > 0
                    ):
                        result["session_synced"] = sync_result["new_messages"]
                except Exception:
                    pass  # Session sync failure should never block memory operations
        elif name == "search_memory":
            result = await search_memory(
                query=arguments["query"], top_k=arguments.get("top_k", 5)
            )
        elif name == "update_memory":
            result = await update_memory(
                segment_id=arguments["segment_id"],
                content=arguments["content"],
                keywords=arguments.get("keywords"),
            )
        elif name == "delete_memory":
            result = await delete_memory(segment_id=arguments["segment_id"])
        elif name == "list_memories":
            result = await list_memories(
                page=arguments.get("page", 1),
                limit=arguments.get("limit", 20),
                keyword=arguments.get("keyword", ""),
                storage=arguments.get("storage", "all"),
            )
        elif name == "decay_memories":
            result = await decay_memories(days=arguments.get("days"))
        elif name == "promote_memory":
            result = await promote_memory(segment_id=arguments["segment_id"])
        # Session Archive tools
        elif name == "save_session" and SESSION_TOOLS_AVAILABLE:
            content = arguments.get("content")
            if arguments.get("source", "opencode") == "manual" and content is not None:
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
                source=arguments.get("source", "opencode"),
                content=content,
                title=arguments.get("title"),
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
                await app.run(
                    read_stream, write_stream, app.create_initialization_options()
                )
        finally:
            # Gracefully close the shared HTTP client
            if _http_client and not _http_client.is_closed:
                await _http_client.aclose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
