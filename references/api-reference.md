# MCP 工具参考

服务完整注册 15 个工具。Session Archive 的 5 个工具还需要会话索引和全量存储 ID 才能实际工作。

## 记忆管理

### `add_memory`

新增一条记忆。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `content` | `string` | 是 | 自含正文 15-80 词；相对时间落地为绝对日期；保留专名/路径/版本 |
| `keywords` | `list[string]` | 否 | 2-6 个标签；可用 `fact:<slug>` 作稳定身份键 |
| `permanent` | `boolean` | 否 | 默认 `false`；`true` 写入长期文档 |
| `fact_key` | `string` | 否 | 如 `fact:user-cad-tool`；已存在则原地更新 |
| `event_date` | `string` | 否 | 事实成立日 `YYYY-MM-DD`，参与时间排序 |
| `expiration_date` | `string` | 否 | 过期后默认隐藏，除非 `show_expired=true` |

### `search_memory`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | `string` | 是 | 自然语言查询；含时间词可触发 temporal boost |
| `top_k` | `integer` | 否 | 返回数量，默认 5 |
| `sort` | `string` | 否 | `relevance`（默认）/`updated_at`/`created_at` |
| `prefer` | `string` | 否 | `current` 加强 recency，用于“现在是什么” |
| `explain` | `boolean` | 否 | 返回 `score_details`（dify/recency/temporal/final） |
| `reference_date` | `string` | 否 | 将该日期视为“现在”做 recency/时间打分 |
| `show_expired` | `boolean` | 否 | 包含已过期记忆 |
| `use_cache` | `boolean` | 否 | 默认 `false`；本地缓存 5 分钟 TTL |

使用 `hybrid_search` + Rerank + recency 重排，覆盖短期、长期记忆。联想用 search；断言当前状态用 `get_memory`/`list_memories`。

### `update_memory`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `segment_id` | `string` | 是 | 目标记忆 ID |
| `content` | `string` | 是 | 新内容 |
| `expected_content_hash` | `string` | 条件必填 | 并发保护；未设置时必须显式允许覆盖 |
| `allow_unsafe_overwrite` | `boolean` | 否 | 明确跳过并发检查 |
| `keywords` | `list[string]` | 否 | 替换关键词 |
| `event_date` | `string` | 否 | 更新事件日期 |
| `expiration_date` | `string` | 否 | 更新过期日期 |
| `updated_by` | `string` | 否 | 修改者 |
| `source_agent` | `string` | 否 | 来源 Agent |
| `source_device` | `string` | 否 | 来源设备 |
| `change_summary` | `string` | 否 | 修改摘要 |

### `delete_memory`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `segment_id` | `string` | 是 | 要删除的记忆 ID |

### `list_memories`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `page` | `integer` | 否 | 默认 1 |
| `limit` | `integer` | 否 | 默认 20，最大 100 |
| `storage` | `string` | 否 | `short_term`、`long_term` 或 `all` |
| `keyword` | `string` | 否 | 关键词过滤（支持 `fact:slug`） |
| `show_expired` | `boolean` | 否 | 包含已过期记忆 |

按 `last_modified_at` 倒序返回，适合核对最新状态。

### `decay_memories`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `days` | `integer` | 否 | 冷记忆阈值，默认 30 天 |

只处理短期文档。

### `promote_memory`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `segment_id` | `string` | 是 | 从短期升级到长期的记忆 ID |

## 运维与诊断

### `get_memory`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `segment_id` | `string` | 是 | 读取当前内容、审计信息和 `content_hash` |

### `flush_offline_queue`

重放本地离线队列。无参数。

### `get_health`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `check_dify` | `boolean` | 否 | 是否检查 Dify 连接，默认 `true` |

返回配置缺失项、Dify 状态、缓存、队列和指标。

## Session Archive

### `save_session`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 否 | 会话 ID；未指定时读取当前来源最近会话 |
| `source` | `string` | 否 | `configured`、`opencode`、`openclaw` 或 `manual` |
| `content` | `string` | 条件必填 | `source=manual` 时必须提供 |
| `title` | `string` | 否 | 会话标题 |
| `created_by` | `string` | 否 | 创建者 |

### `list_sessions`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `limit` | `integer` | 否 | 返回数量 |
| `keyword` | `string` | 否 | 标题或摘要过滤 |

### `search_session`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | `string` | 是 | 会话全文查询 |
| `top_k` | `integer` | 否 | 返回片段数 |

### `get_session_detail`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `segment_id` | `string` | 是 | 会话片段 ID |
| `document_id` | `string` | 否 | 指定会话文档，缩短查找 |

### `delete_session`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session_id` | `string` | 是 | 删除会话索引和全量文档 |

## 通用结果

- 所有工具返回结构化 JSON。
- 隐私过滤后为空时返回 `skipped=true`。
- Dify 写失败且允许重试时写入本地离线队列。
- 更新冲突返回 `expected_content_hash` 和 `current_content_hash`。
