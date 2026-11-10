"""
Task 2 regression tests for session_store.py:
  - save_session returns success:False when initial upload fails
  - save_session rollback creates new doc before deleting old
  - delete_session requires BOTH document and index deletes to succeed
  - get_session_detail accepts optional document_id for direct path
"""

import asyncio
import importlib.util
import os
import sys
from unittest.mock import AsyncMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Import session_store.py as a module
# ---------------------------------------------------------------------------

_SESSION_STORE_PY = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "scripts", "session_store.py")
)


def _load_session_store():
    """Load session_store.py as a module."""
    spec = importlib.util.spec_from_file_location("session_store_mod", _SESSION_STORE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(coro):
    """Helper to run a coroutine in tests without pytest-asyncio."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test 1: save_session returns failure when initial upload fails
# ---------------------------------------------------------------------------

def test_save_session_returns_failure_when_initial_upload_fails():
    """
    When _create_session_document returns document_id=None (upload failed),
    save_session must return success=False and include upload_error.
    """
    mod = _load_session_store()

    fake_session = {
        "id": "sess-001",
        "title": "Test Session",
        "time_created": 1700000000,
        "model": {"id": "test-model"},
        "tokens_input": 10,
        "tokens_output": 20,
        "messages": [
            {"role": "user", "time": 1700000000, "parts": [{"type": "text", "text": "hello"}]}
        ],
    }

    save_index = AsyncMock(return_value="idx-seg-1")
    update_index = AsyncMock(return_value=True)

    async def run_test():
        with patch.object(mod, "_extract_opencode_sessions", return_value=[fake_session]), \
             patch.object(mod, "_find_existing_index", new=AsyncMock(return_value=None)), \
             patch.object(mod, "_create_session_document", new=AsyncMock(
                 return_value={"document_id": None, "error": "boom"}
             )), \
             patch.object(mod, "_save_index_to_memory", new=save_index), \
             patch.object(mod, "_update_index", new=update_index):

            result = await mod.save_session(session_id="sess-001", source="opencode")

        assert result.get("success") is False, (
            f"Expected success=False when upload fails, got: {result}"
        )
        assert "upload_error" in result, (
            f"Expected upload_error in result, got: {result}"
        )
        assert result["upload_error"] == "boom"
        save_index.assert_not_called()
        update_index.assert_not_called()

    run(run_test())


# ---------------------------------------------------------------------------
# Test 2: save_session rollback creates new doc before deleting old
# ---------------------------------------------------------------------------

def test_save_session_rollback_creates_new_before_deleting_old():
    """
    When existing message_count > current message_count (rollback detected),
    _create_session_document must be called BEFORE the DELETE of the old document.
    """
    mod = _load_session_store()

    fake_session = {
        "id": "sess-002",
        "title": "Rollback Session",
        "time_created": 1700000000,
        "model": {"id": "test-model"},
        "tokens_input": 10,
        "tokens_output": 20,
        "messages": [
            {"role": "user", "time": 1700000000, "parts": [{"type": "text", "text": "hi"}]}
        ],
    }

    # existing has more messages than current (5 > 1)
    fake_existing = {
        "segment_id": "seg-old",
        "document_id": "doc-old-id",
        "message_count": 5,
    }

    call_order = []

    async def fake_mify_request(method, path, body=None):
        call_order.append((method, path))
        if method == "DELETE":
            return {"success": True}
        return {"success": True}

    async def fake_create_doc(title, date_str, text):
        call_order.append(("CREATE_DOC", title))
        return {"document_id": "doc-new-id", "error": None}

    async def run_test():
        with patch.object(mod, "_extract_opencode_sessions", return_value=[fake_session]), \
             patch.object(mod, "_find_existing_index", new=AsyncMock(return_value=fake_existing)), \
             patch.object(mod, "_create_session_document", side_effect=fake_create_doc), \
             patch.object(mod, "_update_index", new=AsyncMock(return_value=True)), \
             patch.object(mod, "_mify_request", side_effect=fake_mify_request):

            result = await mod.save_session(session_id="sess-002", source="opencode")

        return result

    result = run(run_test())

    assert result.get("mode") == "rebuild", f"Expected mode=rebuild, got: {result}"
    assert result.get("success") is True, f"Expected success=True, got: {result}"

    # Verify CREATE_DOC happened before DELETE
    create_pos = next(
        (i for i, c in enumerate(call_order) if c[0] == "CREATE_DOC"),
        None
    )
    delete_pos = next(
        (i for i, c in enumerate(call_order) if c[0] == "DELETE"),
        None
    )

    assert create_pos is not None, f"CREATE_DOC not found in call order: {call_order}"
    assert delete_pos is not None, f"DELETE not found in call order: {call_order}"
    assert create_pos < delete_pos, (
        f"Expected CREATE_DOC (pos={create_pos}) before DELETE (pos={delete_pos}). "
        f"Full call order: {call_order}"
    )
    assert result.get("old_document_deleted") is True, (
        f"Expected old_document_deleted=True when cleanup succeeds, got: {result}"
    )


def test_save_session_rollback_reports_old_document_delete_failure():
    """
    If replacement creation succeeds but old document deletion fails, save_session
    should report success for the rebuild and include cleanup status/warning.
    """
    mod = _load_session_store()

    fake_session = {
        "id": "sess-delete-fail",
        "title": "Rollback Cleanup Failure",
        "time_created": 1700000000,
        "model": {"id": "test-model"},
        "tokens_input": 10,
        "tokens_output": 20,
        "messages": [
            {"role": "user", "time": 1700000000, "parts": [{"type": "text", "text": "hi"}]}
        ],
    }
    fake_existing = {
        "segment_id": "seg-old",
        "document_id": "doc-old-id",
        "message_count": 5,
    }

    async def fake_mify_request(method, path, body=None):
        if method == "DELETE" and "documents/doc-old-id" in path:
            return {"error": "delete failed"}
        return {"success": True}

    async def run_test():
        with patch.object(mod, "_extract_opencode_sessions", return_value=[fake_session]), \
             patch.object(mod, "_find_existing_index", new=AsyncMock(return_value=fake_existing)), \
             patch.object(mod, "_create_session_document", new=AsyncMock(
                 return_value={"document_id": "doc-new-id", "error": None}
             )), \
             patch.object(mod, "_update_index", new=AsyncMock(return_value=True)), \
             patch.object(mod, "_mify_request", side_effect=fake_mify_request):
            return await mod.save_session(session_id="sess-delete-fail", source="opencode")

    result = run(run_test())

    assert result.get("success") is True, f"Expected rebuild success, got: {result}"
    assert result.get("document_id") == "doc-new-id"
    assert result.get("old_document_deleted") is False, (
        f"Expected old_document_deleted=False when cleanup fails, got: {result}"
    )
    assert "warning" in result and "delete failed" in result["warning"], (
        f"Expected warning with cleanup failure details, got: {result}"
    )


def test_save_session_rollback_create_failure_preserves_old_document():
    """
    If rollback rebuild cannot create the replacement document, save_session must
    return success=False and must not delete the existing archived document.
    """
    mod = _load_session_store()

    fake_session = {
        "id": "sess-rollback-fail",
        "title": "Rollback Failure Session",
        "time_created": 1700000000,
        "model": {"id": "test-model"},
        "tokens_input": 10,
        "tokens_output": 20,
        "messages": [
            {"role": "user", "time": 1700000000, "parts": [{"type": "text", "text": "hi"}]}
        ],
    }
    fake_existing = {
        "segment_id": "seg-old",
        "document_id": "doc-old-id",
        "message_count": 5,
    }
    mify_request = AsyncMock(return_value={"success": True})
    update_index = AsyncMock(return_value=True)

    async def run_test():
        with patch.object(mod, "_extract_opencode_sessions", return_value=[fake_session]), \
             patch.object(mod, "_find_existing_index", new=AsyncMock(return_value=fake_existing)), \
             patch.object(mod, "_create_session_document", new=AsyncMock(
                 return_value={"document_id": None, "error": "create failed"}
             )), \
             patch.object(mod, "_update_index", new=update_index), \
             patch.object(mod, "_mify_request", new=mify_request):
            return await mod.save_session(session_id="sess-rollback-fail", source="opencode")

    result = run(run_test())

    assert result.get("success") is False, f"Expected success=False, got: {result}"
    assert result.get("mode") == "rebuild", f"Expected mode=rebuild, got: {result}"
    assert result.get("upload_error") == "create failed"
    mify_request.assert_not_called()
    update_index.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: delete_session requires both document and index delete success
# ---------------------------------------------------------------------------

def test_delete_session_requires_document_and_index_delete_success():
    """
    delete_session must return success=False when index delete fails,
    even if document delete succeeded. deleted_document=True, deleted_index=False.
    """
    mod = _load_session_store()

    fake_existing = {
        "segment_id": "seg-idx-1",
        "document_id": "doc-abc",
        "message_count": 3,
    }

    call_count = {"n": 0}

    async def fake_mify_request(method, path, body=None):
        call_count["n"] += 1
        if "documents/doc-abc" in path and method == "DELETE":
            # Document delete succeeds
            return {"success": True}
        elif "segments/seg-idx-1" in path and method == "DELETE":
            # Index delete fails with error
            return {"error": "index delete failed"}
        return {"success": True}

    async def run_test():
        with patch.object(mod, "_find_existing_index", new=AsyncMock(return_value=fake_existing)), \
             patch.object(mod, "_mify_request", side_effect=fake_mify_request):

            # Patch SESSIONS_DATASET_ID and MEMORY_DATASET_ID to non-empty values
            original_sessions = mod.SESSIONS_DATASET_ID
            original_memory = mod.MEMORY_DATASET_ID
            original_index = mod.SESSION_INDEX_DOCUMENT_ID
            mod.SESSIONS_DATASET_ID = "sessions-ds"
            mod.MEMORY_DATASET_ID = "memory-ds"
            mod.SESSION_INDEX_DOCUMENT_ID = "idx-doc"

            try:
                result = await mod.delete_session("sess-003")
            finally:
                mod.SESSIONS_DATASET_ID = original_sessions
                mod.MEMORY_DATASET_ID = original_memory
                mod.SESSION_INDEX_DOCUMENT_ID = original_index

        return result

    result = run(run_test())

    assert result.get("deleted_document") is True, (
        f"Expected deleted_document=True, got: {result}"
    )
    assert result.get("deleted_index") is False, (
        f"Expected deleted_index=False when index delete errors, got: {result}"
    )
    assert result.get("success") is False, (
        f"Expected overall success=False when index delete fails, got: {result}"
    )


# ---------------------------------------------------------------------------
# Test 4: get_session_detail uses document_id direct path
# ---------------------------------------------------------------------------

def test_get_session_detail_uses_document_id_direct_path():
    """
    When document_id is provided, get_session_detail must NOT list all documents.
    It should scan only the specified document's segments.
    """
    mod = _load_session_store()

    # Confirm the function accepts document_id parameter
    import inspect
    sig = inspect.signature(mod.get_session_detail)
    assert "document_id" in sig.parameters, (
        "get_session_detail must accept optional document_id parameter. "
        f"Current signature: {sig}"
    )

    fake_segment = {
        "id": "seg-target",
        "content": "This is the session content.",
        "keywords": ["opencode"],
        "created_at": 1700000000,
    }

    docs_call_count = {"n": 0}

    class FakeResponse:
        def __init__(self, data):
            self._data = data
            self.status_code = 200

        def json(self):
            return self._data

    async def run_test():
        original_sessions = mod.SESSIONS_DATASET_ID
        mod.SESSIONS_DATASET_ID = "sessions-ds"

        try:
            # Mock the HTTP client directly
            fake_client = AsyncMock()

            async def fake_get(url, params=None):
                if "documents" in url and "segments" not in url:
                    # This is a document list call — should NOT be called when document_id given
                    docs_call_count["n"] += 1
                    return FakeResponse({"data": []})
                elif "segments" in url:
                    # Direct segment listing for the given doc
                    return FakeResponse({"data": [fake_segment]})
                return FakeResponse({"data": []})

            fake_client.get = fake_get

            with patch.object(mod, "_get_client", return_value=fake_client):
                result = await mod.get_session_detail(
                    segment_id="seg-target",
                    document_id="doc-direct"
                )
        finally:
            mod.SESSIONS_DATASET_ID = original_sessions

        return result, docs_call_count["n"]

    result, doc_list_calls = run(run_test())

    assert result.get("segment_id") == "seg-target", (
        f"Expected segment_id=seg-target in result, got: {result}"
    )
    assert result.get("content") == "This is the session content.", (
        f"Expected content in result, got: {result}"
    )
    assert doc_list_calls == 0, (
        f"Expected 0 document list calls when document_id is provided, "
        f"got {doc_list_calls}. The function should use the direct document path."
    )
