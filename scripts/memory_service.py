"""Memory CRUD, audit, filtering, retries, cache, and offline queue."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

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
from privacy import sanitize_memory_content

RECENCY_HALF_LIFE_DAYS = 21.0
RECENCY_WEIGHT = 0.35
RECENCY_WEIGHT_CURRENT = 2.0
TEMPORAL_WEIGHT = 0.25
RELATIVE_DATE_RE = re.compile(r"今天|昨天|前天|上周|本周|上个月|这个月|刚才|目前|现在|当前|最近")
ABSOLUTE_DATE_RE = re.compile(r"\d{4}[-/年.]\d{1,2}")
TIME_PHRASE_RE = re.compile(
    r"(\d{4})[-/年.](\d{1,2})|当前|现在|目前|最新|上周|本周|上个月|这个月|去年|今年"
)


class DifyClient:
    def __init__(self) -> None:
        self.base_url = dify_base_url()
        self.api_key = ""
        self.dataset_id = ""
        self.short_document_id = ""
        self.permanent_document_id = ""
        self.session_index_document_id = ""
        self.sessions_dataset_id = ""
        self.decay_days = 30
        self._client: httpx.AsyncClient | None = None
        self.local_store = LocalStore()

    def configure_from_env(self) -> None:
        import os

        self.local_store = LocalStore()
        self.base_url = os.environ.get("DIFY_BASE_URL", self.base_url)
        self.api_key = os.environ.get("DIFY_API_KEY", "")
        self.dataset_id = os.environ.get("DIFY_DATASET_ID", "")
        self.short_document_id = os.environ.get("DIFY_DOCUMENT_ID", "")
        self.permanent_document_id = os.environ.get("DIFY_PERMANENT_DOCUMENT_ID", "")
        self.session_index_document_id = os.environ.get(
            "DIFY_SESSION_INDEX_DOCUMENT_ID", ""
        )
        self.sessions_dataset_id = os.environ.get("DIFY_SESSIONS_DATASET_ID", "")
        self.decay_days = int(os.environ.get("MEMORY_DECAY_DAYS", "30"))

    def missing_config(self) -> list[str]:
        values = {
            "DIFY_API_KEY": self.api_key,
            "DIFY_DATASET_ID": self.dataset_id,
            "DIFY_DOCUMENT_ID": self.short_document_id,
        }
        return [key for key, value in values.items() if not value]

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        retryable: bool = True,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        attempts = 3 if retryable else 1
        last_error = ""
        for attempt in range(attempts):
            started = time.monotonic()
            try:
                client = self._get_client()
                if method == "GET":
                    response = await client.get(url, params=body)
                elif method == "POST":
                    response = await client.post(url, json=body)
                elif method == "PATCH":
                    response = await client.patch(url, json=body)
                elif method == "DELETE":
                    response = await client.delete(url)
                    if response.status_code == 204:
                        metric_increment("dify_requests_success")
                        return {"success": True}
                else:
                    raise ValueError(f"Unsupported method: {method}")

                log_event(
                    "dify_request",
                    method=method,
                    status=response.status_code,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = f"HTTP {response.status_code}: {response.text}"
                    metric_increment("dify_requests_retried")
                    await asyncio.sleep(min(2**attempt, 4))
                    continue
                metric_increment("dify_requests_success")
                if response.status_code >= 400:
                    return {"error": f"HTTP {response.status_code}: {response.text}"}
                if not response.text:
                    return {"success": True}
                parsed = response.json()
                return parsed if isinstance(parsed, dict) else {"success": True}
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = str(exc)
                metric_increment("dify_requests_retried")
                log_event(
                    "dify_transport_retry",
                    method=method,
                    attempt=attempt + 1,
                    error_type=type(exc).__name__,
                )
                await asyncio.sleep(min(2**attempt, 4))
        metric_increment("dify_requests_failed")
        return {"error": last_error or "Dify request failed"}

    def document_ids(self) -> list[tuple[str, bool]]:
        result = []
        if self.short_document_id:
            result.append((self.short_document_id, False))
        if self.permanent_document_id:
            result.append((self.permanent_document_id, True))
        return result

    async def find_segment(self, segment_id: str) -> dict[str, Any]:
        for document_id, permanent in self.document_ids():
            page = 1
            while page <= 50:
                result = await self.request(
                    "GET",
                    f"/datasets/{self.dataset_id}/documents/{document_id}/segments",
                    {"page": page, "limit": 100},
                )
                if "error" in result:
                    return result
                for segment in result.get("data", []):
                    if segment.get("id") == segment_id:
                        return {
                            "segment": segment,
                            "document_id": document_id,
                            "permanent": permanent,
                        }
                data = result.get("data", [])
                if not data or len(data) < 100:
                    break
                page += 1
        return {"error": f"Segment {segment_id} not found"}

    async def find_operation(self, document_id: str, operation_id: str) -> dict[str, Any] | None:
        page = 1
        keyword = f"op:{operation_id}"
        while page <= 50:
            result = await self.request(
                "GET",
                f"/datasets/{self.dataset_id}/documents/{document_id}/segments",
                {"page": page, "limit": 100, "keyword": keyword},
            )
            if "error" in result:
                return None
            for segment in result.get("data", []):
                if keyword in (segment.get("keywords") or []):
                    return segment
            data = result.get("data", [])
            if not data or len(data) < 100:
                break
            page += 1
        return None


CLIENT = DifyClient()


def _record(segment: dict[str, Any], permanent: bool) -> dict[str, Any]:
    raw = segment.get("content", "")
    record = unpack_record(raw, segment)
    record.update(
        {
            "permanent": permanent,
            "keywords": segment.get("keywords", []),
            "hit_count": segment.get("hit_count", 0),
            "enabled": segment.get("enabled", True),
        }
    )
    return record


def _filters_match(
    record: dict[str, Any],
    created_by: str = "",
    source_agent: str = "",
    source_device: str = "",
    permanent: bool | None = None,
    keyword: str = "",
) -> bool:
    audit = record.get("audit", {})
    if created_by and audit.get("created_by") != created_by:
        return False
    if source_agent and audit.get("source_agent") != source_agent:
        return False
    if source_device and audit.get("source_device") != source_device:
        return False
    if permanent is not None and record.get("permanent") is not permanent:
        return False
    if keyword:
        haystack = " ".join([record.get("content", ""), *(record.get("keywords") or [])])
        if keyword.lower() not in haystack.lower():
            return False
    return True


def _iso_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _age_days(last_modified_at: str, reference: datetime | None = None) -> float | None:
    modified = _iso_to_dt(last_modified_at)
    if modified is None:
        return None
    now = reference or datetime.now(timezone.utc)
    return max(0.0, (now - modified).total_seconds() / 86400.0)


def _recency_boost(age_days: float | None) -> float:
    if age_days is None:
        return 0.0
    return 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)


def _is_expired(record: dict[str, Any], reference: datetime | None = None) -> bool:
    expiration = _iso_to_dt(record.get("expiration_date") or "")
    if expiration is None:
        return False
    now = reference or datetime.now(timezone.utc)
    return now >= expiration


def _temporal_boost(query: str, event_date: str, reference: datetime | None = None) -> float:
    if not event_date:
        return 0.0
    event = _iso_to_dt(event_date)
    if event is None:
        return 0.0
    match = TIME_PHRASE_RE.search(query or "")
    if not match:
        return 0.0
    if match.group(1) and match.group(2):
        try:
            if event.year == int(match.group(1)) and event.month == int(match.group(2)):
                return 1.0
        except ValueError:
            return 0.0
        return 0.0
    phrase = match.group(0)
    now = reference or datetime.now(timezone.utc)
    if phrase in {"当前", "现在", "目前", "最新"}:
        age = _age_days(event_date, now)
        return 0.8 if age is not None and age <= 30 else 0.3 if age is not None and age <= 90 else 0.0
    if phrase in {"上周", "本周", "上个月", "这个月"}:
        age = _age_days(event_date, now)
        return 0.7 if age is not None and age <= 45 else 0.0
    if phrase in {"今年", "去年"}:
        target_year = now.year if phrase == "今年" else now.year - 1
        return 1.0 if event.year == target_year else 0.0
    return 0.0


def _write_warnings(content: str) -> list[str]:
    warnings: list[str] = []
    text = content.strip()
    if len(text) < 15:
        warnings.append("content_too_short: prefer 15-80 self-contained words with proper nouns")
    if RELATIVE_DATE_RE.search(text) and not ABSOLUTE_DATE_RE.search(text):
        warnings.append(
            "relative_date_without_absolute: ground relative time to an absolute date (YYYY-MM-DD)"
        )
    return warnings


def _fact_keys(keywords: list[str] | None) -> list[str]:
    return [item for item in (keywords or []) if item.startswith("fact:")]


async def _find_by_fact_key(fact_key: str) -> dict[str, Any] | None:
    """Locate a memory tagged with fact_key.

    Dify's segments keyword filter is unreliable for tags like fact:slug, so
    scan segment keywords client-side across a bounded number of pages.
    """
    slug = fact_key.split(":", 1)[-1] if ":" in fact_key else fact_key
    for document_id, permanent in CLIENT.document_ids():
        page = 1
        while page <= 30:
            result = await CLIENT.request(
                "GET",
                f"/datasets/{CLIENT.dataset_id}/documents/{document_id}/segments",
                {"page": page, "limit": 100},
            )
            if "error" in result:
                break
            data = result.get("data", [])
            for segment in data:
                keywords = segment.get("keywords") or []
                if fact_key in keywords or any(
                    item == fact_key or item.endswith(":" + slug) and item.startswith("fact:")
                    for item in keywords
                ):
                    return {
                        "segment": segment,
                        "document_id": document_id,
                        "permanent": permanent,
                    }
            if not data or len(data) < 100:
                break
            page += 1
    return None


async def add_memory(
    content: str,
    keywords: list[str] | None = None,
    permanent: bool = False,
    created_by: str | None = None,
    source_agent: str | None = None,
    source_device: str | None = None,
    change_summary: str | None = None,
    idempotency_key: str | None = None,
    offline_retry: bool = False,
    event_date: str | None = None,
    expiration_date: str | None = None,
    fact_key: str | None = None,
) -> dict[str, Any]:
    cleaned, findings = sanitize_memory_content(content or "")
    if cleaned is None:
        return {"success": True, "skipped": True, "privacy_findings": findings}
    warnings = _write_warnings(cleaned)
    if permanent and not CLIENT.permanent_document_id:
        return {"error": "Long-term memory is not configured"}

    stored_keywords = list(dict.fromkeys((keywords or [])[:6]))
    resolved_fact_key = fact_key or (_fact_keys(stored_keywords)[0] if _fact_keys(stored_keywords) else "")
    if resolved_fact_key and not offline_retry:
        existing_lookup = await _find_by_fact_key(resolved_fact_key)
        if existing_lookup:
            existing_record = _record(existing_lookup["segment"], existing_lookup["permanent"])
            if resolved_fact_key not in stored_keywords:
                stored_keywords.append(resolved_fact_key)
            updated = await update_memory(
                segment_id=existing_record["memory_id"],
                content=cleaned,
                allow_unsafe_overwrite=True,
                keywords=stored_keywords,
                updated_by=created_by,
                source_agent=source_agent,
                source_device=source_device,
                change_summary=change_summary or f"upsert via {resolved_fact_key}",
                offline_retry=False,
                event_date=event_date,
                expiration_date=expiration_date,
            )
            if "error" not in updated:
                updated["upserted"] = True
                updated["fact_key"] = resolved_fact_key
                updated["warnings"] = warnings
                updated["privacy_findings"] = findings
                return updated

    document_id = CLIENT.permanent_document_id if permanent else CLIENT.short_document_id
    operation_id = idempotency_key or str(uuid.uuid4())
    existing = await CLIENT.find_operation(document_id, operation_id)
    if existing:
        record = _record(existing, permanent)
        record["idempotent_replay"] = True
        record["warnings"] = warnings
        return record

    audit = create_audit(
        created_by,
        source_agent,
        source_device,
        change_summary,
        event_date=event_date,
        expiration_date=expiration_date,
    )
    packed = pack_record(cleaned, audit)
    tag = f"op:{operation_id}"
    keywords_with_tags = stored_keywords + [tag]
    if resolved_fact_key and resolved_fact_key not in keywords_with_tags:
        keywords_with_tags.append(resolved_fact_key)
    result = await CLIENT.request(
        "POST",
        f"/datasets/{CLIENT.dataset_id}/documents/{document_id}/segments",
        {"segments": [{"content": packed, "keywords": keywords_with_tags[:8]}]},
        retryable=False,
    )
    if "data" in result and result["data"]:
        record = _record(result["data"][0], permanent)
        record["operation_id"] = operation_id
        record["warnings"] = warnings
        metric_increment("memory_add_success")
        return record

    existing = await CLIENT.find_operation(document_id, operation_id)
    if existing:
        record = _record(existing, permanent)
        record["idempotent_replay"] = True
        record["warnings"] = warnings
        return record

    if not offline_retry:
        CLIENT.local_store.enqueue(
            {
                "operation_id": operation_id,
                "type": "add",
                "payload": {
                    "content": content,
                    "keywords": keywords,
                    "permanent": permanent,
                    "created_by": created_by,
                    "source_agent": source_agent,
                    "source_device": source_device,
                    "change_summary": change_summary,
                    "idempotency_key": operation_id,
                    "offline_retry": True,
                    "event_date": event_date,
                    "expiration_date": expiration_date,
                    "fact_key": fact_key,
                },
            }
        )
        metric_increment("memory_write_queued")
        return {
            "success": False,
            "queued": True,
            "operation_id": operation_id,
            "error": result.get("error", "Dify write failed"),
            "privacy_findings": findings,
            "warnings": warnings,
        }
    return {**result, "privacy_findings": findings, "warnings": warnings}


async def update_memory(
    segment_id: str,
    content: str,
    expected_content_hash: str | None = None,
    allow_unsafe_overwrite: bool = False,
    keywords: list[str] | None = None,
    updated_by: str | None = None,
    source_agent: str | None = None,
    source_device: str | None = None,
    change_summary: str | None = None,
    offline_retry: bool = False,
    event_date: str | None = None,
    expiration_date: str | None = None,
) -> dict[str, Any]:
    current_lookup = await CLIENT.find_segment(segment_id)
    if "error" in current_lookup:
        return current_lookup
    current_segment = current_lookup["segment"]
    current = _record(current_segment, current_lookup["permanent"])
    if not allow_unsafe_overwrite:
        if not expected_content_hash:
            return {
                "error": "expected_content_hash is required unless allow_unsafe_overwrite=true",
                "current_content_hash": current["content_hash"],
            }
        if expected_content_hash != current["content_hash"]:
            return {
                "error": "Memory changed since it was read",
                "expected_content_hash": expected_content_hash,
                "current_content_hash": current["content_hash"],
            }

    cleaned, findings = sanitize_memory_content(content or "")
    if cleaned is None:
        return {"success": True, "skipped": True, "privacy_findings": findings}
    warnings = _write_warnings(cleaned)

    audit = update_audit(
        current["audit"],
        updated_by,
        source_agent,
        source_device,
        change_summary,
        event_date=event_date,
        expiration_date=expiration_date,
    )
    packed = pack_record(cleaned, audit)
    updated_keywords = keywords if keywords is not None else current.get("keywords", [])
    operation_id = str(uuid.uuid4())
    result = await CLIENT.request(
        "POST",
        f"/datasets/{CLIENT.dataset_id}/documents/{current_lookup['document_id']}/segments/{segment_id}",
        {"segment": {"content": packed, "keywords": updated_keywords, "enabled": True}},
        retryable=False,
    )
    if "data" in result and result["data"]:
        payload = result["data"]
        segment = payload[0] if isinstance(payload, list) else payload
        record = _record(segment, current_lookup["permanent"])
        record["warnings"] = warnings
        metric_increment("memory_update_success")
        return record

    if not offline_retry:
        CLIENT.local_store.enqueue(
            {
                "operation_id": operation_id,
                "type": "update",
                "payload": {
                    "segment_id": segment_id,
                    "content": content,
                    "expected_content_hash": current["content_hash"],
                    "allow_unsafe_overwrite": False,
                    "keywords": keywords,
                    "updated_by": updated_by,
                    "source_agent": source_agent,
                    "source_device": source_device,
                    "change_summary": change_summary,
                    "offline_retry": True,
                    "event_date": event_date,
                    "expiration_date": expiration_date,
                },
            }
        )
        metric_increment("memory_write_queued")
        return {
            "success": False,
            "queued": True,
            "operation_id": operation_id,
            "error": result.get("error", "Dify update failed"),
            "privacy_findings": findings,
            "warnings": warnings,
        }
    return {**result, "privacy_findings": findings, "warnings": warnings}


async def get_memory(segment_id: str) -> dict[str, Any]:
    lookup = await CLIENT.find_segment(segment_id)
    if "error" in lookup:
        return lookup
    return _record(lookup["segment"], lookup["permanent"])


async def search_memory(
    query: str,
    top_k: int = 5,
    created_by: str = "",
    source_agent: str = "",
    source_device: str = "",
    permanent: bool | None = None,
    keyword: str = "",
    use_cache: bool = False,
    sort: str = "relevance",
    prefer: str = "",
    explain: bool = False,
    reference_date: str | None = None,
    show_expired: bool = False,
) -> dict[str, Any]:
    cache_key = json.dumps(
        {
            "query": query,
            "top_k": top_k,
            "created_by": created_by,
            "source_agent": source_agent,
            "source_device": source_device,
            "permanent": permanent,
            "keyword": keyword,
            "sort": sort,
            "prefer": prefer,
            "reference_date": reference_date,
            "show_expired": show_expired,
        },
        sort_keys=True,
    )
    if use_cache:
        cached = CLIENT.local_store.cache_get("search:" + cache_key)
        if cached is not None:
            return {**cached, "cached": True}
    result = await CLIENT.request(
        "POST",
        f"/datasets/{CLIENT.dataset_id}/retrieve",
        {
            "query": query,
            "retrieval_model": build_retrieval_model(max(top_k * 4, 20)),
        },
    )
    if "error" in result:
        cached = CLIENT.local_store.cache_get("search:" + cache_key)
        return {**cached, "cached": True, "stale": True} if cached else result

    reference = _iso_to_dt(reference_date) if reference_date else datetime.now(timezone.utc)
    recency_weight = RECENCY_WEIGHT_CURRENT if prefer == "current" else RECENCY_WEIGHT
    records = []
    for item in result.get("records", []):
        segment = item.get("segment", {})
        permanent_flag = item.get("document_id") == CLIENT.permanent_document_id
        record = _record(segment, permanent_flag)
        record["score"] = item.get("score", 0)
        if not _filters_match(record, created_by, source_agent, source_device, permanent, keyword):
            continue
        if not show_expired and _is_expired(record, reference):
            continue
        age = _age_days(record.get("audit", {}).get("last_modified_at", ""), reference)
        recency = _recency_boost(age)
        temporal = _temporal_boost(query, record.get("event_date") or "", reference)
        dify_score = float(record.get("score") or 0.0)
        if prefer == "current":
            final = dify_score + recency_weight * recency + TEMPORAL_WEIGHT * temporal
        elif sort == "updated_at":
            final = dify_score
        else:
            final = dify_score + recency_weight * recency + TEMPORAL_WEIGHT * temporal
        record["age_days"] = round(age, 3) if age is not None else None
        record["score_details"] = {
            "dify_score": dify_score,
            "recency": round(recency, 4),
            "recency_weight": recency_weight,
            "temporal": round(temporal, 4),
            "temporal_weight": TEMPORAL_WEIGHT,
            "final": round(final, 4),
        }
        record["score"] = round(final, 4)
        records.append(record)

    if sort == "updated_at":
        records.sort(
            key=lambda item: item.get("audit", {}).get("last_modified_at") or "",
            reverse=True,
        )
    elif sort == "created_at":
        records.sort(
            key=lambda item: item.get("audit", {}).get("created_at") or "",
            reverse=True,
        )
    else:
        records.sort(key=lambda item: item.get("score") or 0.0, reverse=True)

    top_records = records[:top_k]
    if not explain:
        for record in top_records:
            record.pop("score_details", None)

    payload = {
        "memories": top_records,
        "count": len(top_records),
        "cached": False,
        "sort": sort,
        "prefer": prefer,
    }
    if use_cache:
        CLIENT.local_store.cache_set("search:" + cache_key, payload)
    return payload


async def list_memories(
    page: int = 1,
    limit: int = 20,
    keyword: str = "",
    storage: str = "all",
    created_by: str = "",
    source_agent: str = "",
    source_device: str = "",
    show_expired: bool = False,
) -> dict[str, Any]:
    records = []
    reference = datetime.now(timezone.utc)
    # Dify segment pagination is not newest-first; scan enough pages so
    # last_modified_at sorting can surface the true latest records.
    fetch_limit = 100
    for document_id, permanent in CLIENT.document_ids():
        if storage != "all" and permanent != (storage == "long_term"):
            continue
        doc_page = 1
        while doc_page <= 30:
            result = await CLIENT.request(
                "GET",
                f"/datasets/{CLIENT.dataset_id}/documents/{document_id}/segments",
                {"page": doc_page, "limit": fetch_limit},
            )
            if "error" in result:
                return result
            data = result.get("data", [])
            for segment in data:
                record = _record(segment, permanent)
                if not show_expired and _is_expired(record, reference):
                    continue
                if _filters_match(record, created_by, source_agent, source_device, None, keyword):
                    records.append(record)
            if not data or len(data) < fetch_limit:
                break
            doc_page += 1
    records.sort(key=lambda item: item["audit"]["last_modified_at"], reverse=True)
    start = max(0, (page - 1) * limit)
    return {
        "memories": records[start : start + limit],
        "total": len(records),
        "page": page,
        "sorted_by": "last_modified_at_desc",
    }


async def flush_offline_queue() -> dict[str, Any]:
    completed = []
    failed = []
    for operation in list(CLIENT.local_store.pending()):
        if operation.get("type") == "add":
            result = await add_memory(**operation["payload"])
        elif operation.get("type") == "update":
            result = await update_memory(**operation["payload"])
        else:
            result = {"error": "Unsupported offline operation"}
        if result.get("success") or result.get("idempotent_replay"):
            CLIENT.local_store.resolve(operation["operation_id"])
            completed.append(operation["operation_id"])
        else:
            failed.append(operation["operation_id"])
    return {"completed": completed, "failed": failed, "queue": CLIENT.local_store.queue_status()}


async def get_health(check_dify: bool = True) -> dict[str, Any]:
    dify = {"checked": False, "ok": None}
    if check_dify:
        result = await CLIENT.request("GET", "/datasets?page=1&limit=1")
        dify = {"checked": True, "ok": "error" not in result}
    return {
        "status": "ok" if not CLIENT.missing_config() and dify.get("ok", True) else "degraded",
        "version": "1.10.1",
        "ranking": {
            "recency_half_life_days": RECENCY_HALF_LIFE_DAYS,
            "recency_weight": RECENCY_WEIGHT,
            "recency_weight_current": RECENCY_WEIGHT_CURRENT,
            "temporal_weight": TEMPORAL_WEIGHT,
        },
        "missing_config": CLIENT.missing_config(),
        "dify": dify,
        "offline_queue": CLIENT.local_store.queue_status(),
        "cache": CLIENT.local_store.cache_status(),
        "metrics": metrics_snapshot(),
    }
