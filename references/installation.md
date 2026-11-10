# 安装与更新

## 适用场景

- 首次安装 `mify-memory-mcp`
- 为多个 AI Agent harness 注册记忆服务
- 升级现有安装并修复缺失配置
- 执行交互式或无人值守安装

## 前置条件

| 依赖 | 要求 | 验证 |
|---|---|---|
| Python | 3.10+，建议使用稳定绝对路径 | `python --version` |
| Python 包 | `mcp==1.27.0`、`httpx==0.28.1` | `python -m pip show mcp httpx` |
| 网络 | 可访问 Dify 服务（默认 `http://127.0.0.1`） | `curl http://127.0.0.1/v1/datasets` 返回 401 |
| Dify API Key | `dataset-` 格式 | Dify 控制台 → 数据集 → API 密钥 |

## 无人值守安装

```bash
python scripts/install.py --api-key "<dataset-key>"
```

未提供 `--api-key` 时，安装器输出 `needs_api_key=true` 和 Mify API 获取地址。取得 Key 后重新执行同一命令。

完整流程：

1. 校验 Python 依赖。
2. 校验 Mify API Key。
3. 创建或复用记忆知识库和 Session Archive 知识库。
4. 复制 Skill、运行脚本、插件、配置和 `references/`。
5. 向检测到的 harness 写入持久化 MCP 注册。
6. 可选安装 OpenCode 记忆注入插件。
7. 可选刷新 `AGENT_RULES.md`。
8. 输出安装结果 JSON 和重启清单。

## 交互式安装

```bash
python scripts/setup.py
```

交互式流程适合人工首次部署。自动化或批量部署使用 `install.py`。

## 更新

重复运行 `install.py` 即可进入更新模式。更新模式会刷新 Skill、脚本、插件、配置模板和参考文档，不会重建已存在的 Mify 知识库。

更新后必须检查：

- `mcp_registration.status`
- `mcp_registration.config_paths`
- `session_archive_configured`
- `tools_available`
- `needs_configuration` 或 `missing_environment`
- 需要重启的 harness

## `install.py` 参数

| name | type | required | description |
|---|---|---|---|
| `--api-key` | `string` | `false` | Mify API Key；未提供时进入引导流程。 |
| `--opencode-config` | `string` | `false` | 指定 OpenCode 配置文件路径。 |
| `--python-path` | `string` | `false` | 指定稳定的 Python 可执行文件路径。 |
| `--skip-tool-patch` | `boolean flag` | `false` | 跳过 harness 配置写入，兼容旧别名 `--skip-opencode-patch`。 |
| `--skip-skill-install` | `boolean flag` | `false` | 跳过 Skill/插件/模板复制。 |
| `--skip-rules-append` | `boolean flag` | `false` | 不修改 `AGENTS.md`。 |

## 返回结果

安装结果 JSON 至少包含：

- `success`
- `dataset_id`
- `document_id`
- `permanent_document_id`
- `session_index_document_id`
- `sessions_dataset_id`
- `session_archive_configured`
- `server_path`
- `python_path`
- `skill_dir`
- `patched_tools`
- `mcp_registration`
- `opencode_memory_plugin`
- `rules_appended`
- `tools_available`

## 安装后验收

1. 重启安装结果中列出的 harness。
2. 调用 `get_health` 检查配置和 Mify 连接。
3. 调用 `list_memories` 验证读取。
4. 需要会话共享时，确认两个 Session Archive 环境 ID 已配置。
5. 首次会话归档仍需用户明确触发。
