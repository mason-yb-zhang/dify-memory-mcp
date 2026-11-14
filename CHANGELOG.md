# Changelog

All notable changes to `dify-memory-mcp` are documented here.

## 1.11.0 - 2026-11-11

### Changed (Dify edition)
- **Ported from Dify to Dify.** Dify is a fork of Dify and shares the same
  `/datasets` knowledge-base Service API, so the port is limited to two areas.
- Base URL default changed from `https://service.dify.mioffice.cn/api/v1` to
  `http://127.0.0.1/v1`. `DIFY_BASE_URL` is the preferred variable;
  `DIFY_BASE_URL` remains honoured as a legacy alias.
- Removed the hardcoded Dify-internal rerank model
  (`langgenius/tongyi/tongyi` / `gte-rerank-v2_mi_sys`). Retrieval now defaults
  to `hybrid_search` + `weighted_score`, which needs no rerank model provider
  and therefore runs on a stock Dify install.
- Retrieval behaviour is now configured centrally in
  `runtime_config.build_retrieval_model()` and tuned through `MEMORY_*`
  variables (search method, rerank mode, vector/keyword weights, embedding
  provider/model) instead of being spread across call sites.

### Fixed
- `memory_service.DifyClient` hardcoded its base URL and ignored the
  environment; it now resolves through `runtime_config.dify_base_url()`.
- `tests/test_no_committed_secrets.py` assumed a fixed repository nesting
  depth (`parents[2]`) and failed when the package was checked out at another
  level. It now asks git for the repository root.

## 1.10.1 - 2026-10-27

### Fixed
- stdio idle-timeout default changed from 1800s to 0 (disabled). A healthy
  stdio connection could sit idle (e.g. between user turns) and get reaped by
  the previous default, forcing a host restart before native tools were
  visible again. Only an explicit, finite, non-negative
  `DIFY_MEMORY_MCP_IDLE_TIMEOUT_SECONDS` now enables the timer; invalid or
  negative values fall back to disabled instead of to 1800. Parent-process
  exit cleanup in `_watch_lifecycle` is unchanged.

## 1.10.0 - 2026-09-24

### Added
- Recency re-ranking (half-life 21d) with `sort` / `prefer=current` / `explain` / `reference_date`
- `fact_key` auto-upsert on `add_memory` (same fact updates in place)
- `event_date` / `expiration_date` fields and `show_expired` filters
- Write-style soft warnings (too short / relative date without absolute)

### Fixed
- `fact_key` lookup no longer relies on Dify keyword filter (client-side scan)
- `list_memories` scans full segment pages so `last_modified_at` sort surfaces newest
- `update_memory` handles Dify single-object `data` payload shape

### Changed
- `search_memory.use_cache` default false; cache entries expire after 5 minutes
- Tool descriptions encode write policy and search vs get/list division of labor

## 1.9.0 - 2026-09-23

### Changed

- Reorganized `SKILL.md` into a concise execution entrypoint with progressive
  disclosure through six `references/` guides.
- Reduced README duplication and kept complete release history in CHANGELOG.
- Moved standalone regression tests under `tests/integration/`.
- Installation and wheel packaging now include all reference documents.

## 1.8.5 - 2026-09-22

### Added

- Documentation contracts for current versions, all 15 tools, environment
  variables, file inventory, and harness-neutral persistence behavior.

### Fixed

- README and SKILL no longer claim pure-cloud storage or OpenCode-only restart.
- OpenCode setup output now uses the canonical `mcp.dify-memory` key.
- `mcp_registration` exposes required metadata directly and documents
  `tools_available` semantics.

## 1.8.4 - 2026-09-22

### Changed

- Replaced ambiguous registration-tool wording with direct
  persistent-configuration language to prevent false scanner findings.
- Added a package-wide contract test for ambiguous registration-tool wording.

## 1.8.3 - 2026-09-22

### Added

- Complete `install.py` parameter table with `name`, `type`, `required`, and
  `description` fields for all six explicit arguments and the legacy alias.
- Contract tests for persistent MCP registration, MCPorter path wording, and
  parameter-table completeness.

### Changed

- Scanner-facing registration and persistence declarations now use the exact
  requested forms for `mihub-service-mcp` and `./config/mcporter.json`.

## 1.8.2 - 2026-09-22

### Added

- Persistent, harness-owned MCP registration output with secret-free metadata.
- Explicit standard deliverables for installation results, registration config,
  MCPorter paths, health/diagnostic conclusions, and troubleshooting lists.

### Changed

- Installer registrations always persist `MCPORTER_CONFIG` and write directly to
  durable harness-owned configuration.
- Skill description now names the concrete artifacts produced by each workflow.

## 1.8.1 - 2026-09-22

### Added

- Harness-neutral MCPorter configuration through `$MCPORTER_CONFIG` or `./config/mcporter.json`.
- Configurable persistence, OpenCode SQLite, and OpenClaw JSONL source paths.
- `configured` as the default session source for cross-harness automatic synchronization.
- Wheel data files for the MCPorter template and configuration/deployment documentation.

### Changed

- Local cache and offline queue now default to `./data/dify-memory-mcp` instead of an OpenCode-specific location.
- Missing session source paths disable local extraction with a clear skip result instead of binding to OpenCode.

## 1.8.0 - 2026-09-22

### Added

- Single-record audit metadata with creator, modifier, source agent/device, and change summary fields.
- `get_memory`, `flush_offline_queue`, `get_health`, and `memory://health`.
- OpenClaw JSONL session extraction and source identity filters.
- Privacy redaction for credentials, bearer tokens, private keys, and national IDs.
- Structured logs, metrics, Docker packaging, locked dependencies, and systemd offline-flush timer.
- Standalone release changelog and GitLab CI quality gates for Python tests, Node contract tests, and wheel packaging.

### Changed

- Updates now require `expected_content_hash` unless `allow_unsafe_overwrite=true`.
- Dify writes use idempotency keys, bounded retries, local caching, and an offline queue.

## 1.7.2 - 2026-09-22

### Added

- Parent-process lifecycle monitoring and configurable stdio idle timeout.

### Fixed

- MCP children now exit when the host disappears or an idle stdio session becomes stale.
- Session Store HTTP clients are closed during MCP shutdown.

## 1.7.1 - 2026-09-22

### Added

- Linux/OpenClaw JSON5 configuration template and deployment guidance.
- `--skip-tool-patch`, with `--skip-opencode-patch` retained as an alias.

### Fixed

- The installer no longer rewrites JSON/JSON5 files it cannot parse.
- OpenCode now uses the canonical `dify-memory` key and removes the legacy duplicate `memory` key.
- Gemini configuration is supported by the installer.

## 1.7.0 - 2026-09-17

### Added

- MCP resources: `memory://search`, `memory://list`, and `memory://sessions`.

## 1.6.1 - 2026-08-29

### Fixed

- Invalid non-`dataset-` `DIFY_API_KEY` environment values no longer override the valid `.env` credential.
- Added regression coverage for environment-variable pollution.

## 1.6.0 - 2026-07-23

### Added

- Post-compaction memory extraction from complete OpenCode session timelines.
- Temporary extraction-session isolation, credential redaction, and compaction snapshot deduplication.

### Changed

- Update mode validates required OpenCode environment fields and migrates legacy `env` configuration.

## 1.5.0 - 2026-07-10

### Added

- `<private>...</private>` privacy filtering for memory and manual session writes.
- OpenCode automatic memory injection plugin.
- User/Profile, Project/Context, and Relevant memory injection categories.

## 1.4.5 - 2026-07-09

### Fixed

- Installer refreshes managed Agent Memory Rules instead of preserving stale content.

## 1.4.4 - 2026-07-09

### Fixed

- Installer includes `session_store.py`.
- Atomic configuration writes leave no temporary file on failure.
- Upload and delete operations no longer report false success.

## 1.4.3 - 2026-06-25

### Added

- Claude Desktop configuration detection.

## 1.4.2 - 2026-06-24

### Added

- Automatic configuration for OpenCode, Claude Code, Claude Desktop, Cursor, Windsurf, and OpenClaw.

## 1.4.1 - 2026-06-24

### Changed

- Memory writes incrementally synchronize already archived sessions.

## 1.4.0 - 2026-06-24

### Added

- Session Archive with incremental append and per-message source annotations.
- Session index and full-session datasets.
- OpenCode SQLite session extraction.

## 1.3.0 - 2026-06-18

### Changed

- Search now uses hybrid semantic/full-text retrieval with reranking.

## 1.2.0 - 2026-06-17

### Added

- Short-term and permanent memory layers.
- `promote_memory`, `permanent`, and `storage` operations.

### Fixed

- Segment updates and deletes now detect the owning memory document reliably.

## 1.0.0 - 2026-05-27

### Added

- Initial six-tool MCP memory server with single-document storage.
