"""
Session Store Module for mify-memory-mcp

Provides full-session archiving with a two-tier architecture:
  - Tier 1 (Index): Lightweight summaries in dedicated session-indexes Document (Memory Dataset)
  - Tier 2 (Full): Complete conversation as independent Documents (Sessions Dataset)

Supports incremental append: subsequent saves only upload new messages.
Each message is annotated with source tool + timestamp at message level.

Data sources supported:
  - Configured: Resolve OpenCode or OpenClaw through MCPorter configuration
  - OpenCode: SQLite session database
  - OpenClaw: JSONL session files
  - Manual: Direct text input

Tools:
  - save_session: Archive session (first=full, subsequent=incremental append)
  - list_sessions: Browse session index from Memory Dataset
  - search_session: Full-text search across archived session content
  - get_session_detail: Read a specific segment by ID
"""

import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import httpx
from openclaw_session import extract_openclaw_sessions
import runtime_config

# ─── Configuration ───────────────────────────────────────────────────────────
SESSIONS_DATASET_ID = os.environ.get("MIFY_SESSIONS_DATASET_ID", "")
MIFY_API_KEY = os.environ.get("MIFY_API_KEY", "")
MIFY_BASE_URL = runtime_config.dify_base_url()

# Session index document (dedicated doc in Memory Dataset for index entries)
MEMORY_DATASET_ID = os.environ.get("MIFY_DATASET_ID", "")
SESSION_INDEX_DOCUMENT_ID = os.environ.get("MIFY_SESSION_INDEX_DOCUMENT_ID", "")

def _opencode_db_path() -> Path | None:
    return runtime_config.opencode_db_path()


def _get_headers():
    return {
        "Authorization": f"Bearer {MIFY_API_KEY}",
        "Content-Type": "application/json",
    }


# Shared HTTP client (lazy init, reused across requests within same event loop)
_http_client: httpx.AsyncClient | None = None
_http_client_loop_id: int | None = None


def _get_client() -> httpx.AsyncClient:
    """Get or create shared HTTP client. Recreates if event loop changed."""
    global _http_client, _http_client_loop_id
    import asyncio
    try:
        current_loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        current_loop_id = None

    if _http_client is None or _http_client.is_closed or _http_client_loop_id != current_loop_id:
        _http_client = httpx.AsyncClient(timeout=60.0, headers=_get_headers())
        _http_client_loop_id = current_loop_id
    return _http_client


async def close_http_client() -> None:
    """Close the shared HTTP client during MCP shutdown."""
    global _http_client, _http_client_loop_id
    client = _http_client
    _http_client = None
    _http_client_loop_id = None
    if client is not None and not client.is_closed:
        await client.aclose()


async def _mify_request(method: str, path: str, body: dict | None = None) -> dict:
    """Make HTTP request to Mify API using shared client."""
    url = f"{MIFY_BASE_URL}{path}"
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
        else:
            raise ValueError(f"Unsupported method: {method}")

        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}: {resp.text}"}
        if not resp.text or resp.text.strip() == "":
            return {"success": True}
        # Guard against non-JSON responses (e.g. DELETE returning "204\n")
        try:
            result = resp.json()
            if not isinstance(result, dict):
                return {"success": True, "raw": result}
            return result
        except (ValueError, TypeError):
            return {"success": True, "raw": resp.text}
    except httpx.HTTPError as e:
        return {"error": f"HTTP error: {str(e)}"}


# ─── OpenCode Session Extraction ─────────────────────────────────────────────

def _extract_opencode_sessions(session_id: str | None = None, last_n: int = 1) -> list[dict]:
    """Extract session(s) from OpenCode SQLite database.
    
    Returns list of dicts with: id, title, time_created, model, messages[]
    Each message has: role, parts[] (type + text/tool info)
    """
    db_path = _opencode_db_path()
    if db_path is None or not db_path.exists():
        return []

    # Open read-only to avoid lock conflicts with running OpenCode
    db_uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True)
    cur = conn.cursor()

    if session_id:
        cur.execute("SELECT id, title, time_created, model, tokens_input, tokens_output FROM session WHERE id = ?", (session_id,))
    else:
        cur.execute("SELECT id, title, time_created, model, tokens_input, tokens_output FROM session ORDER BY time_created DESC LIMIT ?", (last_n,))

    sessions = []
    for row in cur.fetchall():
        sid, title, ts, model_json, tok_in, tok_out = row
        model_info = json.loads(model_json) if model_json else {}

        # Get messages
        cur.execute("SELECT id, data, time_created FROM message WHERE session_id = ? ORDER BY time_created ASC", (sid,))
        messages = []
        for msg_row in cur.fetchall():
            msg_id, msg_data_raw, msg_ts = msg_row
            msg_data = json.loads(msg_data_raw) if msg_data_raw else {}
            role = msg_data.get("role", "unknown")

            # Get parts for this message
            cur.execute("SELECT data FROM part WHERE message_id = ? ORDER BY time_created ASC", (msg_id,))
            parts = []
            for part_row in cur.fetchall():
                part_data = json.loads(part_row[0]) if part_row[0] else {}
                ptype = part_data.get("type", "unknown")

                if ptype == "text":
                    text = part_data.get("text", "")
                    if text.strip():
                        parts.append({"type": "text", "text": text})
                elif ptype == "tool-invocation":
                    tool = part_data.get("toolInvocation", {})
                    tool_name = tool.get("toolName", "")
                    tool_state = tool.get("state", "")
                    # Only include completed tool results summary, not full output
                    tool_args = tool.get("args", {})
                    parts.append({
                        "type": "tool",
                        "name": tool_name,
                        "state": tool_state,
                        "args_summary": _summarize_tool_args(tool_name, tool_args),
                    })
                # Skip step-start, step-finish, reasoning etc. to keep it lean

            if parts:
                messages.append({
                    "role": role,
                    "time": msg_ts,
                    "parts": parts,
                })

        sessions.append({
            "id": sid,
            "title": title or "Untitled",
            "time_created": ts,
            "model": model_info,
            "tokens_input": tok_in,
            "tokens_output": tok_out,
            "messages": messages,
        })

    conn.close()
    return sessions


def _summarize_tool_args(tool_name: str, args: dict) -> str:
    """Create a brief summary of tool arguments."""
    if not args:
        return ""
    if tool_name in ("bash", "Shell"):
        return args.get("command", "")[:200]
    elif tool_name in ("read", "Read"):
        return args.get("filePath", "")
    elif tool_name in ("edit", "Edit"):
        return f"edit {args.get('filePath', '')}"
    elif tool_name in ("write", "Write"):
        return f"write {args.get('filePath', '')}"
    elif tool_name in ("glob", "Glob"):
        return args.get("pattern", "")
    elif tool_name in ("grep", "Grep"):
        return f"grep {args.get('pattern', '')} {args.get('include', '')}"
    elif tool_name.startswith("memory_"):
        return args.get("query", args.get("content", ""))[:100]
    else:
        # Generic: show first few key=value pairs
        items = []
        for k, v in list(args.items())[:3]:
            v_str = str(v)[:50]
            items.append(f"{k}={v_str}")
        return ", ".join(items)


def _ts_to_str(ts: int) -> str:
    """Convert timestamp (ms or s) to readable string."""
    if not ts:
        return "unknown"
    # OpenCode uses millisecond timestamps (13 digits)
    if ts > 1e12:
        ts = ts / 1000
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _ts_to_seconds(ts: int) -> int:
    """Normalize timestamp to seconds."""
    if ts > 1e12:
        return int(ts / 1000)
    return ts


def _format_session_markdown(session: dict, source: str = "opencode") -> str:
    """Format a session as markdown for full storage.
    
    Each message is annotated with source tool and timestamp at message level.
    """
    lines = []
    title = session.get("title", "Untitled")
    ts = session.get("time_created", 0)
    dt = _ts_to_str(ts)
    model = session.get("model", {})
    model_id = model.get("id", model.get("modelID", "unknown"))
    metadata = session.get("metadata", {})

    lines.append(f"# {title}")
    lines.append(f"")
    lines.append(f"- **Date**: {dt}")
    lines.append(f"- **Source**: {source}")
    lines.append(f"- **Created By**: {metadata.get('created_by', 'unknown')}")
    lines.append(f"- **Agent**: {metadata.get('source_agent', source)}")
    lines.append(f"- **Device**: {metadata.get('source_device', 'unknown')}")
    lines.append(f"- **Model**: {model_id}")
    lines.append(f"- **Session ID**: {session['id']}")
    lines.append(f"- **Tokens**: in={session.get('tokens_input', 0)} out={session.get('tokens_output', 0)}")
    lines.append(f"")
    lines.append("---")
    lines.append("")

    for msg in session.get("messages", []):
        role = msg["role"]
        msg_time = _ts_to_str(msg.get("time", 0))
        role_label = "User" if role == "user" else "Assistant"

        # Message-level annotation: [source | tool | timestamp]
        lines.append(f"## {role_label}")
        lines.append(f"<!-- source={source} time={msg_time} -->")
        lines.append("")

        for part in msg.get("parts", []):
            if part["type"] == "text":
                lines.append(part["text"])
                lines.append("")
            elif part["type"] == "tool":
                name = part.get("name", "?")
                summary = part.get("args_summary", "")
                lines.append(f"🔧 `{name}`: {summary}")
                lines.append("")

    return "\n".join(lines)


def _format_session_index(session: dict, document_id: str | None, source: str = "opencode", message_count: int = 0) -> str:
    """Format a lightweight index entry stored in session-indexes document."""
    title = session.get("title", "Untitled")
    ts = session.get("time_created", 0)
    dt = _ts_to_str(ts)
    model = session.get("model", {})
    model_id = model.get("id", model.get("modelID", "unknown"))
    last_updated = datetime.now().strftime("%Y-%m-%d %H:%M")
    metadata = session.get("metadata", {})

    # Extract key topics from conversation
    topics = _extract_topics(session)

    lines = []
    lines.append(f"# {title}")
    lines.append(f"")
    lines.append(f"- **Date**: {dt}")
    lines.append(f"- **Last Updated**: {last_updated}")
    lines.append(f"- **Source**: {source}")
    lines.append(f"- **Created By**: {metadata.get('created_by', 'unknown')}")
    lines.append(f"- **Agent**: {metadata.get('source_agent', source)}")
    lines.append(f"- **Device**: {metadata.get('source_device', 'unknown')}")
    lines.append(f"- **Model**: {model_id}")
    lines.append(f"- **Session ID**: {session['id']}")
    lines.append(f"- **Document ID**: {document_id or 'pending'}")
    lines.append(f"- **Messages**: {message_count}")
    lines.append(f"- **Tokens**: in={session.get('tokens_input', 0)} out={session.get('tokens_output', 0)}")
    lines.append(f"")
    lines.append("## Topics")
    lines.append("")
    for topic in topics:
        lines.append(f"- {topic}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(_generate_summary(session))
    lines.append("")

    return "\n".join(lines)


def _extract_topics(session: dict) -> list[str]:
    """Extract key topics from session messages."""
    topics = set()
    for msg in session.get("messages", []):
        if msg["role"] == "user":
            for part in msg.get("parts", []):
                if part["type"] == "text":
                    # Simple keyword extraction from user messages
                    text = part["text"]
                    topics.add(text[:80].strip())
    return list(topics)[:10]


def _generate_summary(session: dict) -> str:
    """Generate a brief summary of the session."""
    user_msgs = []
    tool_names = set()
    files_touched = set()

    for msg in session.get("messages", []):
        for part in msg.get("parts", []):
            if part["type"] == "text" and msg["role"] == "user":
                user_msgs.append(part["text"][:100])
            elif part["type"] == "tool":
                tool_names.add(part.get("name", ""))
                summary = part.get("args_summary", "")
                # Extract file paths from tool usage
                if "/" in summary or "\\" in summary:
                    # Rough file path extraction
                    for token in summary.split():
                        if ("/" in token or "\\" in token) and len(token) > 3:
                            files_touched.add(token[:80])

    parts = []
    if user_msgs:
        parts.append(f"User asked: {'; '.join(user_msgs[:3])}")
    if tool_names:
        parts.append(f"Tools used: {', '.join(sorted(tool_names)[:10])}")
    if files_touched:
        parts.append(f"Files: {', '.join(sorted(files_touched)[:5])}")

    return " | ".join(parts) if parts else "No summary available"


# ─── Session Upload ──────────────────────────────────────────────────────────

async def _create_session_document(title: str, date_str: str, initial_text: str) -> dict:
    """Create a new Document in Sessions Dataset for a session.
    
    Returns: {"document_id": "...", "error": None} or {"document_id": None, "error": "..."}
    """
    if not SESSIONS_DATASET_ID:
        return {"document_id": None, "error": "MIFY_SESSIONS_DATASET_ID not configured"}

    path = f"/datasets/{SESSIONS_DATASET_ID}/document/create-by-text"
    doc_name = f"{date_str}_{title}"[:80]

    body = {
        "name": doc_name,
        "text": initial_text,
        "indexing_technique": "high_quality",
        "doc_form": "text_model",
        "doc_language": "Chinese",
        "process_rule": {"mode": "automatic"},
    }

    result = await _mify_request("POST", path, body)

    if "document" in result:
        return {"document_id": result["document"]["id"], "error": None}
    elif "error" in result:
        return {"document_id": None, "error": result["error"]}
    else:
        return {"document_id": None, "error": f"Unexpected response: {result}"}


async def _append_segments_to_document(document_id: str, segments_text: list[str], source: str) -> list[str]:
    """Append new message segments to an existing session Document.
    
    Each segment is a message or group of messages with source annotation.
    Returns list of created segment_ids.
    """
    if not SESSIONS_DATASET_ID or not document_id:
        return []

    path = f"/datasets/{SESSIONS_DATASET_ID}/documents/{document_id}/segments"
    segment_ids = []

    # Batch segments (API accepts multiple in one call)
    batch = []
    for text in segments_text:
        batch.append({
            "content": text,
            "keywords": [source, "session-message"],
        })
        # Mify batch limit ~uncertain, send in groups of 10
        if len(batch) >= 10:
            result = await _mify_request("POST", path, {"segments": batch})
            if "data" in result:
                segment_ids.extend(seg["id"] for seg in result["data"])
            batch = []

    if batch:
        result = await _mify_request("POST", path, {"segments": batch})
        if "data" in result:
            segment_ids.extend(seg["id"] for seg in result["data"])

    return segment_ids


# ─── Index Storage (in Memory Dataset) ───────────────────────────────────────

async def _save_index_to_memory(index_content: str, title: str, source: str, session_id: str) -> str | None:
    """Save session index to the dedicated session-indexes document.
    
    Stored in a separate document from memories, but same dataset.
    All tools sharing this MCP can list/browse via list_sessions.
    Returns the created segment_id, or None on failure.
    """
    if not MEMORY_DATASET_ID or not SESSION_INDEX_DOCUMENT_ID:
        return None

    path = f"/datasets/{MEMORY_DATASET_ID}/documents/{SESSION_INDEX_DOCUMENT_ID}/segments"
    body = {
        "segments": [{
            "content": index_content,
            "keywords": ["session-index", source, title[:30]],
        }]
    }
    result = await _mify_request("POST", path, body)
    if "data" in result and result["data"]:
        return result["data"][0]["id"]
    return None


# ─── Public Tool Functions ───────────────────────────────────────────────────

async def _find_existing_index(session_id: str) -> dict | None:
    """Find existing index entry for a session by its session_id."""
    if not MEMORY_DATASET_ID or not SESSION_INDEX_DOCUMENT_ID:
        return None

    path = f"/datasets/{MEMORY_DATASET_ID}/documents/{SESSION_INDEX_DOCUMENT_ID}/segments"
    url = f"{MIFY_BASE_URL}{path}"
    client = _get_client()

    page = 1
    while True:
        resp = await client.get(url, params={"page": page, "limit": 100})
        if resp.status_code >= 400:
            return None
        result = resp.json()

        for seg in result.get("data", []):
            content = seg.get("content", "")
            if f"**Session ID**: {session_id}" in content:
                # Parse document_id and message_count from content
                doc_id = None
                msg_count = 0
                for line in content.split("\n"):
                    if "**Document ID**" in line:
                        doc_id = line.split("**: ", 1)[-1].strip()
                    elif "**Messages**" in line:
                        try:
                            msg_count = int(line.split("**: ", 1)[-1].strip())
                        except ValueError:
                            pass
                return {
                    "segment_id": seg["id"],
                    "document_id": doc_id,
                    "message_count": msg_count,
                }

        if len(result.get("data", [])) < 100:
            break
        page += 1

    return None


async def _update_index(segment_id: str, index_content: str) -> bool:
    """Update an existing index segment with new content."""
    if not MEMORY_DATASET_ID or not SESSION_INDEX_DOCUMENT_ID:
        return False

    path = f"/datasets/{MEMORY_DATASET_ID}/documents/{SESSION_INDEX_DOCUMENT_ID}/segments/{segment_id}"
    body = {"segment": {"content": index_content, "enabled": True}}
    result = await _mify_request("POST", path, body)
    return "data" in result


MAX_SEGMENT_SIZE = 12000


def _format_messages_as_segments(messages: list[dict], source: str) -> list[str]:
    """Format messages into segment-sized text blocks with source annotations.
    
    Handles oversized single messages by splitting them at line boundaries.
    """
    segments = []
    current = ""

    for msg in messages:
        role = msg["role"]
        msg_time = _ts_to_str(msg.get("time", 0))
        role_label = "User" if role == "user" else "Assistant"

        block = f"## {role_label}\n<!-- source={source} time={msg_time} -->\n\n"
        for part in msg.get("parts", []):
            if part["type"] == "text":
                block += part["text"] + "\n\n"
            elif part["type"] == "tool":
                name = part.get("name", "?")
                summary = part.get("args_summary", "")
                block += f"🔧 `{name}`: {summary}\n\n"

        # If single block exceeds limit, split it
        if len(block) > MAX_SEGMENT_SIZE:
            if current:
                segments.append(current)
                current = ""
            # Split oversized block at line boundaries
            lines = block.split("\n")
            chunk = ""
            for line in lines:
                if len(chunk) + len(line) + 1 > MAX_SEGMENT_SIZE:
                    if chunk:
                        segments.append(chunk)
                    chunk = line + "\n"
                else:
                    chunk += line + "\n"
            if chunk:
                current = chunk
        elif len(current) + len(block) > MAX_SEGMENT_SIZE:
            if current:
                segments.append(current)
            current = block
        else:
            current += block

    if current:
        segments.append(current)

    return segments


async def save_session(
    session_id: str | None = None,
    source: str = "configured",
    content: str | None = None,
    title: str | None = None,
    created_by: str | None = None,
    source_agent: str | None = None,
    source_device: str | None = None,
) -> dict:
    """Archive a full conversation session. Supports incremental append.
    
    - First call: creates Document + index
    - Subsequent calls (same session): appends only new messages
    
    Each message is annotated with source tool and timestamp.
    
    Args:
        session_id: OpenCode session ID to archive (omit for most recent session)
        source: "configured", "opencode", "openclaw", or "manual".
        "configured" resolves the active source from MCPorter configuration.
        content: Session content (required if source='manual')
        title: Override session title
    """
    source = (source or "configured").strip().lower()
    if source in {"configured", "auto"}:
        source = runtime_config.session_source()
        if source not in {"opencode", "openclaw"}:
            return {
                "success": True,
                "skipped": True,
                "reason": "No configured session source. Set sessionSources.default in MCPorter config.",
            }
    if source == "opencode":
        sessions = _extract_opencode_sessions(session_id=session_id, last_n=1)
        if not sessions:
            return {"error": "No session found. Check session_id or sessionSources.opencode.databasePath."}
        session = sessions[0]
    elif source == "openclaw":
        sessions = extract_openclaw_sessions(session_id=session_id, last_n=1)
        for session in sessions:
            session.setdefault("metadata", {}).update(
                {
                    key: value
                    for key, value in {
                        "created_by": created_by,
                        "source_agent": source_agent or "openclaw",
                        "source_device": source_device,
                    }.items()
                    if value
                }
            )
        if not sessions:
            return {"error": "No OpenClaw session found. Set sessionSources.openclaw.sessionsRoot or pass session_id."}
        session = sessions[0]
    elif source == "manual":
        if not content:
            return {"error": "content is required for manual source"}
        session = {
            "id": f"manual_{int(time.time())}",
            "title": title or "Manual Session",
            "time_created": int(time.time()),
            "model": {"id": "manual"},
            "tokens_input": 0,
            "tokens_output": 0,
            "messages": [{"role": "user", "time": int(time.time()), "parts": [{"type": "text", "text": content}]}],
        }
    else:
        return {"error": "Unsupported source. Use 'configured', 'opencode', 'openclaw', or 'manual'."}

    if title:
        session["title"] = title

    all_messages = session.get("messages", [])
    total_msg_count = len(all_messages)

    # Check if this session was saved before (incremental mode)
    existing = await _find_existing_index(session["id"])

    if existing and existing.get("document_id") and existing["document_id"] != "pending":
        saved_count = existing["message_count"]
        document_id = existing["document_id"]

        if total_msg_count < saved_count:
            # ─── Session rollback detected: user went back and restarted ───
            # Create new document FIRST, then delete old (copy-before-delete safety)
            session_md = _format_session_markdown(session, source=source)
            ts = session.get("time_created", int(time.time()))
            date_str = datetime.fromtimestamp(_ts_to_seconds(ts)).strftime("%Y-%m-%d")
            upload_result = await _create_session_document(session["title"], date_str, session_md)
            new_doc_id = upload_result.get("document_id")

            if not new_doc_id:
                # Create failed — leave old document untouched
                return {
                    "success": False,
                    "session_id": session["id"],
                    "title": session["title"],
                    "mode": "rebuild",
                    "source": source,
                    "upload_error": upload_result.get("error", "unknown"),
                }

            # New document created successfully — now delete the old one
            del_path = f"/datasets/{SESSIONS_DATASET_ID}/documents/{document_id}"
            delete_result = await _mify_request("DELETE", del_path)
            old_document_deleted = delete_result.get("success") is True

            # Update index with new document_id
            index_content = _format_session_index(session, new_doc_id, source=source, message_count=total_msg_count)
            await _update_index(existing["segment_id"], index_content)

            result = {
                "success": True,
                "session_id": session["id"],
                "title": session["title"],
                "mode": "rebuild",
                "source": source,
                "reason": f"Session rolled back ({saved_count} → {total_msg_count} messages). Document rebuilt.",
                "document_id": new_doc_id,
                "old_document_deleted": old_document_deleted,
                "total_messages": total_msg_count,
                "content_size": len(session_md),
            }
            if not old_document_deleted:
                result["warning"] = (
                    f"New session document created, but old document {document_id} could not be deleted: "
                    f"{delete_result.get('error', 'unknown error')}"
                )
            return result

        elif total_msg_count == saved_count:
            # ─── No change ───
            return {
                "success": True,
                "session_id": session["id"],
                "title": session["title"],
                "mode": "incremental",
                "new_messages": 0,
                "total_messages": total_msg_count,
                "note": "No new messages since last save.",
            }

        else:
            # ─── Incremental append: only new messages ───
            new_messages = all_messages[saved_count:]
            new_segments = _format_messages_as_segments(new_messages, source=source)
            appended = await _append_segments_to_document(document_id, new_segments, source)

            # Update index with new message count and timestamp
            index_content = _format_session_index(session, document_id, source=source, message_count=total_msg_count)
            await _update_index(existing["segment_id"], index_content)

            return {
                "success": True,
                "session_id": session["id"],
                "title": session["title"],
                "mode": "incremental",
                "source": source,
                "document_id": document_id,
                "new_messages": len(new_messages),
                "total_messages": total_msg_count,
                "appended_segments": len(appended),
            }

    else:
        # ─── First save: create Document + full upload ───
        session_md = _format_session_markdown(session, source=source)
        ts = session.get("time_created", int(time.time()))
        date_str = datetime.fromtimestamp(_ts_to_seconds(ts)).strftime("%Y-%m-%d")

        upload_result = await _create_session_document(session["title"], date_str, session_md)
        document_id = upload_result.get("document_id")

        if not document_id:
            # Upload failed — report failure immediately, do not write index
            return {
                "success": False,
                "session_id": session["id"],
                "title": session["title"],
                "mode": "full",
                "source": source,
                "upload_error": upload_result.get("error", "unknown"),
            }

        # Upload succeeded — write/update index
        index_content = _format_session_index(session, document_id, source=source, message_count=total_msg_count)

        if existing:
            # Had a pending index, update it
            await _update_index(existing["segment_id"], index_content)
            index_segment_id = existing["segment_id"]
        else:
            index_segment_id = await _save_index_to_memory(index_content, session["title"], source, session["id"])

        return {
            "success": True,
            "session_id": session["id"],
            "title": session["title"],
            "mode": "full",
            "source": source,
            "index_segment_id": index_segment_id,
            "document_id": document_id,
            "total_messages": total_msg_count,
            "content_size": len(session_md),
        }


async def list_sessions(limit: int = 20, keyword: str = "") -> dict:
    """Browse archived session index.
    
    Session indexes are stored in a dedicated document (session-indexes)
    within the memory dataset. All tools sharing this MCP can access them.
    """
    if not MEMORY_DATASET_ID or not SESSION_INDEX_DOCUMENT_ID:
        return {"error": "Session index document not configured. Set MIFY_SESSION_INDEX_DOCUMENT_ID."}

    # Read all segments from the dedicated session-indexes document
    path = f"/datasets/{MEMORY_DATASET_ID}/documents/{SESSION_INDEX_DOCUMENT_ID}/segments"
    url = f"{MIFY_BASE_URL}{path}"
    client = _get_client()

    all_index_segments = []
    page = 1
    while True:
        resp = await client.get(url, params={"page": page, "limit": 100})
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}: {resp.text}"}
        result = resp.json()

        for seg in result.get("data", []):
            all_index_segments.append(seg)

        if len(result.get("data", [])) < 100:
            break
        page += 1

    # Apply keyword filter if provided
    if keyword:
        filtered = []
        for seg in all_index_segments:
            if keyword.lower() in seg.get("content", "").lower():
                filtered.append(seg)
        all_index_segments = filtered

    # Sort by created_at descending (most recent first)
    all_index_segments.sort(key=lambda s: s.get("created_at", 0), reverse=True)

    # Format output
    entries = []
    for seg in all_index_segments[:limit]:
        content = seg.get("content", "")
        entry = {"segment_id": seg.get("id", "")}

        # Parse structured fields from index content
        for line in content.split("\n"):
            if line.startswith("# "):
                entry["title"] = line[2:].strip()
            elif "**Date**" in line:
                entry["date"] = line.split("**: ", 1)[-1].strip()
            elif "**Source**" in line:
                entry["source"] = line.split("**: ", 1)[-1].strip()
            elif "**Session ID**" in line:
                entry["session_id"] = line.split("**: ", 1)[-1].strip()
            elif "**Document ID**" in line:
                entry["document_id"] = line.split("**: ", 1)[-1].strip()
            elif line.startswith("## Summary"):
                idx = content.index(line)
                remaining = content[idx + len(line):].strip()
                summary_line = remaining.split("\n")[0].strip()
                entry["summary"] = summary_line[:200]
                break
        entries.append(entry)

    return {
        "sessions": entries,
        "total": len(all_index_segments),
        "showing": len(entries),
    }


async def search_session(query: str, top_k: int = 5) -> dict:
    """Deep semantic search across FULL archived session content in Mify KB.
    
    Uses full-text search (keyword_search) for exact matching of conversation content.
    Each session is an independent document — results include document_id for detail retrieval.
    """
    if not SESSIONS_DATASET_ID:
        return {"error": "MIFY_SESSIONS_DATASET_ID not configured. Re-run install.py --api-key <KEY> to enable Session Archive."}

    path = f"/datasets/{SESSIONS_DATASET_ID}/retrieve"
    body = {
        "query": query,
        "retrieval_model": {
            "search_method": "full_text_search",
            "reranking_enable": False,
            "top_k": top_k,
            "score_threshold_enabled": False,
        },
    }
    result = await _mify_request("POST", path, body)
    if "records" in result:
        results = []
        for record in result["records"]:
            seg = record.get("segment", {})
            content = seg.get("content", "")
            doc_name = record.get("document", {}).get("name", "")
            # Extract title from content or document name
            title_match = re.search(r"^# (.+)$", content, re.MULTILINE)
            title = title_match.group(1) if title_match else doc_name or "Unknown"
            results.append({
                "segment_id": seg.get("id", ""),
                "document_id": seg.get("document_id", ""),
                "document_name": doc_name,
                "title": title,
                "score": record.get("score", 0),
                "content_preview": content[:500],
            })
        return {"results": results, "count": len(results)}
    return result


async def get_session_detail(segment_id: str, document_id: str | None = None) -> dict:
    """Read the full content of a specific archived session segment by its Mify segment ID.

    Use segment_ids from search_session results. Returns the full text of that segment.

    Args:
        segment_id: The Mify segment ID to retrieve.
        document_id: Optional. When provided, scans only that document's segments
            (fast direct path). When omitted, falls back to enumerating all documents
            in the Sessions Dataset (with a safety cap of 100 documents).
    """
    if not SESSIONS_DATASET_ID:
        return {"error": "Sessions not configured."}

    client = _get_client()

    async def _scan_document(doc_id: str, doc_name: str) -> dict | None:
        """Scan segments of a single document for segment_id. Returns match or None."""
        seg_url = f"{MIFY_BASE_URL}/datasets/{SESSIONS_DATASET_ID}/documents/{doc_id}/segments"
        seg_page = 1
        while True:
            seg_resp = await client.get(seg_url, params={"page": seg_page, "limit": 100})
            if seg_resp.status_code >= 400:
                break
            seg_result = seg_resp.json()

            for seg in seg_result.get("data", []):
                if seg.get("id") == segment_id:
                    return {
                        "segment_id": segment_id,
                        "document_id": doc_id,
                        "document_name": doc_name,
                        "content": seg.get("content", ""),
                        "keywords": seg.get("keywords", []),
                        "created_at": seg.get("created_at", 0),
                    }

            if len(seg_result.get("data", [])) < 100:
                break
            seg_page += 1
        return None

    if document_id:
        # Fast direct path: caller already knows which document to look in
        match = await _scan_document(document_id, "")
        if match:
            return match
        return {"error": f"Segment {segment_id} not found in document {document_id}"}

    # Fallback: enumerate documents (paginated, safety cap = 100 documents)
    docs_path = f"/datasets/{SESSIONS_DATASET_ID}/documents"
    url = f"{MIFY_BASE_URL}{docs_path}"

    docs_page = 1
    docs_scanned = 0
    while docs_scanned < 100:
        resp = await client.get(url, params={"page": docs_page, "limit": 100})
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}: {resp.text}"}
        docs_result = resp.json()

        for doc in docs_result.get("data", []):
            doc_id = doc.get("id", "")
            doc_name = doc.get("name", "")
            match = await _scan_document(doc_id, doc_name)
            if match:
                return match
            docs_scanned += 1
            if docs_scanned >= 100:
                break

        if len(docs_result.get("data", [])) < 100:
            break
        docs_page += 1

    return {"error": f"Segment {segment_id} not found in sessions archive"}


async def delete_session(session_id: str) -> dict:
    """Delete an archived session completely (Document + index entry).
    
    Removes the full session Document from Sessions Dataset
    and the corresponding index segment from session-indexes.
    
    Args:
        session_id: The session_id to delete (from list_sessions output)
    """
    if not SESSIONS_DATASET_ID:
        return {"error": "Sessions not configured."}

    # Find the index entry to get document_id
    existing = await _find_existing_index(session_id)
    if not existing:
        return {"error": f"Session {session_id} not found in index."}

    document_id = existing.get("document_id")
    index_segment_id = existing.get("segment_id")

    results = {"session_id": session_id, "deleted_document": False, "deleted_index": False}

    # Delete the full session Document from Sessions Dataset
    doc_required = bool(document_id and document_id != "pending")
    if doc_required:
        path = f"/datasets/{SESSIONS_DATASET_ID}/documents/{document_id}"
        result = await _mify_request("DELETE", path)
        results["deleted_document"] = result.get("success") is True
        if "error" in result:
            results["document_error"] = result["error"]

    # Delete the index segment from session-indexes
    index_required = bool(index_segment_id and MEMORY_DATASET_ID and SESSION_INDEX_DOCUMENT_ID)
    if index_required:
        path = f"/datasets/{MEMORY_DATASET_ID}/documents/{SESSION_INDEX_DOCUMENT_ID}/segments/{index_segment_id}"
        result = await _mify_request("DELETE", path)
        results["deleted_index"] = result.get("success") is True
        if "error" in result:
            results["index_error"] = result["error"]

    # Overall success: every required delete must have succeeded
    doc_ok = results["deleted_document"] if doc_required else True
    index_ok = results["deleted_index"] if index_required else True
    results["success"] = doc_ok and index_ok
    return results
