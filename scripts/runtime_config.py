"""Harness-neutral MCPorter configuration and persistence paths."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def get_config_path() -> Path:
    configured = os.environ.get("MCPORTER_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / "config" / "mcporter.json"


def load_config() -> dict[str, Any]:
    path = get_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid MCPorter config JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"MCPorter config must contain a JSON object: {path}")
    return data


def _resolve_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def state_dir() -> Path:
    configured = os.environ.get("DIFY_MEMORY_STATE_DIR")
    if configured:
        return _resolve_path(configured)
    config = load_config()
    persistence = config.get("persistence", {})
    value = (
        persistence.get("directory")
        or config.get("persistenceDir")
        or config.get("dataDir")
        or "./data/dify-memory-mcp"
    )
    return _resolve_path(value)


def session_source() -> str:
    configured = os.environ.get("DIFY_MEMORY_SESSION_SOURCE")
    if configured:
        return configured.strip().lower()
    config = load_config()
    sources = config.get("sessionSources", {})
    return str(sources.get("default") or config.get("sessionSource") or "").strip().lower()


def opencode_db_path() -> Path | None:
    configured = os.environ.get("OPENCODE_DB_PATH")
    if configured:
        return _resolve_path(configured)
    config = load_config()
    value = config.get("sessionSources", {}).get("opencode", {}).get("databasePath")
    return _resolve_path(value) if value else None


def openclaw_sessions_root() -> Path | None:
    configured = os.environ.get("OPENCLAW_SESSIONS_ROOT")
    if configured:
        return _resolve_path(configured)
    config = load_config()
    value = config.get("sessionSources", {}).get("openclaw", {}).get("sessionsRoot")
    return _resolve_path(value) if value else None


# --- Dify (or Dify) service API -------------------------------------------------
#
# Dify is a fork of Dify, so both speak the same knowledge-base Service API
# under `/datasets/...`. Only the base URL and the rerank provider differ.

DEFAULT_BASE_URL = "http://127.0.0.1/v1"


def dify_base_url() -> str:
    """Knowledge-base Service API base URL.

    Prefers ``DIFY_BASE_URL`` and falls back to the legacy ``DIFY_BASE_URL``
    so an existing Dify deployment keeps working without changes.
    """
    for key in ("DIFY_BASE_URL", "DIFY_BASE_URL"):
        value = os.environ.get(key)
        if value:
            return value.rstrip("/")
    return DEFAULT_BASE_URL


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def build_retrieval_model(top_k: int) -> dict[str, Any]:
    """Build the Dify ``retrieval_model`` payload for ``/datasets/*/retrieve``.

    Defaults to ``hybrid_search`` + ``weighted_score``, which needs no rerank
    model provider — the vector/keyword blend is computed by Dify itself. This
    is what makes the port work on a stock Dify install.

    Tunables (env):
      MEMORY_SEARCH_METHOD        ``hybrid_search`` | ``semantic_search`` |
                                  ``full_text_search`` | ``keyword_search``
      MEMORY_RERANK_MODE          ``weighted_score`` | ``reranking_model`` | ``none``
      MEMORY_VECTOR_WEIGHT        0.0-1.0, default 0.7
      MEMORY_KEYWORD_WEIGHT       0.0-1.0, default 0.3
      MEMORY_EMBEDDING_PROVIDER   default ``ollama``
      MEMORY_EMBEDDING_MODEL      default ``bge-m3``
      MEMORY_RERANK_PROVIDER      used only when mode == ``reranking_model``
      MEMORY_RERANK_MODEL         used only when mode == ``reranking_model``
    """
    search_method = os.environ.get("MEMORY_SEARCH_METHOD", "hybrid_search").strip()
    rerank_mode = os.environ.get("MEMORY_RERANK_MODE", "weighted_score").strip()
    if not rerank_mode or rerank_mode.lower() in {"none", "off", "disabled"}:
        return {
            "search_method": search_method,
            "reranking_enable": False,
            "top_k": top_k,
            "score_threshold_enabled": False,
        }

    payload: dict[str, Any] = {
        "search_method": search_method,
        "reranking_enable": True,
        "reranking_mode": rerank_mode,
        "top_k": top_k,
        "score_threshold_enabled": False,
    }

    if rerank_mode == "reranking_model":
        payload["reranking_model"] = {
            "reranking_provider_name": os.environ.get("MEMORY_RERANK_PROVIDER", ""),
            "reranking_model_name": os.environ.get("MEMORY_RERANK_MODEL", ""),
        }
    else:
        payload["weights"] = {
            "vector_setting": {
                "vector_weight": _env_float("MEMORY_VECTOR_WEIGHT", 0.7),
                "embedding_provider_name": os.environ.get(
                    "MEMORY_EMBEDDING_PROVIDER", "ollama"
                ),
                "embedding_model_name": os.environ.get(
                    "MEMORY_EMBEDDING_MODEL", "bge-m3"
                ),
            },
            "keyword_setting": {
                "keyword_weight": _env_float("MEMORY_KEYWORD_WEIGHT", 0.3),
            },
        }
    return payload
