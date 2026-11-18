# Codex 会话导出方案

状态：首版已实现；命令已接入 uv workspace 和聚合安装包。

日期：2026-10-29

实施清单：[codex-session-export-implementation.md](codex-session-export-implementation.md)。

## 1. 目标与范围

提供 `codex-session-export` 命令，从本机 Codex 数据目录导出会话，支持按项目目录筛选。**每个会话统一提供一份标准 `rollout.jsonl`，使用方不需要关心底层记录在文件还是数据库。** 原始 JSONL 和数据库记录可以同时保留在 `raw/`，元数据单独记录。

首版包括：

- 所有本地可发现会话，包括归档会话、子代理会话和缺失正文的索引记录。
- 按会话工作目录 `cwd` 筛选，默认包含子目录；支持精确目录和多个目录。
- 文件、数据库两种来源均通过适配和关联，生成同一种 rollout 事件结构；不是把数据库行简单改名为 rollout。
- 原始 rollout 字节快照和数据库原始行放到 `raw/`，用于溯源，不能替代标准文件的交付。
- 每个选中会话生成 `metadata.json`；每次运行生成独立批次及 `manifest.json`。

首版不访问云端、不恢复已删除数据、不生成 Markdown 对话、不下载图片或复制消息引用的文件，不恢复完整代码仓库，不做增量同步或覆盖旧批次。

## 2. 调研依据与数据边界

2026-10-29 的本机只读调查结果如下，仅用于解释设计，不作为运行时固定数量：

| 数据情况 | 会话数 |
| --- | ---: |
| 索引总数 | 820 |
| 有数据库轮次记录 | 755 |
| 有原始 rollout 文件 | 67 |
| 两类历史都有 | 64 |
| 只有数据库历史 | 691 |
| 只有原始 rollout | 3 |
| 两类历史都未找到 | 62 |

本机索引包含 3 个归档会话，数据库有其历史，归档目录中没有 JSONL。因此需要把数据库中的结构化消息转换为 rollout 事件。数据库的 `item_json` 是 `userMessage`、`commandExecution` 等条目，不是已经带有 `timestamp/type/payload` 的原始事件行。

本方案保证标准输出格式及可恢复内容的转换，不声称逐字节还原历史原文件，也不承诺输出可直接用于 `codex resume`。无法恢复的信息记录到元数据，不伪造原始指令、推理正文、工具参数或发生时间。

本机确认的数据源：

| 数据源（相对 Codex home） | 首版用途 |
| --- | --- |
| `state_5.sqlite` 的 `threads` | 会话 ID、标题、cwd、时间、归档、模型、Git、rollout 路径等 |
| `state_5.sqlite` 的 `thread_spawn_edges` | 可用时补充父子关系 |
| `thread_history_1.sqlite` 的 `thread_turns`、`thread_items` | 转换轮次、时间和结构化消息为标准 rollout |
| `sessions/**/*.jsonl` | 未归档原始记录 |
| `archived_sessions/**/*.jsonl` | 归档原始记录 |

`history.jsonl`（Codex home 下的输入历史）和 `session_index.jsonl` 不作为完整对话来源；首版不导出 Goal 数据库、应用全局设置、认证文件、缓存和其他辅助数据库。下游只需要消费标准 `rollout.jsonl`，不需要另外兼容数据库导出格式。

这些数据库是内部存储，不是承诺稳定的公开协议。首版通过明确支持的文件及表结构适配；发现不支持的新版本时报告，不能按文件名数字最大值盲选。官方也提供线程及轮次分页读取接口，可作为以后适配路线，首版不启动 App Server：[官方文档](https://learn.chatgpt.com/docs/app-server)。

## 3. 命令定义

用法：

```bash
# 全量导出
uv run codex-session-export --output ~/Downloads/codex-export

# 指定项目目录，包含子目录
uv run codex-session-export \
  --directory ~/Code/my-py-utils \
  --output ~/Downloads/codex-export

# 只匹配该目录本身
uv run codex-session-export \
  --directory ~/Code/my-py-utils --exact-directory \
  --output ~/Downloads/codex-export

# 多目录取并集
uv run codex-session-export \
  --directory ~/Code/project-a --directory ~/Code/project-b \
  --output ~/Downloads/codex-export

# 指定数据源和会话
uv run codex-session-export \
  --codex-home ~/.codex --session <session-id> \
  --output ~/Downloads/codex-export
```

| 参数 | 规则 |
| --- | --- |
| `--output PATH` | 必填；批次的父目录，可自动创建 |
| `--codex-home PATH` | 优先于非空 `$CODEX_HOME`，最后回退 `~/.codex` |
| `--directory PATH` | 可重复；多个目录取并集；省略时不按目录筛选 |
| `--exact-directory` | 只匹配目录本身；未传 `--directory` 时为参数错误 |
| `--session ID` | 可重复；ID 之间取并集，与目录条件取交集 |

归档与未归档、父代理与子代理统一参与筛选，没有默认隐藏条件。显式指定但无法发现的会话 ID 记为请求错误并返回退出码 1；存在但不符合目录条件的会话只计为筛选排除。普通目录筛选零命中仍生成清单并返回 0。

## 4. 目录筛选语义

筛选依据是会话的 `cwd`，不是会话曾读取的文件、命令的临时执行目录、Git 远端、项目名称或父会话目录。

### 4.1 cwd 来源

1. 优先使用 `threads.cwd` 中非空且有效的绝对路径。
2. 缺失或无效时回退到选定 rollout 的首个、且 ID 匹配的 `session_meta.cwd`。
3. 两者都不可用时 `cwd` 为 `null`；目录筛选时排除并统计，无目录筛选时仍导出。
4. 来源冲突时以数据库为准，保留候选值和冲突告警；不把两个 cwd 同时用于匹配。

### 4.2 路径规则

- 用户输入展开 `~`，相对路径按命令启动目录转为绝对路径，规范化 `.`、`..` 和多余分隔符。
- 会话记录中的相对 cwd 不基于导出器的当前目录猜测，视为无效并尝试回退。
- 使用路径组成部分比较；默认是目录相等或位于其子树，精确模式只接受相等。
- 采用词法规范化，不解析符号链接，不进行大小写折叠；符号链接别名和不同大小写不自动视为同一路径。
- 筛选目录无需实际存在，允许导出已删除或迁移项目的历史；不解析 Git worktree 归属。
- 多目录匹配后按会话 ID 去重。子代理使用自己的 cwd，父会话命中不自动带入目录外的子代理。

| 筛选目录 | 会话 cwd | 默认匹配 | 精确匹配 |
| --- | --- | --- | --- |
| `~/Code/project` | `~/Code/project` | 是 | 是 |
| `~/Code/project` | `~/Code/project/packages/a` | 是 | 否 |
| `~/Code/project` | `~/Code/project-backup` | 否 | 否 |
| `~/Code/project` | 未知 | 否 | 否 |

表中的 `~` 是展示简写，实际匹配使用规范化后的绝对路径。

## 5. 发现、合并和读取

1. 检查数据源及可识别的表结构，打开 SQLite 只读连接。
2. 合并 `threads`、历史表中的 thread ID、两个会话目录扫描结果，以及索引明确指向的 rollout 文件。去重以记录中的会话 ID 为准，不能用标题去重。
3. JSONL 文件从 `session_meta` 提取 ID；缺少可用 ID 的文件列入批次 `discovery_errors`，不捏造会话 ID。
4. 同一会话发现多个文件时全部保存至 `raw/rollouts/`。数据库引用且 ID 一致的文件为首选事件来源，否则按规范化路径排序选取；其他来源继续参与关联与补齐，不直接拼接成重复历史。
5. 数据库引用文件的 ID 不一致时不将其归入该会话，并报告错误。读不到元数据的候选文件仍保留发现错误，不当成正常缺失。
6. 确定 cwd 并执行筛选；只为选中会话读取完整历史、导出文件。
7. 建立统一的会话/轮次/条目中间模型，按 session、turn、item、call ID 关联原生事件与数据库记录。原生已有内容优先复用，数据库补齐缺失条目及明确更新的状态。不能按文本去重，否则会误删用户重复发送的消息。
8. 相同条目仅保留一个对应视图；明确更新的完成状态优于旧进行中状态，但失败不能改成成功。无法确认的 ID、内容或顺序冲突保留在 raw，标为 partial，不任意覆盖或假装恢复原始顺序。
9. 两种适配器均输出统一中间模型，经同一序列化器生成标准 rollout。校验成功后原子发布，再写元数据及批次清单。

数据库缺失是可报告的数据可用性状态，可以继续使用其他来源。数据库存在但损坏、不可读或结构不支持是错误，允许继续导出可用来源，但退出码必须体现失败。若没有任何可识别数据源，属于启动失败。

## 6. 输出契约

### 6.1 批次与会话目录

```text
<output>/
  20261029T080000123456Z-<随机后缀>/
    manifest.json
    sessions/
      <session-id>/
        rollout.jsonl             # 唯一标准消费入口
        metadata.json            # 来源、完整性、转换说明
        raw/                     # 按实际可用来源生成
          rollouts/
            001.jsonl            # 原始文件字节快照
            002.jsonl
          database.jsonl         # 该会话原始数据库行
```

批次通过排他创建避免同一时刻运行冲突，不覆盖旧批次。正常会话 ID 原样作为目录名；含分隔符、`.`、`..` 等不安全 ID 使用固定前缀加 SHA-256 的目录名，并在清单中保留原始 ID 与路径映射。

只要可转换，文件来源、数据库来源、混合来源均生成同一格式的 `rollout.jsonl`。只有索引时可生成仅含一条 `session_meta` 的合法文件，明确标记 `metadata_only`，不能当成已恢复对话。标准文件校验失败时不发布，保留 raw 和错误。

`raw/database.jsonl` 每行使用 `{"table":"thread_items","row":{...}}`，原始 `item_json` 字符串保持原值；只导出选中会话的行，不复制整个数据库。所有清单和元数据中的产物路径相对批次根目录。

### 6.2 metadata.json

```json
{
  "schema_version": 1,
  "session": {
    "id": "<session-id>",
    "title": "会话标题",
    "created_at": "2026-10-29T06:00:00Z",
    "updated_at": "2026-10-29T07:00:00Z",
    "cwd": "/path/to/project",
    "archived": false,
    "source": "cli",
    "model": "<model>",
    "parent_thread_id": null,
    "git": {"branch": "main", "commit": "<sha>", "repository_url": null}
  },
  "selection": {
    "cwd_source": "state_db",
    "normalized_cwd": "/path/to/project",
    "matched_directories": ["/path/to/project"]
  },
  "export": {
    "exported_at": "2026-10-29T08:00:00Z",
    "status": "ok",
    "rollout": {
      "format": "codex-rollout-export-v1",
      "status": "generated",
      "source_mode": "database",
      "completeness": "available_content",
      "file": "sessions/<session-id>/rollout.jsonl",
      "bytes": 123456,
      "sha256": "<hash>",
      "record_count": 120,
      "message_count": 10
    },
    "raw_sources": [],
    "record_sources": [],
    "missing_information": [],
    "unconverted_records": [],
    "warnings": [],
    "errors": []
  }
}
```

补充约定：

- `session` 可补充模型服务商、推理强度、客户端版本、项目 ID、权限配置和索引已有 Token 统计；缺失字段为 `null`，不推算费用或完整性。
- 标题优先取非空 `name`，其次 `title`。毫秒时间列优先于秒时间列，标准输出统一 UTC ISO 8601；原始数据库时间数值在 raw 中保持不变。
- `selection` 额外记录 cwd 候选及冲突；无目录条件时 `matched_directories` 为空。
- `source_mode` 为 `file/database/mixed/metadata`，仅追踪来源，下游读取逻辑不依赖它。
- `completeness` 为 `available_content`（已转换全部已识别可用正文，不保证恢复全部历史）、`partial`（已知遗漏或歧义）、`metadata_only`。
- `export.status` 为 `ok`、`warning` 或 `error`。已知源不存在是告警；读取、解析或写入失败是错误，不能用缺失掩盖。
- rollout 状态为 `generated` 或 `error`，失败时 file 为 null。raw 保留成功不能抵消标准文件生成失败。
- `raw_sources` 记录源路径、相对产物路径、读取区间、字节上限、摘要和源变化。原始与标准文件摘要分别计算。
- `record_sources` 按输出 ordinal 或连续范围关联原文件行/序号、数据库表及键、转换规则、生成的 ID 或时间。
- `missing_information` 记录来源未保存的信息；转换失败另外进入 `unconverted_records/errors`。错误对象包含稳定的 `code`、`source` 和可读 `message`。

### 6.3 标准 rollout.jsonl

统一格式称为 `codex-rollout-export-v1`，版本记录在 metadata 中。这是本工具的导出契约名称，不声称是官方格式版本号。采用已观测的 Codex rollout 事件结构，UTF-8，每行一个 JSON 对象，末尾换行。

每行固定包含 `timestamp/ordinal/type/payload`：timestamp 为 UTC ISO 8601 毫秒精度，ordinal 从 0 连续编号，type 和 payload 使用原生事件形状。首行且仅一行为 `session_meta`，ID 与 metadata 一致。标准文件不出现 `table/row/record_type/raw_item_json` 等自定义数据库封装或错误行。

对话视图片段示例（完整文件还可包含轮次、工具及结构化条目）：

```jsonl
{"timestamp":"2026-10-29T06:00:00.000Z","ordinal":0,"type":"session_meta","payload":{"id":"example-session","timestamp":"2026-10-29T06:00:00.000Z","cwd":"/path/to/project"}}
{"timestamp":"2026-10-29T06:00:01.000Z","ordinal":1,"type":"response_item","payload":{"type":"message","id":"user-1","role":"user","content":[{"type":"input_text","text":"分析这个项目"}]}}
{"timestamp":"2026-10-29T06:00:02.000Z","ordinal":2,"type":"response_item","payload":{"type":"message","id":"assistant-1","role":"assistant","content":[{"type":"output_text","text":"项目包含三个模块。"}]}}
```

数据库映射规则：

| 来源 | 标准事件 |
| --- | --- |
| 会话属性 | `session_meta`，已知属性映射原生字段，未知可选属性省略 |
| 已记录轮次开始 | `event_msg/task_started` |
| `userMessage` | `response_item/message`、role=user；文本为 input_text，支持的图片 URL 为 input_image |
| `agentMessage` | `response_item/message`、role=assistant；正文为 output_text，保留可用 phase |
| `reasoning` | `response_item/reasoning`，仅保留已有 summary/content |
| 结构化 item | `event_msg/item_completed`，保留原生 item 类型及对应字段 |
| 轮次完成/失败/中断 | 按已知状态映射 `task_complete`（可含 error）或 `turn_aborted` |

本机原始记录已出现 `item_completed`，其 payload 形状为 `type/thread_id/turn_id/item/started_at_ms/completed_at_ms`。数据库条目转换为原生大写类型及字段：如 `agentMessage → AgentMessage`（content/Text）、`reasoning → Reasoning`（summary_text/raw_content）、`commandExecution → CommandExecution`（argv、cwd URI、aggregated_output/exit_code/duration）、`fileChange → FileChange`（按路径组织 changes）。MCP 调用的 arguments/result/error 通过原生 McpToolCall 保留。该事件表示条目发布，不把工具自身失败/进行中状态改写为成功。无原始函数参数或 call ID 时不猜造 function_call。

首版覆盖已观测的 `userMessage/agentMessage/reasoning/commandExecution/fileChange/mcpToolCall/dynamicToolCall/webSearch/imageView/plan/contextCompaction/collabAgentToolCall/subAgentActivity/sleep`。Skill 引用及只有本地路径的图片保留在结构化用户 item 中，不假装成用户原话或虚构图片数据。

消息可同时有 response_item 对话视图与 item_completed 结构化视图，按相同 item ID 关联，每种视图只生成一次。下游读取对话使用 response_item/message，读取操作使用 item_completed，不能将两个视图算作两条消息。原生来源已有的函数调用/返回、turn_context、compacted 等受支持事件保留对应 payload 信息。

顺序和缺失规则：

- 原生事件保留相对顺序；数据库以 rollout 序号关联轮次和条目。最终连续编号，不按文本去重或仅按时间排序。
- 时间依次采用对应原生事件、数据库消息/轮次、会话创建时间；全缺失使用固定 `1970-01-01T00:00:00.000Z` 占位。所有回退与占位记录到来源映射，不把导出时间充当历史时间。
- 缺少 ID 时只能为已确认的条目生成稳定导出 ID，记录生成依据；不能凭相同文本判断跨来源条目相同。
- 活动轮次不补完成事件；孤立 item 不伪造开始/完成。无法恢复全局交错顺序的片段按固定来源优先级、片段内序号追加，并标记顺序不确定及 partial。
- 系统指令、原始参数、Token 明细、压缩摘要等若源未保存，明确缺失，不拿当前环境填充历史。
- 未知或损坏记录保留 raw、记录 unconverted_records，其余内容可输出合法 partial 文件，退出码 1；不能静默丢弃或把未知对象塞进标准 payload。

实现已包含机器可读 schema、运行时校验器和独立编写的数据库/原生条目样本，检查到 payload 字段与关联，不能只检查 JSON 可解析。文件来源、数据库来源、混合来源必须能被同一读取器处理；相同逻辑会话应获得一致的对话和操作。

### 6.4 manifest.json

记录：

- `schema_version`、批次 ID、工具版本、开始/结束时间、`status`（`in_progress`、`completed`、`completed_with_errors`、`interrupted`）。
- 规范化 Codex home、输出位置、原始及规范化筛选条件、目录匹配模式、请求的会话 ID。
- 数据源文件、表结构适配结果、读取时间、不可用原因；不嵌入数据库全文。
- 发现数量、选中数量、普通排除数量、因 cwd 未知排除数量、标准文件生成/失败数量、三种完整性数量、成功/告警/失败数量。
- 每个选中会话的 ID、metadata 相对路径、来源模式、完整性、标准文件状态；失败时可记录尚未生成的 metadata。
- `discovery_errors`、未找到的请求 ID、批次错误及告警。

计数必须可核对：选中数等于标准文件生成与失败数之和，也等于成功、告警、失败数之和；三种完整性计数之和等于已生成标准文件数。未识别会话 ID 的文件单独计入发现错误，不充当会话。

## 7. 一致性、标准化与失败处理

- SQLite 使用 `mode=ro` 和读事务读取，包含 WAL 中已提交数据；不直接复制主数据库、不使用忽略 WAL 的读取方式。每个数据库采用自己的快照并记录读取区间，不承诺跨数据库与 JSONL 的全局同一时刻快照。
- 原始文件打开后固定长度，将该范围全部字节保留到 raw，包括末尾片段。标准文件由有效记录重新序列化，不能要求其与 raw 字节相同。
- 有效但无换行的最后一条 JSON 可规范化输出；未写完的末尾片段仅存 raw，记录截断告警及 partial。中间损坏记录保留 raw 并记转换错误，其余有效记录可形成标准 partial 文件。
- 文件追加不追赶新内容；截断或替换导致读取不可信时记录错误。摘要只覆盖实际导出的字节，不能拿截取前缀的摘要声称完整文件一致。
- 每个文件先写同目录临时文件再原子改名。初始化 `in_progress` 清单，逐会话更新，最后写终态；中断尽力写 `interrupted`。进程被强制结束后残留的 `in_progress` 也明确表示批次未完成。
- 单会话错误不阻断其他会话；无法写 metadata 时错误写入批次清单。输出磁盘不可写等全局错误终止并向 stderr 报告，不能承诺磁盘故障时仍能落盘完整清单。
- 输出位置不得位于 Codex home 内，避免扫描自生成记录。只读取会话数据，不修改源会话或恢复/启动会话。

| 退出码 | 含义 |
| --- | --- |
| `0` | 可用内容已按标准格式输出；可包含明确说明的源缺失、metadata_only、活动尾部截断告警或零命中 |
| `1` | 发现、读取、转换、校验、写入错误，未转换记录、合并歧义或显式 ID 未找到；保留其他成果 |
| `2` | 参数无效、Codex home 不存在或无任何可识别来源，无法开始 |
| `130` | 用户中断，尽力落盘中断状态 |

## 8. 验收目标

- 指定目录可导出该目录及子目录会话，精确模式、多目录并集和会话 ID 交集符合第 4 节。
- 同前缀兄弟目录不误匹配；删除目录、未知 cwd、符号链接别名都有确定行为。
- 文件、数据库和混合来源均输出统一的标准 rollout，使用方不需要分支解析原始存储。
- 相同逻辑会话在两种来源下，通过同一读取器得到一致的消息与操作，不重复统计两种事件视图。
- raw 快照字节及摘要可核对，标准文件单独校验 schema 与摘要；只有 raw 成功不算标准导出成功。
- 归档、子代理、只有文件、只有历史表的会话不会因枚举方式遗漏。
- 元数据与批次计数一致，源缺失、损坏和写入失败可区分。
- 测试仅使用人工构造数据，不将真实会话或导出内容提交到仓库。

## 9. 首版实现说明

- 实现位于 `packages/codex-session-export`，聚合安装包已重新导出命令。
- 原生条目采用已实查的 `UserMessage/AgentMessage/CommandExecution` 等类型和字段。数据库的 camelCase 投影由 `items.py` 转换；不能直接把数据库 item_json 当成原生 item。
- 命令 argv 从数据库记录的显示命令按 POSIX shell 引号规则解析，并在来源映射标注重建；解析失败明确报错。已有原生 argv 优先保留。数据库毫秒耗时与原生纳秒耗时在同一毫秒内视为投影精度差异，保留原生精度。
- 数据库文件新增/删除记录若只有 diff，保留 unified_diff，不能假装恢复完整文件内容。计划、等待分别以本导出契约的 Extension/plan、Extension/sleep 记录；原生网页查询使用 Extension/web.search。
- 一个会话按需载入内存，未选中的会话只读取发现所需的元数据；特别大的单会话仍会占用相应内存。
- 已完成文件/数据库/混合来源一致性、目录筛选、WAL、中断、损坏和并发读取等自动化测试，并对本项目两个真实会话（数据库独有、混合来源）做临时目录导出验证。实际导出资料没有提交到仓库。
