"""
Task 3 regression tests for server.py:
  - delete_memory returns error when segment not found in either document
  - get_session_detail tool schema includes optional document_id
  - README and SKILL do not reference setup_sessions.py
  - SKILL return table uses patched_tools and session_archive_configured (not opencode_patched)
"""

import asyncio
import importlib.util
import json
import os
import sys
import time
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TESTS_DIR = os.path.dirname(__file__)
_SCRIPTS_DIR = os.path.abspath(os.path.join(_TESTS_DIR, "..", "scripts"))
_SERVER_PY = os.path.join(_SCRIPTS_DIR, "server.py")
_README_MD = os.path.abspath(os.path.join(_TESTS_DIR, "..", "README.md"))
_SKILL_MD = os.path.abspath(os.path.join(_TESTS_DIR, "..", "SKILL.md"))


def _load_server_module():
    """Load server.py without running main(). Disable _check_config side-effects."""
    # Ensure env vars are non-empty so _check_config won't sys.exit at module level
    os.environ.setdefault("MIFY_API_KEY", "dataset-test")
    os.environ.setdefault("MIFY_DATASET_ID", "ds-test")
    os.environ.setdefault("MIFY_DOCUMENT_ID", "doc-test")
    spec = importlib.util.spec_from_file_location("server_mod", _SERVER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test 1: delete_memory returns error when segment not found
# ---------------------------------------------------------------------------


def test_delete_memory_returns_error_when_segment_not_found():
    """
    delete_memory must verify that the segment actually exists before deleting.
    If the segment is not found in either short-term or long-term document,
    it must return {"error": "Segment <id> not found"} rather than success.

    The DELETE API returns success=True even for non-existent segments (no-op),
    so a naive DELETE-and-return-success is wrong. The guard must LIST segments
    and confirm presence before treating DELETE as successful.
    """
    mod = _load_server_module()

    # Patch DOCUMENT_ID and PERMANENT_DOCUMENT_ID on the loaded module
    mod.DOCUMENT_ID = "doc-short"
    mod.PERMANENT_DOCUMENT_ID = "doc-perm"
    mod.DATASET_ID = "ds-test"

    # DELETE returns success, but GET /segments never includes our target segment
    async def fake_mify_request(method, path, body=None):
        if method == "GET" and "segments" in path:
            # Return empty data — segment not found
            return {"data": [], "total": 0}
        if method == "DELETE":
            # DELETE succeeds (this is the naive behaviour we want to guard against)
            return {"success": True}
        return {"success": True}

    async def run_test():
        with patch.object(mod, "mify_request", side_effect=fake_mify_request):
            result = await mod.delete_memory(segment_id="seg-missing")
        return result

    result = run(run_test())

    assert "error" in result, (
        f"Expected error when segment not found, got success: {result}"
    )
    assert "seg-missing" in result["error"], (
        f"Error message should mention the segment_id: {result}"
    )
    # Must NOT claim success
    assert result.get("success") is not True, (
        f"delete_memory must not claim success when segment was not found: {result}"
    )


def test_delete_memory_returns_lookup_error_when_segment_list_fails():
    """A segment listing failure must not be reported as segment-not-found."""
    mod = _load_server_module()
    mod.DOCUMENT_ID = "doc-short"
    mod.PERMANENT_DOCUMENT_ID = "doc-perm"
    mod.DATASET_ID = "ds-test"

    async def fake_mify_request(method, path, body=None):
        if method == "GET" and "segments" in path:
            return {"error": "HTTP 500: broken"}
        if method == "DELETE":
            return {"success": True}
        return {"success": True}

    async def run_test():
        with patch.object(mod, "mify_request", side_effect=fake_mify_request):
            return await mod.delete_memory(segment_id="seg-any")

    result = run(run_test())

    assert result.get("success") is not True, (
        f"delete_memory must not claim success when lookup fails: {result}"
    )
    assert "HTTP 500" in result.get("error", ""), (
        f"Expected underlying lookup error to be surfaced, got: {result}"
    )
    assert "not found" not in result.get("error", "").lower(), (
        f"Lookup failure should not be masked as not-found: {result}"
    )


# ---------------------------------------------------------------------------
# Test 2: get_session_detail tool schema includes optional document_id
# ---------------------------------------------------------------------------


def test_get_session_detail_schema_accepts_document_id():
    """
    list_tools() must expose get_session_detail with an optional 'document_id'
    property in its inputSchema.
    """
    mod = _load_server_module()

    # Force SESSION_TOOLS_AVAILABLE so session tools are registered
    mod.SESSION_TOOLS_AVAILABLE = True

    async def run_test():
        tools = await mod.list_tools()
        return tools

    tools = run(run_test())

    tool_names = {t.name: t for t in tools}
    assert "get_session_detail" in tool_names, (
        f"get_session_detail tool not found. Available tools: {list(tool_names)}"
    )

    schema = tool_names["get_session_detail"].inputSchema
    props = schema.get("properties", {})
    assert "document_id" in props, (
        f"get_session_detail inputSchema must include optional 'document_id' property. "
        f"Current properties: {list(props.keys())}"
    )
    # document_id should be optional (not in required list)
    required = schema.get("required", [])
    assert "document_id" not in required, (
        f"document_id must be optional (not in required), got required={required}"
    )


# ---------------------------------------------------------------------------
# Test 3: call_tool passes document_id to get_session_detail
# ---------------------------------------------------------------------------


def test_call_tool_passes_document_id_to_get_session_detail():
    """
    call_tool('get_session_detail', {segment_id, document_id}) must pass
    document_id through to get_session_detail().

    get_session_detail is imported from session_store at module load time,
    so we patch the name on the server module's namespace directly.
    """
    mod = _load_server_module()
    mod.SESSION_TOOLS_AVAILABLE = True

    received_kwargs = {}

    async def fake_get_session_detail(segment_id, document_id=None):
        received_kwargs["segment_id"] = segment_id
        received_kwargs["document_id"] = document_id
        return {"segment_id": segment_id, "document_id": document_id, "content": "ok"}

    # get_session_detail lives in mod's globals (imported at top of server.py)
    # If SESSION_TOOLS_AVAILABLE=True the function is bound in the module namespace.
    # Use create=True so the mock can create the attribute if session import was skipped.
    async def run_test():
        with patch.object(
            mod, "get_session_detail", side_effect=fake_get_session_detail, create=True
        ):
            result = await mod.call_tool(
                "get_session_detail",
                {"segment_id": "seg-abc", "document_id": "doc-xyz"},
            )
        return result

    run(run_test())

    assert received_kwargs.get("document_id") == "doc-xyz", (
        f"call_tool must pass document_id='doc-xyz' to get_session_detail, "
        f"got: {received_kwargs}"
    )


def test_prepare_memory_content_removes_private_blocks():
    """Private-tagged content must be removed before any memory is persisted."""
    mod = _load_server_module()

    assert (
        mod._prepare_memory_content("alpha <private>secret</private> beta")
        == "alpha beta"
    )
    assert (
        mod._prepare_memory_content(
            "a <private>one</private> b <private>two</private> c"
        )
        == "a b c"
    )
    assert mod._prepare_memory_content("visible <private>secret") == "visible"
    assert mod._prepare_memory_content("<private>secret</private>") is None


def test_add_memory_sanitizes_private_content_before_persisting():
    """add_memory must send only sanitized content to Mify."""
    mod = _load_server_module()
    calls = []

    async def fake_mify_request(method, path, body=None):
        calls.append((method, path, body))
        return {
            "data": [
                {
                    "id": "seg-1",
                    "content": body["segments"][0]["content"],
                    "status": "completed",
                }
            ]
        }

    async def run_test():
        with patch.object(mod, "mify_request", side_effect=fake_mify_request):
            return await mod.add_memory("alpha <private>secret</private> beta")

    result = run(run_test())

    assert result["content"] == "alpha beta"
    assert calls[0][2]["segments"][0]["content"] == "alpha beta"


def test_add_memory_skips_private_only_content_without_mify_call():
    """private-only content should not create a Mify segment."""
    mod = _load_server_module()

    async def fail_mify_request(method, path, body=None):
        raise AssertionError(
            "mify_request should not be called for private-only content"
        )

    async def run_test():
        with patch.object(mod, "mify_request", side_effect=fail_mify_request):
            return await mod.add_memory("<private>secret</private>")

    result = run(run_test())

    assert result["success"] is True
    assert result["skipped"] is True


# ---------------------------------------------------------------------------
# Test 4: Static docs check — README does not reference setup_sessions.py
# ---------------------------------------------------------------------------


def test_readme_does_not_reference_setup_sessions_py():
    """README.md must not reference 'setup_sessions.py' (file does not exist)."""
    with open(_README_MD, "r", encoding="utf-8") as fh:
        content = fh.read()
    assert "setup_sessions.py" not in content, (
        "README.md still references 'setup_sessions.py'. Remove or replace."
    )


def test_skill_does_not_reference_setup_sessions_py():
    """SKILL.md must not reference 'setup_sessions.py' (file does not exist)."""
    with open(_SKILL_MD, "r", encoding="utf-8") as fh:
        content = fh.read()
    assert "setup_sessions.py" not in content, (
        "SKILL.md still references 'setup_sessions.py'. Remove or replace."
    )


# ---------------------------------------------------------------------------
# Test 5: SKILL.md return table uses 'patched_tools', not 'opencode_patched'
# ---------------------------------------------------------------------------


def test_skill_return_table_uses_patched_tools_not_opencode_patched():
    """
    SKILL.md install.py return table must use 'patched_tools' (actual field name)
    and 'session_archive_configured', not the old 'opencode_patched' name.
    """
    with open(_SKILL_MD, "r", encoding="utf-8") as fh:
        content = fh.read()

    assert "patched_tools" in content, (
        "SKILL.md must document 'patched_tools' in the install.py return table. "
        "This is the actual field name in install.py output."
    )
    assert "opencode_patched" not in content, (
        "SKILL.md must not reference 'opencode_patched' — "
        "the actual field is 'patched_tools'. Update the return table."
    )


def test_idle_timeout_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MIFY_MEMORY_MCP_IDLE_TIMEOUT_SECONDS", raising=False)
    assert _load_server_module()._idle_timeout_seconds() == 0.0


@pytest.mark.parametrize("value", ["bad", "-1", "nan", "inf"])
def test_invalid_idle_timeout_disables_timer(monkeypatch, value):
    monkeypatch.setenv("MIFY_MEMORY_MCP_IDLE_TIMEOUT_SECONDS", value)
    assert _load_server_module()._idle_timeout_seconds() == 0.0


def test_explicit_idle_timeout_remains_supported(monkeypatch):
    monkeypatch.setenv("MIFY_MEMORY_MCP_IDLE_TIMEOUT_SECONDS", "45")
    assert _load_server_module()._idle_timeout_seconds() == 45.0


def test_disabled_idle_still_watches_parent():
    mod = _load_server_module()
    with patch.object(mod, "_parent_is_alive", side_effect=[True, True, False]), \
         patch.object(mod.time, "sleep") as sleep:
        assert mod._watch_lifecycle([0.0], 0.0, 123) == "parent_exited"
        assert sleep.call_count == 2


def test_lifecycle_watchdog_reports_idle_timeout():
    mod = _load_server_module()
    assert (
        mod._watch_lifecycle(
            [time.monotonic() - 1.0],
            0.05,
            os.getppid(),
        )
        == "idle_timeout"
    )


def test_lifecycle_watchdog_detects_dead_parent():
    mod = _load_server_module()
    assert mod._watch_lifecycle([time.monotonic()], 0.0, 99999999) == "parent_exited"


def test_activity_read_stream_marks_receive_activity():
    mod = _load_server_module()
    activity = []

    class FakeStream:
        async def receive(self):
            return "message"

        async def __anext__(self):
            return "message"

        async def aclose(self):
            return None

        def close(self):
            return None

        extra_attributes = {}
        statistics = {}

    stream = mod._ActivityReadStream(FakeStream(), lambda: activity.append(time.monotonic()))

    async def run_test():
        assert await stream.receive() == "message"
        assert await stream.__anext__() == "message"

    run(run_test())
    assert len(activity) == 2
