# 环境与配置

## 配置文件

将配置文件保存到：$MCPORTER_CONFIG，或使用当前工作目录的相对路径。
数据持久化目录：./config/mcporter.json

读取优先级：

1. `$MCPORTER_CONFIG`
2. `./config/mcporter.json`

`./config/mcporter.json` 是配置文件路径。缓存和离线队列的数据目录由
`persistence.directory` 决定，默认 `./data/mify-memory-mcp`。

```json
{
  "persistence": {
    "directory": "./data/mify-memory-mcp"
  },
  "sessionSources": {
    "default": "opencode",
    "opencode": {
      "databasePath": "~/.local/share/opencode/opencode.db"
    },
    "openclaw": {
      "sessionsRoot": "~/.openclaw/agents"
    }
  }
}
```

## Mify 凭据

| 变量 | 用途 |
|---|---|
| `MIFY_API_KEY` | `dataset-` 格式 API Key |
| `MIFY_DATASET_ID` | 记忆知识库 ID |
| `MIFY_DOCUMENT_ID` | 短期记忆文档 ID |
| `MIFY_PERMANENT_DOCUMENT_ID` | 长期记忆文档 ID |
| `MIFY_SESSION_INDEX_DOCUMENT_ID` | 会话索引文档 ID |
| `MIFY_SESSIONS_DATASET_ID` | Session Archive 知识库 ID |
| `MIFY_BASE_URL` | Mify API 地址 |

## 运行参数

| 变量 | 用途 |
|---|---|
| `MEMORY_DECAY_DAYS` | 冷短期记忆阈值，默认 30 |
| `MIFY_MEMORY_MCP_IDLE_TIMEOUT_SECONDS` | 默认 0，不因空闲退出；仅显式有限正数启用计时回收。父进程退出清理独立保留 |
| `MIFY_MEMORY_ACTOR` | 审计 actor |
| `MIFY_MEMORY_AGENT` | 审计 Agent |
| `MIFY_MEMORY_DEVICE_ID` | 审计设备 |
| `MCPORTER_CONFIG` | harness 中立配置文件路径 |

## 兼容覆盖项

以下变量优先于 `mcporter.json` 对应字段，适合迁移和特殊部署：

| 变量 | 覆盖项 |
|---|---|
| `MIFY_MEMORY_STATE_DIR` | `persistence.directory` |
| `OPENCODE_DB_PATH` | `sessionSources.opencode.databasePath` |
| `OPENCLAW_SESSIONS_ROOT` | `sessionSources.openclaw.sessionsRoot` |

## 配置校验

1. `get_health` 不应返回 `missing_config`。
2. `MIFY_API_KEY` 必须以 `dataset-` 开头。
3. Session Archive 需要两个会话 ID。
4. 自动同步需要 `sessionSources.default` 为 `opencode` 或 `openclaw`。
5. 真实凭据只能保存在环境变量或本地忽略文件中。
