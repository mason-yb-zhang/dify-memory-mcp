# Agent Memory Rules（自动记忆行为规则）

**本文件定义 Agent 何时自动使用记忆工具。安装 dify-memory-mcp 后，Agent 应在每次对话中遵循以下规则。**

---

## 自动读取（每次对话开始时）

在开始处理用户请求之前，如果用户的问题涉及：
- 项目特定知识（工具、环境、偏好）
- 之前做过的决策
- 用户的个人偏好

则 **主动调用 `search_memory`**，用用户消息的核心关键词搜索，获取相关历史上下文。

**示例：**
- 用户说"帮我改 Creo 脚本" → `search_memory("Creo 开发规则")`
- 用户说"FreeCAD 建个螺丝" → `search_memory("FreeCAD 螺丝")`
- 用户说"配置一下环境" → `search_memory("环境配置")`

**不搜索的情况：**
- 纯闲聊/问候
- 用户明确说"不要查记忆"
- 通用编程问题（不涉及项目特定上下文）

---

## 自动写入（对话过程中）

当以下情况发生时，**主动调用 `add_memory`** 存储：

### 短期记忆 vs 长期记忆

| 存入短期（默认） | 存入长期（`permanent=true`） |
|-----------------|---------------------------|
| 日常工作记录 | 用户明确表达的**核心偏好** |
| 临时环境信息 | 长期不变的**架构决策** |
| 单次 debug 经验 | 反复验证的**关键经验教训** |
| 项目阶段性进展 | 用户说"**永远记住这个**" |

**判断原则：** 如果这条信息 3 个月后仍然有效且重要，存长期；否则存短期。

### 必须存储的信息

| 触发条件 | 示例 | 存储层 |
|----------|------|--------|
| 用户表达**偏好** | "我喜欢用 TypeScript"、"别生成注释" | **长期**（permanent=true） |
| 做出重要**决策** | "我们用方案 B"、"数据库选 PostgreSQL" | **长期**（permanent=true） |
| 发现**环境信息** | 新装的工具、路径、端口、版本 | 短期（可能变动） |
| 完成**项目里程碑** | 创建了新项目、部署成功、架构确定 | 短期 |
| 解决了**棘手问题** | debug 过程中发现的关键经验教训 | 短期（反复验证后 promote） |
| 用户明确说"**记住这个**" | 任何用户要求记忆的内容 | 看内容决定 |
| 用户说"**永远记住**" | 强调永久性的内容 | **长期**（permanent=true） |

### 写入格式规范

```
content: 自含陈述（15–80 词），相对时间→绝对日期 YYYY-MM-DD，专名/路径/版本必留
keywords: 2-6 个标签；同一事实可加 fact:<slug> 作为稳定身份键
permanent: true/false（是否为长期记忆）
可选: fact_key / event_date / expiration_date
```

**好的记忆：**
- `"项目 X 使用 React 18 + Vite，部署在 Vercel（2026-09-24 确认）"` → `["project-x", "tech-stack", "fact:project-x-stack"]`, permanent=true
- `"用户不喜欢在代码中加冗余注释（2026-09 起）"` → `["preference", "code-style", "fact:code-comment-style"]`, permanent=true
- `"FreeCAD 1.1 的 RPC Server 端口是 9876"` → `["freecad", "environment", "fact:freecad-rpc-port"]`, permanent=false

**坏的记忆（不要存）：**
- 空泛短句（“用户喜欢 CAD”——没有名字搜不到）
- 相对时间且无绝对日期（“上周改了端口”）
- 太长的上下文（超过 200 字）——拆成索引 + 正文
- 临时性信息（“帮我打开这个文件”）
- 通用知识（“Python 是解释型语言”）

---

## 自动更新（同事实覆盖，禁止叠条）

当已有事实**变更**时，**必须 `update_memory`（带 `expected_content_hash`）**，禁止再 `add_memory` 叠一条。

**判断方式：**
1. 先 `search_memory` / `list_memories(keyword="fact:...")` 找同事实
2. 用 `get_memory` 取 `content_hash` 与 `last_modified_at`
3. 同一事实 → `update_memory`；全新事实才 `add_memory`
4. 也可直接 `add_memory(fact_key="fact:xxx")`，服务端会自动 upsert

**检索习惯：**
- 联想/回忆 → `search_memory`
- **断言“现在是什么”→ `list_memories` 或 `get_memory` 看 `last_modified_at`，不要只信 search 排名**
- 需要新鲜结果时 `use_cache=false`（默认已是 false）；`prefer=current` 加强 recency

1. 先 `search_memory` 搜索相关记忆
2. 如果搜到的结果与当前事实矛盾 → 记录 `content_hash`，再用 `update_memory` 并填写修改者、Agent、设备信息
3. 如果没搜到 → `add_memory` 新增

---

## 自动升级（promote）

当发现某条短期记忆被多次检索命中（hit_count 高）或用户反复提及同一事实时，考虑用 `promote_memory` 将其升级为长期记忆。

**升级时机：**
- 同一条记忆在不同对话中被检索 3 次以上
- 用户再次强调某个已记录的偏好或决策
- 短期环境信息确认为长期稳定（如"这个端口以后都不会变了"）

---

## 自动清理

每 **20 次对话**左右（或用户主动要求时），调用一次 `decay_memories` 清理冷记忆。

**注意：** `decay_memories` 只清理短期记忆，长期记忆（permanent）永远不会被自动删除。

---

## 会话存档（Session Archive）

### 首次上传：仅手动触发

**禁止 Agent 自行决定上传哪个会话。** 只有用户明确要求时才调用 `save_session`。

| 触发条件 | 说明 |
|----------|------|
| 用户说"保存会话"、"存档对话"、"同步会话" | 手动触发首次上传 |
| 用户说"上传这个会话"、"把对话存下来" | 手动触发首次上传 |

### 已存档会话的更新：随记忆自动同步

对于**已经上传过的会话**，不需要手动再次调用 `save_session`。每次 `add_memory` 执行时，MCP Server 会自动检测当前会话是否已存档，若已存档则增量追加新消息：

```
add_memory 被调用
  ├─ 正常写入记忆
  └─ 检查当前会话是否已存档
       ├─ 未存档 → 不触发（首次必须用户手动）
       └─ 已存档 → 自动增量追加新消息到已有 Document
```

### 使用规则

- **首次必须手动**：Agent 绝不主动上传未存档的会话
- **后续自动同步**：已存档会话随 `add_memory` 自动保持更新，无需用户感知
- **手动同步也可**：对已存档会话，手动调 `save_session` 同样可以触发增量追加
- **来源标注**：每条消息自动标注 `source` 和 `time`，无需手动
- **切换工具时**：先调 `list_sessions` 浏览历史，找到相关会话后调 `search_session` 检索细节

### 跨工具协作流程

```
工具 A（如 OpenCode）完成任务 → 用户说"保存会话" → save_session
                                                        ↓
工具 B（如 Claude Code）开始新任务 → list_sessions → search_session → 获取上下文
```

---

## 注意事项

- **不要过度存储** — 只存有长期价值的信息
- **不要存敏感信息** — 密码、token、密钥等绝不存入记忆；确需在一句话中混合普通信息和敏感片段时，用 `<private>...</private>` 包住敏感片段，MCP Server 会在写入前强制过滤
- **不要绕过冲突保护** — 更新默认携带 `expected_content_hash`，只有明确要求强制覆盖时才使用 `allow_unsafe_overwrite=true`
- **搜索结果仅参考** — 如果记忆内容与用户当前描述矛盾，以用户当前描述为准
- **静默执行** — 记忆操作在后台进行，不需要每次都告知用户"我存了一条记忆"
- **会话存档非实时** — `save_session` 只在有意义时调用，不要每轮对话都调
