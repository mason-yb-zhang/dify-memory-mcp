import asyncio
import json
from pathlib import Path

import memory_model
import openclaw_session
import privacy
from local_store import LocalStore


def test_privacy_redacts_credentials_and_national_ids():
    cleaned, findings = privacy.sanitize_memory_content(
        "token=abc123secret id=110101199001011234"
    )
    assert "[REDACTED]" in cleaned
    assert "110101199001011234" not in cleaned
    assert "credential_assignment" in findings
    assert "cn_national_id" in findings


def test_audit_round_trip_and_hash_conflict_detection():
    audit = memory_model.create_audit("alice", "codex", "host-a", "initial")
    packed = memory_model.pack_record("latest content", audit)
    record = memory_model.unpack_record(packed, {"id": "memory-1", "created_at": 0})
    assert record["content"] == "latest content"
    assert record["audit"]["created_by"] == "alice"
    assert record["content_hash"] == memory_model.content_hash("latest content", audit)


def test_local_store_cache_and_offline_queue(tmp_path):
    store = LocalStore(Path(tmp_path))
    store.cache_set("search", {"memories": []})
    assert store.cache_get("search") == {"memories": []}
    store.enqueue({"operation_id": "op-1", "type": "add", "payload": {}})
    assert store.queue_status()["pending"] == 1
    store.resolve("op-1")
    assert store.queue_status()["pending"] == 0


def test_openclaw_jsonl_extraction(tmp_path):
    root = tmp_path / ".openclaw" / "agents"
    session_dir = root / "main" / "sessions"
    session_dir.mkdir(parents=True)
    records = [
        {"role": "user", "timestamp": 1700000000, "content": "hello"},
        {"role": "assistant", "timestamp": 1700000001, "content": ["world"]},
    ]
    path = session_dir / "session-1.jsonl"
    path.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")
    sessions = openclaw_session.extract_openclaw_sessions(
        "session-1", root=root
    )
    assert sessions[0]["id"] == "session-1"
    assert sessions[0]["metadata"]["source_agent"] == "openclaw"
    assert len(sessions[0]["messages"]) == 2


def test_memory_update_requires_conflict_guard(monkeypatch):
    import memory_service

    class FakeClient:
        permanent_document_id = ""

        async def find_segment(self, segment_id):
            audit = memory_model.create_audit("alice", "codex", "host-a", "create")
            raw = memory_model.pack_record("old", audit)
            return {
                "segment": {"id": segment_id, "content": raw, "created_at": 0},
                "document_id": "doc-1",
                "permanent": False,
            }

    monkeypatch.setattr(memory_service, "CLIENT", FakeClient())
    result = asyncio.run(memory_service.update_memory("seg-1", "new"))
    assert result["error"].startswith("expected_content_hash")
