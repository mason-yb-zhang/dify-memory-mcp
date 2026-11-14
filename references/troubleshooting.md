# 故障排查

## 诊断顺序

1. 调用 `get_health`。
2. 检查 `missing_config`、Dify 连接和本地队列。
3. 确认 harness 注册键和环境字段。
4. 读取 `mcp_registration.config_paths`。
5. 根据下表处理，并交付诊断结论和处理记录。

## 常见问题

| 现象 | 可能原因 | 处理 |
|---|---|---|
| `Missing environment variables` | 注册缺少 Dify ID | 运行 `install.py --api-key <KEY>` 并校验注册 |
| HTTP 401 | API Key 无效或环境被非 `dataset-` 值污染 | 替换 Key；检查环境优先级 |
| 搜索无结果 | 新记忆仍在索引 | 等待数秒后重试 |
| `MCPORTER_CONFIG` 缺失 | 注册环境未落盘 | 补齐注册环境和配置路径 |
| 自动同步不执行 | 未配置默认会话源或尚未首次归档 | 配置 `sessionSources.default`，首次手动归档 |
| OpenClaw 无会话 | JSONL 根目录错误 | 校验 `sessionSources.openclaw.sessionsRoot` |
| OpenCode 无会话 | SQLite 路径错误 | 校验 `sessionSources.opencode.databasePath` |
| 工具列表为空 | 注册未持久化或 harness 未重启 | 校验注册键，重启 harness |
| 更新冲突 | 内容已被其他会话修改 | 使用最新 `expected_content_hash` |
| 离线队列堆积 | Dify 暂时不可用 | 网络恢复后调用 `flush_offline_queue` |

## 安装包缺失

安装结果必须包含：

- `scripts/runtime_config.py`
- `config/mcporter.json`
- `references/*.md`
- `README.md`
- `SKILL.md`
- `CHANGELOG.md`

缺失时重新运行 `install.py`，并检查 `skill_dir`。

## 会话归档

- 首次归档必须由用户明确要求。
- `save_session(source="manual")` 适合没有可提取会话源的 harness。
- `source=configured` 读取 `sessionSources.default`。
- 隐私过滤后无内容时返回 `skipped=true`。

## 交付格式

故障排查结束时输出：

- 健康检查结论
- 诊断结论
- 根因
- 处理记录
- 验证结果
- 遗留风险或待用户操作项
