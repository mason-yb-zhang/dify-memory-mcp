# 架构与运行模型

## 组件

```text
Agent
  -> MCP stdio server.py
      -> memory_service / session_store
          -> Dify API
              -> Memory Dataset
              -> Sessions Dataset
      -> local_store
          -> cache.json
          -> offline-queue.json
```

`server.py` 负责 MCP 工具和资源；`memory_service.py` 负责记忆 CRUD；`session_store.py` 负责会话归档；`runtime_config.py` 统一解析运行路径。

## 记忆数据

| 文档 | 用途 | 生命周期 |
|---|---|---|
| `agent-memories` | 短期记忆 | 可按 `hit_count` 和时间衰减 |
| `agent-memories-permanent` | 长期记忆 | 不自动删除 |
| `session-indexes` | 会话摘要和指针 | 与 Session Archive 同步 |

每条记忆保存最新内容和审计信息：创建者、创建时间、修改者、修改时间、来源 Agent、来源设备和修改摘要。

## Session Archive

- 首次归档由用户明确触发。
- 已归档会话可随 `add_memory` 自动增量同步。
- `configured` 来源由 `sessionSources.default` 选择。
- OpenCode 从 SQLite 提取；OpenClaw 从 JSONL 提取；`manual` 接收直接文本。
- 未配置默认会话源时自动同步安全跳过。

## 本地韧性

- `cache.json` 保存短期搜索缓存。
- `offline-queue.json` 保存 Dify 写入失败后的待重试操作。
- 数据目录由 `persistence.directory` 决定。
- 本地文件不是权威记忆库；权威数据位于 Dify。

## MCP 生命周期

- stdio MCP 由 harness 启动并拥有。
- harness 应在会话结束时关闭 transport。
- server 提供父进程监控和空闲超时兜底。
- 修改 MCP 注册后必须重启对应 harness。

## 安全边界

- 不在代码、配置模板或文档中保存真实凭据。
- `<private>...</private>` 内容写入 Dify 前移除。
- `update_memory` 默认要求 `expected_content_hash`。
- Dify 写入使用幂等键和有限重试。
