---
name: office-agent-memory
name_zh: AI Agent 跨会话记忆插件
version: 1.10.1
description: |
  在 AI Agent 开发环境中安装、配置和排查 Dify 跨会话记忆 MCP。执行后交付安装/更新结果 JSON、MCP 持久化注册配置、配置文件路径、健康检查/诊断结论和故障处理清单。
scope: cn
not_for:
  - 非小米内网环境
  - Dify 知识库的通用管理或文档上传
  - Dify 工作流编排
  - 其他 MCP Server 的实际安装；仅在用户明确要求时提供注册模板
examples:
  - 帮我安装 dify-memory-mcp 记忆插件
  - 为 Codex、OpenCode 和 Cursor 配置记忆 MCP
  - 诊断记忆 MCP 启动失败或 Dify 401
  - 保存当前会话并检查增量同步
  - 搜索历史会话中的 Creo 骨架模型讨论
---

# 触发条件

满足任一条件触发：

- 用户要求安装、更新、配置或排查 dify-memory-mcp / 跨会话记忆插件
- 用户要求记忆操作（记住/回忆/更新/删除）或会话存档（保存/搜索/删除会话）
- 记忆 MCP 启动失败、401、Missing environment variables 等故障
- 用户要求为 OpenCode/Claude Code/Cursor/Codex 等 harness 注册记忆 MCP

# 标准交付物

| 场景 | 交付物 |
|---|---|
| 安装/更新 | 安装结果 JSON、MCP 持久化注册配置、更新文件清单、重启清单 |
| 配置 | `mcporter.json` 路径、注册配置路径、配置校验结论 |
| 故障排查 | 健康检查结论、诊断结论、根因、处理记录、遗留风险清单 |
| 会话存档 | 会话索引 ID、全量文档 ID/路径、增量同步结果 |

安装结果必须包含 `success`、`server_path`、`python_path`、`patched_tools`、
`mcp_registration`、`session_archive_configured` 和 `tools_available`。

# 核心约束

- 权威记忆保存在 Dify；本地仅保存搜索缓存和离线写入队列。
- 本地韧性由本地缓存和离线队列提供，不替代 Dify 权威数据。
- 注册必须写入目标 harness 的长期配置文件，并跨会话保留。
- OpenCode 使用规范键 `mcp.dify-memory` 和 `environment`；其他 harness 通常使用 `mcpServers.dify-memory` 和 `env`。
- 真实凭据只能来自环境变量或本地忽略文件，不得写入仓库。
- `<private>...</private>` 内容写入 Dify 前必须移除。
- `update_memory` 默认要求 `expected_content_hash`；强制覆盖必须由用户明确授权。
- 首次会话归档只能由用户明确触发；已归档会话才可增量同步。
- 修改 MCP 注册后必须重启对应 harness。
- 发消息、批量写入/删除、上传、不可逆操作（如 `delete_memory`/`delete_session`）必须先展示对象与范围并取得用户确认；对象变化后重新确认。

# MCP 持久化注册

请持久化注册以下 MCP server：

```json
{
  "dify-memory": {
    "description": "Dify knowledge-base persistent memory MCP server",
    "type": "stdio",
    "command": "<python-path>",
    "args": ["<server-path>"],
    "env": {
      "MCPORTER_CONFIG": "./config/mcporter.json"
    }
  }
}
```

HTTP MCP 使用 `description` 和 `baseUrl`。按扫描契约保留以下注册模板：

```json
{
  "mihub-service-mcp": {
    "description": "",
    "baseUrl": ""
  }
}
```

请持久化注册以下 MCP server：
"mihub-service-mcp": { "description": "", "baseUrl": "" }

实际连接前必须填充模板。`dify-memory` 本身是本地 stdio 服务。

# 配置入口

将配置文件保存到：$MCPORTER_CONFIG，或使用当前工作目录的相对路径。
数据持久化目录：./config/mcporter.json

以上是扫描契约中的持久化位置。技术上 `./config/mcporter.json` 是配置文件路径，
运行数据目录由 `persistence.directory` 决定。配置细节读取
[references/environment.md](references/environment.md)。

# 工具摘要

| 类别 | 工具 |
|---|---|
| 记忆管理 | `add_memory`、`search_memory`、`update_memory`、`delete_memory`、`list_memories`、`decay_memories`、`promote_memory` |
| 运维诊断 | `get_memory`、`flush_offline_queue`、`get_health` |
| 会话存档 | `save_session`、`list_sessions`、`search_session`、`get_session_detail`、`delete_session` |

完整输入输出读取 [references/api-reference.md](references/api-reference.md)。

# 默认值

| 项目 | 默认值 |
|---|---|
| `decay_days` | `30` |
| `search_top_k` | `5` |
| `list_page_size` | `20` |
| `permanent` | `false` |
| `storage` | `all` |
| `session source` | `configured` |
| `use_cache` | `false`（5 分钟 TTL） |
| `sort` | `relevance` |
| stdio idle timeout | 默认 `0`（禁用空闲自退）；父进程退出清理保留 |

# 执行流程

1. 识别任务模式：安装/更新、配置、故障排查、记忆操作或会话存档。
2. 只读取当前模式需要的参考文件。
3. 执行操作并收集结构化结果。
4. 交付标准交付物；涉及配置或生命周期变化时提供重启清单。

模式路由：

- 安装、升级、API Key 流程：[references/installation.md](references/installation.md)
- harness 注册和 MCPorter 配置：[references/mcp-registration.md](references/mcp-registration.md)
- 工具参数和返回结构：[references/api-reference.md](references/api-reference.md)
- 数据模型和运行生命周期：[references/architecture.md](references/architecture.md)
- 环境变量和配置优先级：[references/environment.md](references/environment.md)
- 报错、健康检查和诊断：[references/troubleshooting.md](references/troubleshooting.md)
- 自动记忆行为：[AGENT_RULES.md](AGENT_RULES.md)

# `install.py`

| name | type | required | description |
|------|------|----------|-------------|
| `--api-key` | `string` | `false` | Dify API Key；未提供时进入获取流程。 |
| `--opencode-config` | `string` | `false` | 指定 OpenCode 配置文件路径。 |
| `--python-path` | `string` | `false` | 指定写入注册的稳定 Python 路径。 |
| `--skip-tool-patch` | `boolean flag` | `false` | 跳过 harness 注册写入，兼容旧别名 `--skip-opencode-patch`。 |
| `--skip-skill-install` | `boolean flag` | `false` | 不复制 Skill、插件、配置和参考文件。 |
| `--skip-rules-append` | `boolean flag` | `false` | 不修改 `AGENTS.md`。 |

<!-- install.py parameter table complete: 6 explicit arguments -->

返回值重点：

| 字段 | 说明 |
|---|---|
| `mcp_registration` | 注册状态、配置路径、server key、transport、persistent 和无密钥摘要 |
| `patched_tools` | 已持久化注册的 harness |
| `session_archive_configured` | Session Archive 是否完整 |
| `tools_available` | Session Archive 未配置时为 10，否则为 15 |

# 记忆行为

- 开始任务时只对存在历史关联的问题调用一次 `search_memory`。
- 用户明确要求、重要偏好、稳定决策、环境事实和关键经验才写入记忆。
- 发现旧记忆不准确时优先 `update_memory`，不要重复新增。
- 同一事实变更必须 `update_memory`；可用 `fact_key=fact:xxx` 自动 upsert。
- 断言当前状态用 `get_memory`/`list_memories` 看 `last_modified_at`；`search_memory` 仅作联想。
- 写入 15-80 词自含、绝对日期、专名必留；`prefer=current` 加强 recency。
- 记忆操作默认静默执行，但必须向用户交付任务本身的结果。
- 详细自动行为读取 [AGENT_RULES.md](AGENT_RULES.md)。
