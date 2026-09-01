---
name: office-agent-memory
name_zh: AI Agent 跨会话记忆插件
version: 1.6.1
description: |
  帮助 AI Agent 开发者和用户，在需要跨会话持久化记忆时，自动完成记忆插件的安装、配置和故障排查。
  基于 Dify 知识库混合检索（向量+全文，Rerank 默认关闭），支持语义搜索、细粒度 CRUD、冷记忆自动衰减，零本地基础设施，一条指令完成部署。
  安装后 Agent 自动获得 7 个 MCP 工具：add_memory、search_memory、update_memory、delete_memory、list_memories、decay_memories、promote_memory。
  v1.6.0 Session Archive（5 个跨工具会话共享工具）+ 自动检测并配置所有已安装的 AI 工具（OpenCode/Claude Code/Claude Desktop/Cursor/Windsurf/OpenClaw）。新增隐私标签保护、OpenCode 自动记忆注入，以及压缩完成后的完整会话记忆提炼。
  v1.6.1 修复环境变量污染导致的 401：非 dataset- 格式的 DIFY_API_KEY（如 sk- 网关 key）不再覆盖 .env 凭证。
scope: cn
not_for:
  - 不负责 Dify 知识库的通用管理（创建/删除知识库、文档上传等，那是 dify-knowledge-base skill）
  - 不负责 Dify 工作流编排（那是 dify-dify-guide / dify-workflow-dsl skill）
  - 不负责其他 MCP Server 的安装配置
examples:
  - 帮我安装 dify-memory-mcp 记忆插件
  - 安装记忆插件并认证
  - 记忆 MCP Server 启动报错 Missing environment variables 怎么办
  - 记忆工具全部报 401 unauthorized 怎么办
  - 帮同事部署跨会话记忆系统
  - 为 OpenClaw 配置记忆插件
  - 保存当前会话
  - 查看我存档的会话列表
  - 搜索之前会话中关于 Creo 骨架模型的讨论
  - 删除那个测试会话
---

# 前置条件

| 依赖 | 用途 | 验证方式 |
|------|------|----------|
| Python 3.10+ | 运行 install.py 和 server.py | `python --version` |
| pip 包：mcp, httpx | MCP Server 运行时依赖 | `pip show mcp httpx` |
| Dify API Key | 访问 Dify Knowledge API | `curl https://api.dify.ai/v1` |

如果依赖缺失，安装脚本会自动处理 pip 包安装；内网不通则直接报错终止。

# 触发条件

满足以下任一条件时触发：

**安装与配置场景：**
- 用户要求安装/配置记忆插件、部署记忆系统
- 排查记忆 MCP Server 问题（报错、环境变量缺失）
- 配置记忆隔离（多空间、岗位号与个人号分开）

**日常记忆操作场景：**
- 用户说"记住这个"、"记下来"、"记录一下"、"别忘了"
- 用户说"你还记得吗"、"之前说过什么"、"回忆一下"
- 用户分享关键信息并要求记忆（偏好、决策、环境配置）
- 用户说"永远记住"、"这个很重要"

**会话存档场景：**
- 用户说"保存会话"、"存档对话"、"同步这个会话"
- 用户说"查看存档会话"、"有哪些历史会话"
- 用户说"搜索之前的对话"、"在历史会话里找 XXX"
- 用户说"删除那个会话"、"把这个存档删掉"

# 工具速查表

### 安装脚本

| 脚本 | 功能 | 用法 |
|------|------|------|
| `scripts/install.py` | 非交互式自动安装 | `python install.py --api-key <KEY>` |
| `scripts/setup.py` | 交互式安装（人工运行） | `python setup.py` |

### MCP Server 提供的工具（安装后自动可用）

| 工具 | 功能 | 关键参数 |
|------|------|----------|
| `add_memory` | 存储新记忆 | content, keywords, permanent |
| `search_memory` | 语义搜索记忆 | query, top_k |
| `update_memory` | 更新已有记忆 | segment_id, content |
| `delete_memory` | 删除记忆 | segment_id |
| `list_memories` | 分页浏览记忆 | page, limit, storage |
| `decay_memories` | 清理冷短期记忆 | days |
| `promote_memory` | 短期→长期升级 | segment_id |
| `save_session` | 存档会话（支持增量追加） | session_id, source, title |
| `list_sessions` | 浏览已存档会话索引 | limit, keyword |
| `search_session` | 全文检索会话内容 | query, top_k |
| `get_session_detail` | 读取会话片段全文 | segment_id, document_id（可选） |
| `delete_session` | 删除已存档会话（不可逆） | session_id |

### OpenCode 自动记忆注入（v1.5.0+）

安装脚本会为 OpenCode 额外安装 `plugins/dify-memory-inject.js`，并注册到 `opencode.json` 的 `plugin` 数组。该插件在新会话中自动检索并注入 Dify 记忆：

| 注入类型 | 触发时机 | 说明 |
|----------|----------|------|
| User/Profile | 每个 session 首次系统提示构建时 | 注入用户偏好、工作习惯、常用工具等常驻记忆 |
| Project/Context | 每个 session 首次系统提示构建时 | 注入当前目录/项目相关记忆 |
| Relevant | 用户消息命中关键词时 | 基于当前用户消息检索相关记忆后注入 |

插件配置文件：`~/.config/opencode/dify-memory-plugin.jsonc`。安装脚本只在配置文件不存在时创建默认模板，避免覆盖用户自定义配置。

### 隐私标签保护（v1.5.0+）

`add_memory`、`update_memory` 和 `save_session(source="manual")` 在写入 Dify 前会统一删除 `<private>...</private>` 内容。若清洗后没有可存储内容，则返回 `skipped: true`，不会创建 Dify segment。

### 压缩后自动记忆提炼（v1.6.0+）

OpenCode 成功压缩会话后，插件通过 `experimental.compaction.autocontinue` 读取完整的 `session.messages` 时间线，而不是只读取压缩后的活跃上下文。插件创建临时提炼会话，提取长期有价值的偏好、决策、环境事实和未完成事项，过滤隐私/凭据后写入 Dify，再允许会话继续。

- 只在压缩成功后执行，不改变 OpenCode 的压缩阈值。
- 临时提炼会话不会再次注入 Profile/Project 记忆，完成后自动删除。
- 同一压缩快照只处理一次，Dify 不可用时只记录脱敏日志，不阻塞主会话。
- 默认最多保存 8 条候选记忆，完整会话仍由 Session Archive 负责保存。

# 默认值汇总

| 参数 | 默认值 | 说明 |
|------|--------|------|
| decay_days | `30` | 冷记忆清理阈值（天） |
| search_top_k | `5` | 语义搜索返回条数 |
| list_page_size | `20` | 列表每页条数（最大100） |
| permanent | `false` | 新记忆默认为短期 |
| storage | `all` | list 默认查询全部（short_term/long_term/all） |

# 统一执行流程

## 步骤 1: 安装记忆插件

### 1.1 推荐安装指令

在 AI 对话中贴入：

```
帮我安装 dify-memory-mcp 记忆插件：
1. pip install mcp httpx -q
2. 下载 https://git.n.xiaomi.com/zhangyanbin3/ai-skills/-/archive/main/ai-skills-main.zip 到临时目录并解压（已存在则跳过）
3. 运行其中的 dify-memory-mcp/scripts/install.py
```

### 1.2 API Key 获取流程

**情况 A：用户已提供 API Key（命令中带 --api-key）**

直接执行完成，无需交互。

**情况 B：用户未提供 API Key（默认）**

1. 脚本输出 JSON：`{"needs_api_key": true, "url": "...", "instruction": "..."}`
2. Agent 告知用户：
   > 请打开 https://cloud.dify.ai/datasets ，点击右上角「设置」→「API」，复制 API Key（格式：dataset-XXXX）粘贴给我。
3. 用户粘贴 Key 后，执行：`python install.py --api-key "<KEY>"`

> 为什么需要 API Key：Dify 知识库按用户隔离，每人独立空间，Key 是唯一认证凭据。

### 1.3 安装成功后

告知用户：
- 记忆插件已配置完成
- 需要**重启 AI 工具**激活 MCP Server
- 重启后 Agent 自动获得 7 个记忆工具

## 步骤 2: 故障排除

根据用户描述的错误现象，对照降级处理表定位问题并给出解决方案。

## 步骤 3: 为非 OpenCode 工具安装

```bash
python install.py --api-key "dataset-XXX" --skip-opencode-patch
```

将输出配置手动加入目标工具：

**Claude Desktop / Cursor / Windsurf（`"env"` 字段）：**

```json
{
  "mcpServers": {
    "dify-memory": {
      "command": "<python-path>",
      "args": ["<server-path>"],
      "env": {
        "DIFY_API_KEY": "<KEY>",
        "DIFY_DATASET_ID": "<ID>",
        "DIFY_DOCUMENT_ID": "<ID>"
      }
    }
  }
}
```

**OpenCode（`"environment"` 字段）：**

```json
{
  "mcp": {
    "memory": {
      "command": ["<python-path>", "<server-path>"],
      "enabled": true,
      "type": "local",
      "environment": {
        "DIFY_API_KEY": "<KEY>",
        "DIFY_DATASET_ID": "<ID>",
        "DIFY_DOCUMENT_ID": "<ID>"
      }
    }
  }
}
```

> 为什么字段名不同：OpenCode 用 `"environment"`，其他工具用 `"env"`，混用会导致环境变量无法注入子进程。

## 步骤 4: 为 OpenClaw 配置

在 `~/.openclaw/openclaw.json` 中添加 MCP 配置：

```json5
{
  mcp: {
    "dify-memory": {
      command: "<python-path>",
      args: ["<server-path>"],
      env: {
        DIFY_API_KEY: "<KEY>",
        DIFY_DATASET_ID: "<ID>",
        DIFY_DOCUMENT_ID: "<ID>"
      }
    }
  }
}
```

**推荐方案：并存互补**

| 对比维度 | OpenClaw 原生记忆 | dify-memory-mcp |
|----------|-------------------|-----------------|
| 存储 | 本地文件 | Dify 云端向量库 |
| 检索 | SQLite/LanceDB | hybrid_search + rerank |
| 跨设备 | 不同步 | 云端同步 |
| 跨工具 | 仅 OpenClaw | 所有 MCP 工具共享 |
| 离线 | 完全离线 | 需要内网 |

> 为什么推荐并存：原生记忆提供 session bootstrap（对话开头注入上下文），dify-memory 提供跨工具共享的语义检索层，互补而非互斥。

# 工具定义

## install.py

| 参数 | 类型 | 必填 | 来源 | 说明 |
|------|------|------|------|------|
| --api-key | string | 否 | 用户输入 | Dify API Key（dataset-XXXX），不传则引导获取 |
| --opencode-config | string | 否 | 自动检测 | opencode.json 路径 |
| --python-path | string | 否 | 自动检测 | 写入配置的 Python 路径（排除 venv/tmp） |
| --skip-opencode-patch | flag | 否 | 用户指定 | 不自动修改 opencode.json |
| --skip-skill-install | flag | 否 | 用户指定 | 不复制 Skill 文件 |
| --skip-rules-append | flag | 否 | 用户指定 | 不追加 AGENT_RULES 到 AGENTS.md |

**返回值**：

| 字段 | 类型 | 说明 |
|------|------|------|
| success | boolean | 是否成功 |
| dataset_id | string | Dify 知识库 ID |
| document_id | string | 短期记忆容器文档 ID |
| server_path | string | MCP Server 脚本路径 |
| python_path | string | 写入配置的 Python 路径 |
| patched_tools | list[string] | 已成功注入 MCP 配置的工具名称列表 |
| session_archive_configured | boolean | Session Archive 是否已完整配置 |
| rules_appended | boolean | AGENTS.md 是否已追加规则 |
| opencode_memory_plugin | object | OpenCode 自动记忆注入插件安装/注册结果 |

## MCP Server: add_memory

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| content | string | 是 | 记忆内容（≤200字为佳） |
| keywords | list[string] | 否 | 分类标签（2~5个） |
| permanent | boolean | 否 | 默认 false，true 存为长期记忆 |

## MCP Server: search_memory

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| query | string | 是 | 自然语言搜索词 |
| top_k | integer | 否 | 返回条数，默认 5 |

## MCP Server: list_memories

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| page | integer | 否 | 页码，默认 1 |
| limit | integer | 否 | 每页条数，默认 20，最大 100 |
| storage | string | 否 | `short_term`/`long_term`/`all`，默认 all |
| keyword | string | 否 | 按关键词过滤 |

## MCP Server: decay_memories

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| days | integer | 否 | 清理阈值天数，默认 30 |

## MCP Server: promote_memory

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| segment_id | string | 是 | 要升级的短期记忆 ID |

# 架构说明

```
Agent ←→ MCP stdio ←→ server.py ←→ Dify API ←→ 向量存储(云端)
                                             ←→ Embedding(内置)
                                             ←→ Rerank(内置)
```

| 项目 | 说明 |
|------|------|
| Embedding | text-embedding-3-small_mi_sys |
| 检索策略 | hybrid_search（语义 + 全文）+ rerank |
| 存储粒度 | segment 级（1 条记忆 = 1 个 segment） |
| 双文档架构 | `agent-memories`（短期）+ `agent-memories-permanent`（长期） |
| 记忆衰减 | 仅短期文档，hit_count=0 且超过 N 天 |

## Session Archive 架构（v1.4.0+）

```
Memory Dataset
├── Document: agent-memories（短期记忆）
├── Document: agent-memories-permanent（长期记忆）
└── Document: session-indexes（会话索引，独立管理）
      └── 每条 segment = 一个会话的摘要 + document_id 指针

Sessions Dataset（独立知识库）
├── Document: "2026-06-24_会话标题A"（每会话独立文档）
├── Document: "2026-06-25_会话标题B"
└── ...
```

| 项目 | 说明 |
|------|------|
| 索引存储 | Memory Dataset 的 session-indexes 文档 |
| 全量存储 | Sessions Dataset，每会话一个独立 Document |
| 检索模式 | full_text_search（精确匹配会话内容） |
| 增量追加 | 通过 index 记录 message_count，只追加新消息为 segment |
| 来源标注 | 消息级 `<!-- source=opencode time=2026-06-24 16:36 -->` |
| 跨工具共享 | 所有配置相同 MCP 的工具可见同一份会话索引 |

### 环境变量（Session Archive 额外需要）

| 变量 | 说明 |
|------|------|
| `DIFY_SESSION_INDEX_DOCUMENT_ID` | session-indexes 文档 ID |
| `DIFY_SESSIONS_DATASET_ID` | Sessions 知识库 ID |

## 记忆共享与隔离

**同一套 API 配置 = 共享同一份记忆。** 多个 AI 工具（OpenCode / Cursor / Claude Desktop / OpenClaw）只要配置相同的 `DIFY_API_KEY` + `DIFY_DATASET_ID` + `DIFY_DOCUMENT_ID`，就会读写同一份记忆库。

**需要隔离时：** 为不同身份配置不同的 Dify 工作空间（即使用不同的 `dataset-XXXX` API Key），各自拥有独立的知识库和记忆空间。

典型场景：
- 岗位号（工作机）和个人号分开记忆 → 两套 API Key
- 同一人的多个项目共享记忆 → 同一套 API Key

> 为什么这样设计：基于 Dify 知识库的天然隔离——不同 API Key 对应不同空间，无需额外的多租户逻辑。

# 降级处理

| 错误场景 | 原因 | 影响 | 解决方式 |
|----------|------|------|----------|
| Missing environment variables | opencode.json 中 environment 字段缺失 | Server 启动失败 | 重新运行 `install.py --api-key <KEY>` |
| 搜索无结果 | 新记忆需 2-3 秒完成向量索引 | 刚写入的记忆搜不到 | 等待 3 秒后重试 |
| API Key 无效 | 格式错误或已过期 | 所有操作失败 | 到 Dify 设置页重新获取 dataset-XXXX Key |
| HTTP 401 unauthorized（Key 未过期） | 进程环境被 sk- 格式网关 key 污染，占据 DIFY_API_KEY 变量名 | 该实例所有操作失败 | v1.6.1+ 已自动防御（server 启动时忽略非 dataset- 格式的环境 Key）；旧版本更新 server.py 即可 |
| 浏览器未弹出 | AI 子进程无 GUI 环境 | 无法自动引导获取 Key | 手动打开 URL（脚本 JSON 输出中有地址） |
| Python 路径找不到 | 安装时用了 venv/tmp 路径 | Server 配置指向不存在的路径 | `install.py --python-path <稳定路径>` |
| 知识库重复创建 | 不会发生 | 无 | install.py 自动检测同名知识库并复用（幂等） |
| 字段名混用 | OpenCode 用 environment，其他用 env | 环境变量注入失败 | 检查配置文件字段名是否匹配目标工具 |
| OpenCode 记忆环境变量不完整 | 更新模式只发现 MCP 已启用但缺少必需变量 | MCP Server 启动失败 | 重新运行 `install.py --api-key <KEY>`；更新模式会列出缺失变量 |
| OpenCode 自动注入无效果 | 未重启 OpenCode 或 plugin 未注册 | 只影响自动上下文注入，不影响 MCP 记忆工具 | 确认 `opencode.json` 的 `plugin` 数组包含 `dify-memory-inject.js` 并重启 OpenCode |
| 压缩后没有新增记忆 | 会话没有可持久化信息、Dify 不可用或提炼失败 | 不影响会话压缩和继续 | 检查 `dify-memory-plugin.log` 中的脱敏计数和 HTTP 状态 |

# 开发维护规范

## 安全约束

- ⚠️ **禁止** 在 server.py 中 hardcode 凭据，`os.environ.get()` 默认值为空字符串
- ⚠️ **禁止** 将 .env 文件提交到 Git
- 提醒用户不要将含 API Key 的 opencode.json 提交到版本控制

## 幂等性

- install.py 先搜索已有的 `opencode-agent-memory` 知识库，存在则复用
- AGENTS.md 追加规则前检查 marker `"# Agent Memory Rules"` 是否已存在
- Skill 文件复制使用覆盖模式

## 跨平台兼容

- 路径使用 `os.path.expanduser("~")` + `os.path.join()`
- 浏览器打开优先平台原生命令（`cmd /c start`、`open`、`xdg-open`）
- Python 路径写入时排除 venv/tmp（`find_stable_python()`）

## API 调用规范

- 模块级共享 `httpx.AsyncClient` 复用 TCP 连接
- 批量删除限流：每批 5 条，间隔 200ms
- 所有调用捕获 `TimeoutException` 和 `ConnectError`，返回结构化错误

# 文件清单

| 文件 | 说明 |
|------|------|
| SKILL.md | 本文件，Skill 定义 |
| AGENT_RULES.md | 记忆自动行为规则（追加到 AGENTS.md） |
| scripts/install.py | 非交互式自动安装脚本（Agent 调用） |
| scripts/setup.py | 交互式安装脚本（人工手动运行） |
| scripts/server.py | MCP Server 主程序 |
| scripts/session_store.py | Session Archive 模块（v1.4.0+） |
| scripts/.env.example | 环境变量模板 |
| plugins/dify-memory-inject.js | OpenCode 自动记忆注入插件（v1.5.0+） |
| config/dify-memory-plugin.jsonc | OpenCode 自动注入插件默认配置模板 |
