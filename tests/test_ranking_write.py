"""P0/P1/P2 ranking, cache TTL, write-style, fact_key, and temporal tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import memory_model
import memory_service
from local_store import LocalStore


def test_cache_ttl_expires(tmp_path):
    store = LocalStore(tmp_path)
    store.cache_set("k", {"v": 1}, ttl=0)
    # ttl=0 means immediately stale
    assert store.cache_get("k") is None
    store.cache_set("k2", {"v": 2}, ttl=3600)
    assert store.cache_get("k2") == {"v": 2}


def test_legacy_cache_entries_treated_as_miss(tmp_path):
    store = LocalStore(tmp_path)
    cache = {"legacy": {"memories": []}}
    store._write(store.cache_path, cache)
    assert store.cache_get("legacy") is None


def test_recency_boost_prefers_newer():
    fresh = memory_service._recency_boost(0.0)
    old = memory_service._recency_boost(21.0)
    assert fresh == 1.0
    assert abs(old - 0.5) < 1e-6


def test_write_warnings():
    warnings = memory_service._write_warnings("短")
    assert any(item.startswith("content_too_short") for item in warnings)
    warnings = memory_service._write_warnings("上周把端口改掉了没有任何绝对日期")
    assert any(item.startswith("relative_date_without_absolute") for item in warnings)
    assert memory_service._write_warnings("FreeCAD 1.1 RPC 端口是 9876（2026-09-24 确认）") == []


def test_temporal_boost_matches_year_month():
    boost = memory_service._temporal_boost(
        "2026-09 的部署记录", "2026-09-12T00:00:00+00:00", datetime(2026, 9, 24, tzinfo=timezone.utc)
    )
    assert boost == 1.0
    assert memory_service._temporal_boost(
        "2026-01 的记录", "2026-09-12T00:00:00+00:00", datetime(2026, 9, 24, tzinfo=timezone.utc)
    ) == 0.0


def test_expired_memory_hidden_from_search(monkeypatch):
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    expired = now - timedelta(days=1)
    future = now + timedelta(days=30)

    class FakeClient:
        permanent_document_id = "doc-p"
        dataset_id = "ds-1"

        async def request(self, method, path, body=None, retryable=True):
            def seg(sid, content, exp):
                audit = memory_model.create_audit(
                    "alice", "codex", "host", "t",
                    event_date="2026-09-01",
                    expiration_date=exp,
                )
                return {
                    "id": sid,
                    "content": memory_model.pack_record(content, audit),
                    "created_at": 0,
                    "keywords": [],
                    "hit_count": 0,
                    "enabled": True,
                }

            return {
                "records": [
                    {
                        "segment": seg("old", "旧内容已经过期需要隐藏", expired.isoformat()),
                        "document_id": "doc-s",
                        "score": 0.9,
                    },
                    {
                        "segment": seg("new", "新内容仍然有效可以检索", future.isoformat()),
                        "document_id": "doc-s",
                        "score": 0.8,
                    },
                ]
            }

        def missing_config(self):
            return []

        def document_ids(self):
            return [("doc-s", False)]

        class _LS:
            def cache_get(self, key):
                return None

            def cache_set(self, key, value, ttl=None):
                return None

        local_store = _LS()

    monkeypatch.setattr(memory_service, "CLIENT", FakeClient())
    result = asyncio.run(
        memory_service.search_memory("内容", top_k=5, reference_date=now.isoformat(), explain=True)
    )
    ids = [item["memory_id"] for item in result["memories"]]
    assert ids == ["new"]
    assert "score_details" in result["memories"][0]
    shown = asyncio.run(
        memory_service.search_memory(
            "内容", top_k=5, reference_date=now.isoformat(), show_expired=True
        )
    )
    assert set(item["memory_id"] for item in shown["memories"]) == {"old", "new"}


def test_fact_key_upsert_updates_instead_of_add(monkeypatch):
    calls = {"add": 0, "update": 0, "find": 0}

    class FakeClient:
        permanent_document_id = ""
        dataset_id = "ds-1"
        short_document_id = "doc-s"

        def missing_config(self):
            return []

        def document_ids(self):
            return [("doc-s", False)]

        class _LS:
            def cache_get(self, key):
                return None

            def cache_set(self, key, value, ttl=None):
                return None

            def enqueue(self, op):
                return None

        local_store = _LS()

        async def find_operation(self, document_id, operation_id):
            return None

        async def find_segment(self, segment_id):
            audit = memory_model.create_audit("alice", "codex", "host", "create")
            raw = memory_model.pack_record("旧事实内容足够长可以过校验", audit)
            return {
                "segment": {
                    "id": segment_id,
                    "content": raw,
                    "created_at": 0,
                    "keywords": ["fact:cad-tool"],
                },
                "document_id": "doc-s",
                "permanent": False,
            }

        async def request(self, method, path, body=None, retryable=True):
            if method == "GET":
                calls["find"] += 1
                audit = memory_model.create_audit("alice", "codex", "host", "create")
                return {
                    "data": [
                        {
                            "id": "seg-1",
                            "content": memory_model.pack_record("旧事实内容足够长可以过校验", audit),
                            "created_at": 0,
                            "keywords": ["fact:cad-tool"],
                        }
                    ]
                }
            calls["update"] += 1
            audit = memory_model.update_audit(memory_model.create_audit("alice", "codex", "host", "create"))
            return {
                "data": [
                    {
                        "id": "seg-1",
                        "content": memory_model.pack_record("新事实内容足够长可以过校验", audit),
                        "created_at": 0,
                        "keywords": ["fact:cad-tool"],
                    }
                ]
            }

    monkeypatch.setattr(memory_service, "CLIENT", FakeClient())
    result = asyncio.run(
        memory_service.add_memory(
            "新事实内容足够长可以过校验",
            keywords=["fact:cad-tool"],
            fact_key="fact:cad-tool",
        )
    )
    assert result.get("upserted") is True
    assert result.get("fact_key") == "fact:cad-tool"
    assert calls["add"] == 0
    assert calls["update"] == 1


def test_search_sort_updated_at(monkeypatch):
    class FakeClient:
        permanent_document_id = "doc-p"
        dataset_id = "ds-1"

        def missing_config(self):
            return []

        def document_ids(self):
            return [("doc-s", False)]

        class _LS:
            def cache_get(self, key):
                return None

            def cache_set(self, key, value, ttl=None):
                return None

        local_store = _LS()

        async def request(self, method, path, body=None, retryable=True):
            def seg(sid, content, modified):
                audit = memory_model.create_audit("alice", "codex", "host", "t")
                audit["last_modified_at"] = modified
                return {
                    "id": sid,
                    "content": memory_model.pack_record(content, audit),
                    "created_at": 0,
                    "keywords": [],
                    "hit_count": 0,
                    "enabled": True,
                }

            return {
                "records": [
                    {
                        "segment": seg("older", "较旧内容AAAA", "2026-01-01T00:00:00+00:00"),
                        "document_id": "doc-s",
                        "score": 0.99,
                    },
                    {
                        "segment": seg("newer", "较新内容BBBB", "2026-09-20T00:00:00+00:00"),
                        "document_id": "doc-s",
                        "score": 0.10,
                    },
                ]
            }

    monkeypatch.setattr(memory_service, "CLIENT", FakeClient())
    by_time = asyncio.run(
        memory_service.search_memory("内容", top_k=5, sort="updated_at", reference_date="2026-09-24")
    )
    assert [item["memory_id"] for item in by_time["memories"]] == ["newer", "older"]
    by_rel = asyncio.run(
        memory_service.search_memory("内容", top_k=5, prefer="current", reference_date="2026-09-24")
    )
    assert by_rel["memories"][0]["memory_id"] == "newer"
