# MCP 持久化注册

## 原则

注册必须写入目标 harness 的长期配置文件，并在重启后继续存在。不得依赖临时会话状态。

请持久化注册以下 MCP server：

```json
{
  "mify-memory": {
    "description": "Mify knowledge-base persistent memory MCP server",
    "type": "stdio",
    "command": "<python-path>",
    "args": ["<server-path>"],
    "env": {
      "MCPORTER_CONFIG": "./config/mcporter.json"
    }
  }
}
```

## OpenCode

OpenCode 使用 `mcp.mify-memory` 和 `environment` 字段：

```json
{
  "mcp": {
    "mify-memory": {
      "command": ["<python-path>", "<server-path>"],
      "enabled": true,
      "type": "local",
      "environment": {
        "MIFY_API_KEY": "<dataset-key>",
        "MIFY_DATASET_ID": "<dataset-id>",
        "MIFY_DOCUMENT_ID": "<document-id>",
        "MCPORTER_CONFIG": "<mcporter-config-path>"
      }
    }
  }
}
```

旧 `mcp.memory` 只作为迁移来源，不应继续新增。

## 其他 stdio Harness

Claude、Cursor、Windsurf、Gemini 等通常使用 `mcpServers.mify-memory`、`command`、`args` 和 `env`。字段名以目标 harness 为准。

## OpenClaw

合并 `config/openclaw-linux.json5.example`，不要覆盖已有 JSON5 配置。推荐：

```json5
{
  mcp: {
    "mify-memory": {
      command: "<python-path>",
      args: ["<server-path>"],
      env: {
        MCPORTER_CONFIG: "<mcporter-config-path>"
      }
    }
  }
}
```

## HTTP MCP 扫描契约

HTTP MCP 使用 `description` 和 `baseUrl`：

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

该模板需在实际连接前由服务提供方填充。`mify-memory` 本身是 stdio 服务。

## 验收

- 注册键、`command`、`args` 和环境字段符合目标 harness。
- `MCPORTER_CONFIG` 已写入注册环境。
- 配置文件中不存在重复的旧服务键。
- 重启 harness 后工具列表仍可发现。
