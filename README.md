# dify-memory-mcp (Dify edition)

基于 Dify 知识库的 AI Agent 跨会话记忆与 Session Archive MCP Server。

> ## Dify edition
>
> 本版本已从 Dify 移植到 **Dify**。Dify 是 Dify 的内部分支，两者共用
> `/datasets` 知识库 Service API，因此移植只涉及两类改动：
>
> 1. **Base URL** —— 从 `service.dify.mioffice.cn/api/v1` 改为任意 Dify 的 `/v1`
>    （`DIFY_BASE_URL`，兼容旧的 `DIFY_BASE_URL`）
> 2. **重排配置** —— 移除 Dify 内部的通义重排模型，默认改为
>    `hybrid_search` + `weighted_score`，**无需任何 rerank 模型**即可在
>    标准 Dify 上运行
>
> 检索参数集中在 `runtime_config.build_retrieval_model()`，通过 `MEMORY_*`
> 环境变量调优，不必改动代码。

## 能力

- 短期/长期记忆 CRUD、衰减和升级
- 检索 recency 重排、`fact_key` upsert、`event_date`/`expiration_date` 时间维
- `hybrid_search` + Rerank
- 最新记录审计与 `expected_content_hash` 并发保护
- 隐私标签、凭据过滤、幂等写入和离线队列
- OpenCode/OpenClaw 会话提取与跨工具会话检索
- 多 harness 持久化注册和健康诊断

权威数据存储在 Dify 知识库；本地只保存缓存和离线队列，即本地缓存和离线队列。

## 快速开始

### 前置条件（Dify）

1. 一个可用的 Dify 实例，且**已配置默认的文本嵌入模型**（否则无法创建知识库，
   报 `Default model not found for text-embedding`）
2. Dify 知识库 API Key（控制台 → 数据集 → API 密钥，`dataset-` 开头）
3. Python 3.10+

### 安装

```bash
# 可选：指定 Dify 地址，默认 http://127.0.0.1/v1
export DIFY_BASE_URL=http://127.0.0.1/v1

python scripts/install.py --api-key "<dataset-key>"
```

安装器会创建/复用记忆知识库、写入检测到的 harness 注册，并返回安装结果 JSON、
配置路径和重启清单。详细流程见 [references/installation.md](references/installation.md)。

> **安全提醒**：安装结果里的 `dataset_id` / `document_id` 和你的 API Key
> 不要提交到版本库。真实凭据写在 `scripts/.env`（已在 `.gitignore` 中），
> 仓库里只保留 `scripts/.env.example` 占位模板。

## 配置

将配置文件保存到：$MCPORTER_CONFIG，或使用当前工作目录的相对路径。
数据持久化目录：./config/mcporter.json

配置读取优先级：

1. `$MCPORTER_CONFIG`
2. `./config/mcporter.json`

`./config/mcporter.json` 是配置文件路径，缓存和离线队列目录由
`persistence.directory` 决定。完整字段见
[references/environment.md](references/environment.md)。

OpenCode 必须使用规范键 `mcp.dify-memory.environment`。旧 `mcp.memory`
仅用于迁移，不应新增。

## MCP 注册

`dify-memory` 使用本地 stdio `command/args`。注册必须写入 harness 长期配置。
模板、OpenCode/OpenClaw 示例和 HTTP MCP 契约见
[references/mcp-registration.md](references/mcp-registration.md)。

## 工具

| 类别 | 工具 |
|---|---|
| 记忆管理 | `add_memory`、`search_memory`、`update_memory`、`delete_memory`、`list_memories`、`decay_memories`、`promote_memory` |
| 运维诊断 | `get_memory`、`flush_offline_queue`、`get_health` |
| Session Archive | `save_session`、`list_sessions`、`search_session`、`get_session_detail`、`delete_session` |

完整参数和返回结构见 [references/api-reference.md](references/api-reference.md)。

Session Archive 的 5 个工具需要：

- `DIFY_SESSION_INDEX_DOCUMENT_ID`
- `DIFY_SESSIONS_DATASET_ID`

首次归档必须由用户明确触发；已归档会话可增量同步。

## 架构

```text
Agent
  -> MCP stdio server.py
      -> memory_service / session_store
          -> Dify API
      -> local_store
          -> cache.json
          -> offline-queue.json
```

数据文档：

- `agent-memories`：短期记忆
- `agent-memories-permanent`：长期记忆
- `session-indexes`：会话索引
- Sessions Dataset：全量会话

详细生命周期、数据格式和安全边界见
[references/architecture.md](references/architecture.md)。

## 环境变量

| 变量 | 用途 |
|---|---|
| `DIFY_API_KEY` | Dify API Key |
| `DIFY_DATASET_ID` | 记忆知识库 ID |
| `DIFY_DOCUMENT_ID` | 短期文档 ID |
| `DIFY_PERMANENT_DOCUMENT_ID` | 长期文档 ID |
| `DIFY_SESSION_INDEX_DOCUMENT_ID` | 会话索引文档 ID |
| `DIFY_SESSIONS_DATASET_ID` | Session Archive 知识库 ID |
| `DIFY_BASE_URL` | Dify API 地址（默认 `http://127.0.0.1/v1`，兼容旧 `DIFY_BASE_URL`） |
| `MEMORY_*` | 检索与重排调优，见 `runtime_config.build_retrieval_model` |
| `MEMORY_DECAY_DAYS` | 冷记忆阈值 |
| `DIFY_MEMORY_MCP_IDLE_TIMEOUT_SECONDS` | stdio 空闲回收时间 |
| `MCPORTER_CONFIG` | harness 中立配置文件 |
| `DIFY_MEMORY_STATE_DIR` | 覆盖 `persistence.directory` |
| `OPENCODE_DB_PATH` | 覆盖 OpenCode SQLite 路径 |
| `OPENCLAW_SESSIONS_ROOT` | 覆盖 OpenClaw 会话根目录 |

## 安装文件

```text
dify-memory-mcp/
├── SKILL.md
├── README.md
├── CHANGELOG.md
├── AGENT_RULES.md
├── references/
│   ├── installation.md
│   ├── mcp-registration.md
│   ├── api-reference.md
│   ├── architecture.md
│   ├── environment.md
│   └── troubleshooting.md
├── scripts/
│   ├── install.py
│   ├── setup.py
│   ├── server.py
│   ├── session_store.py
│   ├── memory_service.py
│   ├── memory_model.py
│   ├── runtime_config.py
│   ├── openclaw_session.py
│   ├── local_store.py
│   ├── privacy.py
│   ├── observability.py
│   ├── flush_offline_queue.py
│   └── .env.example
├── config/
│   ├── mcporter.json
│   ├── dify-memory-plugin.jsonc
│   └── openclaw-linux.json5.example
├── plugins/
│   └── dify-memory-inject.js
├── deploy/
├── tests/
├── Dockerfile
├── pyproject.toml
└── requirements.lock
```

安装器会复制 `SKILL.md`、`README.md`、`CHANGELOG.md`、`AGENT_RULES.md`、
`references/`、运行脚本、插件和配置模板。

## 故障排查

调用 `get_health` 后检查：

- `missing_config`
- Dify 连接
- `cache.json` 和 `offline-queue.json`
- `sessionSources.default`
- harness 注册键和环境字段
- `mcp_registration.config_paths`

常见错误和处理步骤见
[references/troubleshooting.md](references/troubleshooting.md)。

## FAQ

### 多个 harness 会共享记忆吗？

使用相同 API Key 与 Dataset/Document ID 时共享同一记忆库。
需要隔离时使用独立的 Dify 知识库。

### 记忆保存在哪里？

权威内容在 Dify 知识库。本地只保存搜索缓存、离线队列和可选会话源索引。

### 如何验证安装？

重启安装结果中的 harness，调用 `get_health`，再调用 `list_memories`。

### 如何重置记忆？

在 Dify 中清理 `opencode-agent-memory` 和 `agent-session-archive`，
再重新运行 `install.py`。

### OpenClaw 能否替代原生记忆？

推荐并存。`dify-memory-mcp` 提供跨工具语义检索，不复制 OpenClaw 的
session bootstrap、dreaming 和 auto flush。

## 发布历史

完整变更记录见 [CHANGELOG.md](CHANGELOG.md)。
