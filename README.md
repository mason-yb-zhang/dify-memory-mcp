# dify-memory-mcp

基于 Dify 知识库的 AI Agent 持久化记忆 + 跨工具会话共享 MCP Server。

## 这是什么

让你的 AI 助手（OpenCode / Claude Code / Cursor / Codex）拥有**跨会话记忆**和**跨工具会话共享**能力：
- **记忆**：AI 自动记住你的偏好、决策、环境信息，下次对话时自动回忆
- **会话共享**：在 OpenCode 里的对话可以存档，切换到 Claude Code 后检索继续

## 特性

- **零本地基础设施** — 不需要 Docker、数据库、GPU，纯云端
- **混合检索** — 向量 + 全文 + Rerank 三级检索，精确且语义兼顾
- **细粒度 CRUD** — 单条记忆增删改查
- **双层记忆** — 短期（可衰减）+ 长期（永不删除）
- **会话存档** — 全量上传 + 增量追加 + 消息级来源标注
- **跨工具共享** — 配置相同 MCP Server 的工具天然互通
- **隐私标签保护** — `<private>...</private>` 内容写入前强制过滤
- **OpenCode 自动注入** — 可选插件在会话上下文中自动注入相关 Dify 记忆
- **一条命令安装** — 自动完成所有配置，无需手动操作

## 架构

```
Agent ←→ MCP stdio ←→ server.py ←→ Dify API ←→ 向量存储(云端)
                                              ←→ Embedding(内置)
                                              ←→ Rerank(内置)
```

## 安装

### 从 Skill 市场安装（推荐，零门槛）

- **MiCodeHub**：https://agentichub.mioffice.cn/skills/3584
- **Agentic 市场**：https://agentic.mioffice.cn/main-claw-skill-market/skillmarket/list/detail/skill_emhhbmd5YW5iaW4z_b2ZmaWNlLWFnZW50LW1lbW9yeQ?versionTag=stable

通过市场安装无需 SSH key、Git clone 或手动下载，按市场页面提示操作即可。

---

### 从源码安装

#### 前置条件

- Python 3.10+
- Dify API Key

### 获取 API Key

1. 打开 https://cloud.dify.ai/datasets
2. 右上角点击「设置」→「API」
3. 复制 API Key（格式：`dataset-XXXXXXXXXXXX`）

### 一行指令安装（在 AI 对话中贴入）

不需要提前获取 API Key，脚本会自动打开浏览器引导你：

```
帮我安装 dify-memory-mcp 记忆插件：
1. pip install mcp httpx -q
2. 下载 https://git.n.xiaomi.com/zhangyanbin3/ai-skills/-/archive/main/ai-skills-main.zip 到临时目录并解压（已存在则跳过）
3. 运行其中的 dify-memory-mcp/scripts/install.py
```

如果你已经有 API Key，直接运行：

```bash
python scripts/install.py --api-key dataset-YOUR_KEY_HERE
```

脚本会用这个 API Key 自动找到对应的知识库，并创建所需的三个 Document。

如果你明确知道 dataset UUID，也可以一起传入：

```bash
python scripts/install.py --api-key dataset-YOUR_KEY_HERE --dataset-id xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

### 安装流程

1. 安装 Python 依赖（mcp, httpx）
2. 未传 API Key → 自动打开浏览器到 Dify 设置页，AI 会提示你粘贴 Key
3. 验证 API Key → 创建知识库（已存在则复用）
   - 记忆知识库（短期文档 + 长期文档 + 会话索引文档）
   - 会话存档知识库（独立 dataset）
4. 复制 Skill 文件到 `~/.config/opencode/skills/dify-memory-mcp/`
5. 注入 MCP Server 配置到 `opencode.json`（含全部 6 个环境变量）
6. 安装并注册 OpenCode 自动记忆注入插件（仅 OpenCode）
7. 追加记忆行为规则到 `AGENTS.md`

**一次安装，全部 12 个工具就绪。** 会话共享功能已配置好但不会自动上传任何内容——只有你主动说"保存会话"时才会触发首次存档。

### 安装完成后

**重启 OpenCode** 即可生效。

### 更新到最新版本

贴入与安装相同的指令即可。脚本检测到已安装时会自动进入更新模式——刷新 skill、MCP 脚本和 OpenCode 注入插件，不重新创建知识库，也不会覆盖已有的插件配置。更新模式会校验 `mcp.memory.environment`，发现缺失时明确列出缺失变量。

```
帮我更新 dify-memory-mcp 记忆插件：
1. pip install mcp httpx -q
2. 下载 https://git.n.xiaomi.com/zhangyanbin3/ai-skills/-/archive/main/ai-skills-main.zip 到临时目录并解压（已存在则删除重新下载）
3. 运行其中的 dify-memory-mcp/scripts/install.py
```

更新后脚本会检测并提示：
- 如果缺少长期记忆配置 → 提示 `--api-key` 重新运行
- 如果缺少 Session Archive 配置 → 提示可选重新运行 `install.py --api-key <KEY>`

**重启 AI 工具**后新版本生效。

### 手动安装（不通过 AI 对话）

```bash
pip install mcp httpx
python scripts/install.py --api-key dataset-YOUR_KEY_HERE
```

## 能力总览

### 记忆管理（7 个工具）

安装完成后自动获得：

| 工具 | 说明 |
|------|------|
| `add_memory` | 存储新记忆，`permanent=true` 存为长期记忆（永不衰减） |
| `search_memory` | 语义搜索记忆（hybrid_search，默认关闭 rerank），覆盖短期和长期 |
| `update_memory` | 按 segment_id 更新记忆内容（自动检测短期/长期文档） |
| `delete_memory` | 按 segment_id 删除记忆（自动检测短期/长期文档） |
| `list_memories` | 分页浏览记忆，支持 `storage` 参数（short_term/long_term/all） |
| `decay_memories` | 清理冷短期记忆（永不触碰长期记忆） |
| `promote_memory` | 将短期记忆升级为长期（永久保留） |

---

### 会话共享能力：Session Archive（5 个工具，v1.4.0+）

安装时自动配置，无需额外操作。功能默认就绪，但**不会自动上传任何会话**——只有你主动说"保存会话"时才会触发首次存档，之后随记忆自动增量同步。

| 工具 | 说明 |
|------|------|
| `save_session` | 存档当前会话（首次全量上传，后续增量追加新消息） |
| `list_sessions` | 浏览已存档会话索引 |
| `search_session` | 全文检索所有已存档会话内容 |
| `get_session_detail` | 按 segment_id 读取会话片段全文 |
| `delete_session` | 删除已存档会话（Document + 索引，不可逆） |

**环境变量（install.py 自动配置）：**

| 变量 | 说明 |
|------|------|
| `DIFY_SESSION_INDEX_DOCUMENT_ID` | 会话索引文档 ID |
| `DIFY_SESSIONS_DATASET_ID` | 会话全量存储知识库 ID |

> 如果是从 v1.3.0 或更早版本升级，重新运行 install.py --api-key 即可自动补充这两个配置。

---

### OpenCode 自动记忆注入（v1.5.0+）

安装脚本会额外安装 `dify-memory-inject.js` 到 `~/.config/opencode/plugins/`，并注册到 `opencode.json` 的 `plugin` 数组。插件不会替代 MCP 工具，而是在模型调用前把相关 Dify 记忆注入系统上下文：

| 注入内容 | 时机 | 说明 |
|----------|------|------|
| User/Profile | 每个 session 首轮 | 用户偏好、工作习惯、常用环境 |
| Project/Context | 每个 session 首轮 | 当前项目/目录相关记忆 |
| Relevant | 命中关键词时 | 按当前用户消息检索相关记忆 |

默认配置位于 `~/.config/opencode/dify-memory-plugin.jsonc`。更新脚本不会覆盖已有配置。

### 压缩后自动记忆提炼（v1.6.0+）

OpenCode 成功压缩后，插件在自动继续前读取完整会话时间线，使用临时子会话提炼 durable memory，并将候选内容写入 Dify。提炼过程会过滤 `<private>...</private>`、常见凭据格式和重复候选；失败只写入脱敏日志，不阻塞主会话。

该功能默认开启，可在配置中关闭：

```json
{
  "postCompactionMemory": {
    "enabled": true,
    "maxCandidates": 8,
    "minMessages": 4,
    "maxTranscriptChars": 240000
  }
}
```

这项功能负责提炼记忆，不等同于完整会话存档。需要找回全部原文时，仍应使用 Session Archive 的 `save_session` / `search_session`。

---

### 隐私标签保护（v1.5.0+）

写入记忆或手动会话内容时，可以用 `<private>...</private>` 包住敏感内容。MCP Server 会在写入 Dify 前删除标签内文本：

```text
记住服务器路径是 /srv/app <private>token=secret</private>
```

实际写入：

```text
记住服务器路径是 /srv/app
```

如果清洗后没有可存储内容，工具会返回 `skipped: true`，不会创建 segment。

---

### 环境变量总览（install.py 全部自动配置）

| 变量 | 用途 |
|------|------|
| `DIFY_API_KEY` | Dify 知识库 API Key |
| `DIFY_DATASET_ID` | 记忆知识库 ID |
| `DIFY_DOCUMENT_ID` | 短期记忆文档 ID |
| `DIFY_PERMANENT_DOCUMENT_ID` | 长期记忆文档 ID |
| `DIFY_SESSION_INDEX_DOCUMENT_ID` | 会话索引文档 ID |
| `DIFY_SESSIONS_DATASET_ID` | 会话全量存储知识库 ID |

---

### 可选配置：记忆检索模式

记忆工具 `search_memory` 的检索策略可通过修改 `server.py` 中的配置调整：

| 模式 | 配置值 | 适用场景 |
|------|--------|----------|
| **混合检索（默认）** | `hybrid_search` | 兼顾语义理解和精确匹配，推荐大多数场景 |
| 语义检索 | `semantic_search` | 纯向量相似度，适合模糊/联想式搜索 |
| 全文检索 | `full_text_search` | 纯关键词匹配，适合精确查找代码片段/路径 |

**Rerank 模型配置：**

当前默认关闭 rerank。如需开启，修改 `server.py` 中 `search_memory` 的 `retrieval_model` 配置，指定 Dify 租户中可用的 rerank provider 和 model。

> 会话存档的 `search_session` 固定使用 `full_text_search`（无 rerank），因为会话内容以精确匹配为主。

---

### 会话存档更新方式

| 方式 | 说明 |
|------|------|
| **手动触发** | 用户说"保存会话"时首次存档（必须手动） |
| **随记忆自动同步** | 当 `add_memory` 被调用时，若当前会话已存档，自动增量追加新消息 |
| **手动增量追加** | 对已存档会话，手动调用 `save_session` 时只上传新增消息 |

> **首次存档必须手动**，之后只要会话持续产生记忆（Agent 调 `add_memory`），存档就会自动保持同步。无需依赖"会话结束"信号。

**内部自动行为（无需关心）：**
- 首次存档 → 创建新 Document，全量上传
- 有新消息 → 增量追加 segment
- 会话被回退（跳回前几轮重新开始） → 自动检测消息数减少，删除旧文档重建，确保存档与实际一致

**消息级来源标注：**

每条消息在存档时自动标注来源工具和时间：
```markdown
## User
<!-- source=opencode time=2026-06-24 16:36 -->

用户的问题...

## Assistant
<!-- source=claude time=2026-06-24 16:37 -->

回答内容...
```

这使得同一个会话如果从多个工具分别存档，能清晰看到每段内容的来源。

## 工作原理

### 三层存储架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    Memory Dataset（记忆 + 索引）                    │
│                                                                 │
│  ┌─────────────────┐ ┌──────────────────┐ ┌──────────────────┐ │
│  │  短期记忆文档    │ │  长期记忆文档     │ │  会话索引文档     │ │
│  │  agent-memories  │ │  agent-memories-  │ │  session-indexes │ │
│  │                 │ │  permanent        │ │                  │ │
│  │  • 可衰减       │ │  • 永不删除       │ │  • 摘要+指针     │ │
│  │  • 日常信息     │ │  • 关键偏好/决策  │ │  • document_id   │ │
│  │  • hit=0超期删  │ │  • promote 升级   │ │  • message_count │ │
│  └─────────────────┘ └──────────────────┘ └──────────────────┘ │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                Sessions Dataset（全量会话，按需检索）               │
│                                                                 │
│  ┌──────────────────────┐  ┌──────────────────────┐            │
│  │  2026-06-24_会话标题A  │  │  2026-06-25_会话标题B  │  ...      │
│  │  (独立 Document)      │  │  (独立 Document)      │            │
│  │  • 完整对话 markdown  │  │  • 增量追加 segment   │            │
│  │  • 全文检索           │  │  • 消息级来源标注     │            │
│  └──────────────────────┘  └──────────────────────┘            │
└─────────────────────────────────────────────────────────────────┘
```

### AI 自动记忆

安装后 Agent 会自动：
- **对话开始** → 用当前话题搜索相关历史记忆（覆盖短期+长期）
- **发现重要信息** → 自动存储（普通信息存短期，关键决策存长期 permanent=true）
- **定期清理** → 删除从未被检索且超过 30 天的冷短期记忆（长期记忆永不触碰）
- **记忆升级** → 发现某条短期记忆很重要时，promote 升级为长期

### 记忆生命周期

```
写入: Agent 提取事实 → 存入短期/长期文档 → 自动向量索引
读取: 新对话开始 → 语义检索（两个文档） → Rerank → 返回相关记忆
衰减: 定期扫描短期文档 → hit_count=0 且过期 → 删除
升级: promote_memory → 从短期移动到长期 → 永久保留
```

## 技术细节

| 项目 | 说明 |
|------|------|
| Embedding 模型 | `text-embedding-3-small_mi_sys`（Dify 内置） |
| 检索策略 | `hybrid_search`（语义 + 全文混合） |
| Rerank 模型 | 默认关闭（可在 `server.py` 中开启并配置 Dify 支持的 reranker） |
| 存储粒度 | segment 级（1 条记忆 = 1 个 segment） |
| 衰减依据 | `hit_count`（被检索命中次数）+ 创建时间 |
| 默认衰减阈值 | 30 天未命中自动清理 |

## 目录结构

```
dify-memory-mcp/
├── README.md            # 本文件
├── SKILL.md             # Skill 定义（AI 工具识别用）
├── AGENT_RULES.md       # 记忆自动行为规则
├── .gitignore
├── config/
│   └── dify-memory-plugin.jsonc # OpenCode 自动注入插件默认配置
├── plugins/
│   └── dify-memory-inject.js    # OpenCode 自动记忆注入插件
└── scripts/
    ├── install.py       # 一键安装脚本（自动注入配置）
    ├── setup.py         # 交互式安装（手动）
    ├── server.py        # MCP Server 主程序
    ├── session_store.py # 会话存档模块（v1.4.0+）
    └── .env.example     # 环境变量模板
```

## FAQ

**Q: 每个人的记忆是独立的吗？**
是。每个人用自己的 API Key，创建独立的知识库，互不干扰。

**Q: 多个工具之间会共享记忆吗？**
相同 API 配置（API Key + Dataset ID + Document ID）= 共享记忆。比如你的 OpenCode 和 Cursor 配了同一套 Key，它们会读写同一份记忆库。如果岗位号和个人号需要隔离，用不同的 Dify API Key 即可。

**Q: 支持哪些 AI 工具？**
OpenCode、OpenClaw（龙虾）、Claude Code、Claude Desktop、Cursor、Windsurf — 任何支持 MCP 协议的工具。

**Q: 能替代 OpenClaw 的原生记忆吗？**
可以并存互补，也可以完全替代。并存方案更安全（保留 dreaming 和 auto flush），完全替代更省 token 但会失去自动化能力。详见 SKILL.md 中的 OpenClaw 配置章节。

**Q: 记忆存在哪里？**
Dify 云端（公司内部服务），向量化后支持语义检索。不存本地。

**Q: 怎么验证安装成功？**
重启 OpenCode 后说"列出我的所有记忆"，Agent 调用 `list_memories` 即说明生效。

**Q: 怎么重置所有记忆？**
https://cloud.dify.ai/datasets → 删除 `opencode-agent-memory` 知识库 → 重新运行 install.py。

## 更新日志

### v1.6.1（2026-08-29）

**Bug 修复：**
- `server.py` 修复环境变量污染导致的 401：当进程环境中存在非 dataset- 格式的 `DIFY_API_KEY`（如被其他工具注入的 sk- 网关 key）时，旧的"进程环境优先"策略会让它覆盖 `.env` 里有效的 dataset- key，导致该 server 实例所有调用返回 `HTTP 401: Access token is invalid`。
- 现在 `.env` 中的 dataset- 格式 key 优先于环境中非 dataset- 格式的同名变量；环境中正确的 dataset- key 仍然优先生效。
- 新增回归测试 `scripts/test_env_pollution.py`（污染场景端到端验证，search_memory 无 401 泄漏）。

### v1.6.0（2026-07-23）

**新功能：**
- OpenCode 压缩成功后自动读取完整会话时间线，提炼并写入长期有价值的 Dify 记忆。
- 新增临时子会话隔离、压缩快照去重、常见凭据脱敏和失败不阻塞策略。

**安装修复：**
- 更新模式校验 `mcp.memory.environment` 必需变量，不再把不完整配置报告为可用。
- 自动迁移旧的 `mcp.memory.env` 配置到 OpenCode 正确的 `environment` 字段。

### v1.5.0（2026-07-10）

**新功能：**
- 新增 `<private>...</private>` 隐私标签保护，`add_memory`、`update_memory`、`save_session(source="manual")` 写入 Dify 前强制过滤敏感片段
- 新增 OpenCode 自动记忆注入插件 `plugins/dify-memory-inject.js`，按 User/Profile、Project/Context、Relevant 三类向系统上下文注入 Dify 记忆
- 新增插件默认配置 `config/dify-memory-plugin.jsonc`，安装后复制到 `~/.config/opencode/dify-memory-plugin.jsonc`

**安装改进：**
- `install.py` 会复制插件文件并注册到 OpenCode `plugin` 数组
- 更新模式会刷新插件代码，但不会覆盖用户已有的插件配置

### v1.4.5（2026-07-09）

**安装修复：**
- `install.py` 现在会刷新已安装的 `AGENT_RULES.md` 内容，而不是检测到旧规则后直接跳过
- 为全局 `AGENTS.md` 中的记忆规则块增加 managed markers，后续更新可安全替换

### v1.4.4（2026-07-09）

**可靠性修复：**
- `install.py` 安装时补齐 `session_store.py`，确保 Session Archive 工具可注册
- 配置文件写入改为临时文件 + `os.replace`，降低配置损坏风险
- `save_session` 上传失败不再返回假成功；会话回退重建改为先建新文档再删旧文档
- `delete_session` 和 `delete_memory` 强化删除结果判断，避免误报删除成功
- `get_session_detail` 新增可选 `document_id` 快速路径
- 清理失效的会话归档配置提示，更新返回字段文档

### v1.4.3（2026-06-25）

**新功能：**
- `install.py` 新增 Claude Desktop 自动检测（`%LOCALAPPDATA%\Claude-3p\claude_desktop_config.json`）
- 现支持 6 个工具自动配置：OpenCode / Claude Code / Claude Desktop / Cursor / Windsurf / OpenClaw

**Bug 修复：**
- `session_store.py` `_dify_request` 修复 DELETE 返回非 dict 时 `.get()` 崩溃

### v1.4.2（2026-06-24）

**新功能：**
- `install.py` 自动检测本机所有已安装的 AI 工具，一次性全部配置
- 每个工具使用正确的配置格式（OpenCode 用 `environment` + command 数组，其他用 `env` + command/args 分离）
- MiCodeHub 和 Agentic 技能市场安装入口

**安全修复：**
- 清除 git 历史中的硬编码 API Key（filter-repo）
- 删除记忆库中误存的 4 条含凭据记忆

### v1.4.1（2026-06-24）

**改进：**
- `add_memory` 自动触发已存档会话的增量同步（记忆和会话共享联动）
- `save_session` 支持会话回退检测（消息数减少时自动删除旧文档重建）
- `delete_session` 新工具：删除已存档会话（Document + 索引）
- `install.py` 一次性配置全部 12 个工具（记忆 + 会话共享），无需额外步骤
- 安装/更新完成后输出清晰的能力说明和使用引导

### v1.4.0（2026-06-24）

**新功能：Session Archive — 跨工具会话共享**
- 新增 `save_session` 工具：存档完整会话，支持增量追加（只上传新消息）
- 新增 `list_sessions` 工具：浏览已存档会话索引
- 新增 `search_session` 工具：全文检索所有已存档会话内容
- 新增 `get_session_detail` 工具：按 segment_id 读取会话片段
- 每个会话存储为独立 Document（可独立管理/删除）
- 会话索引存储在 Memory Dataset 的独立文档中（session-indexes）
- 消息级来源标注：每条消息标注 `<!-- source=opencode time=... -->`
- 自动从 OpenCode SQLite DB 提取会话（session→message→part）

**改进：**
- httpx AsyncClient 共享 + event-loop 感知（避免跨 loop 错误）
- SQLite 使用 `?mode=ro` 只读模式，避免锁冲突
- 超大单消息按行边界自动拆分（解决 >12000 字 segment 问题）
- 新增 `session_store.py` 模块，server.py 通过条件导入加载

**环境变量新增：**
- `DIFY_SESSION_INDEX_DOCUMENT_ID` — 会话索引文档 ID
- `DIFY_SESSIONS_DATASET_ID` — 会话全量存储知识库 ID

### v1.3.0（2026-06-18）

**新功能：**
- 知识库检索模式从向量检索升级为**混合检索（向量+全文+Rerank）**
- 新增记忆共享与隔离说明：同一套 API 配置共享记忆，不同 API Key 隔离记忆
- MCP 工具描述增加中文触发词（记住这个/记下来/你还记得吗/永远记住等）

**改进：**
- SKILL.md 按 MIT AI Skill V1.1 规范完整重写（15/15 验证通过）
- 触发条件补充日常记忆操作场景（不再仅限安装配置）
- `decay_memories` 容器文档检测扩展为 3 种前缀匹配（兼容新旧格式）
- `list_memories` 返回值增加分页说明 note 字段
- Server 退出时 graceful shutdown 关闭 HTTP 客户端
- 版本号从 beta 转为正式版

### v1.2.0（2026-06-17）

**新功能：**
- 双层记忆架构：短期记忆（可衰减）+ 长期记忆（永不删除）
- `add_memory` 新增 `permanent` 参数，直接写入长期存储
- 新增 `promote_memory` 工具，将短期记忆升级为长期
- `list_memories` 新增 `storage` 参数（short_term/long_term/all）
- `search_memory` 结果增加 `permanent` 字段标注来源
- `update_memory`/`delete_memory` 自动检测 segment 所在文档
- 安装指令支持幂等更新（重复执行只刷新文件，不重建知识库）
- 新增 OpenClaw（龙虾）配置说明

**Bug 修复：**
- 修复 Dify DELETE API 返回 `"204\n"` 解析为 int 导致 `.get()` 报错
- 修复 server.py hardcode 作者个人凭据作为 fallback（安全漏洞）
- 修复 install.py `"env"` → `"environment"`（OpenCode 官方字段名）
- 修复 setup.py 无幂等检查导致重复创建知识库
- 修复 `promote_memory` 只查第一页找不到 segment
- 修复 `installed_server` 变量可能未定义的 NameError
- 修复浏览器打开失败时无提示

**改进：**
- httpx AsyncClient 复用 TCP 连接（不再每次新建）
- decay_memories 批量删除加入限流（每批 5 条，间隔 200ms）
- install.py 新增 `find_stable_python()` 排除 venv/tmp 路径
- api_request 捕获 URLError 和通用异常
- 移除 .jsonc 候选（json.load 不支持注释）
- SKILL.md 按 MIT AI Skill 撰写规范 V1.1 重构
- 安装方式从 SSH clone 改为 HTTP archive 下载

### v1.0.0（2026-05-27）

- 初始版本：6 个 MCP 工具，单文档架构

## License

MIT
