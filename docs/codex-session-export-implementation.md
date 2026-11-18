# Codex 会话导出实施文档

状态：首版已实现；实施任务与验证结果见下文。

日期：2026-10-29

行为依据：[Codex 会话导出方案](codex-session-export-design.md)。如实施发现存储结构与调研不同，先同步两份文档再调整实现，不能静默改变导出语义。

**标准 `rollout.jsonl` 是必交付产物，原始 JSONL 和数据库行放到 raw 留存，不能用原始资料导出成功代替标准文件生成成功。** 实施采用“来源适配器 → 会话中间模型 → 统一 rollout 序列化器 → 格式校验”，下游无需判断存储来源。

## 1. 交付物与模块

独立 uv workspace 包，Python 3.11+，首版只用标准库，无新增运行时第三方依赖。

```text
packages/codex-session-export/
  pyproject.toml
  README.md
  src/codex_session_export/
    __init__.py
    main.py          # argparse、输出、退出码
    models.py        # 会话/轮次/条目、来源映射及导出结果
    selection.py     # 路径规范化及筛选
    sources.py       # SQLite/JSONL 发现、快照及 raw 留存
    adapters.py      # 原生事件、数据库行转换为统一模型
    items.py         # 数据库投影与原生条目字段映射
    rollout.py       # 统一序列化、校验及标准读取器
    exporter.py      # 批次、元数据、清单及失败处理
    schemas/
      rollout-v1.schema.json
tests/
  fixtures/codex_session_export/
    items.json          # 数据库投影样本
    native-items.json   # 独立编写的原生条目样本
  test_codex_session_export.py
```

入口为 `codex-session-export = "codex_session_export.main:main"`。CLI 保持薄，核心逻辑接受明确路径与配置，不隐式读取真实用户目录。只在出现其他包可复用的逻辑时提取到 `my-py-utils-common`。

## 2. 实施任务

### 阶段 1：标准格式契约与命令骨架

- [x] 创建包、构建配置和命令入口，遵循仓库已有 uv 构建方式。
- [x] 实现 `--output`、`--codex-home`、可重复的 `--directory`、`--exact-directory`、可重复的 `--session`。
- [x] 实现 home 优先级、空参数校验、无目录时禁止精确模式、输出与源目录隔离。
- [x] 定义会话/来源/结果类型，统一错误代码及退出码，不将所有异常吞成成功。
- [x] 固定 `codex-rollout-export-v1`：timestamp/ordinal/type/payload、唯一首行 session_meta、连续序号和各类 payload 校验规则。
- [x] 提交机器可读 schema、标准读取器、人工固定样本；schema 资源纳入安装包。运行时用标准库显式校验支持的类型和字段，并测试与 schema 规则一致。
- [x] 定义 response_item 对话视图和 item_completed 结构化视图的 ID 关联、计数及去重规则。
- [x] 定义时间/ID 回退及来源追踪，不把导出时间或当前环境当成历史事实。

验收：`uv run --package codex-session-export codex-session-export --help` 可运行；参数错误返回 2，help 不读取用户会话。标准读取器接受人工合法样本，拒绝错误 payload、重复 session_meta 和不连续序号。

### 阶段 2：数据源适配与会话发现

- [x] 通过 SQLite 只读 URI、读事务及表结构探测，支持调研确认的 `state_5.sqlite` 和 `thread_history_1.sqlite`。
- [x] 必需字段：`threads.id`；轮次的 `thread_id/turn_id/rollout_ordinal`；消息的 `thread_id/turn_id/item_id/rollout_ordinal/item_json`。其他列缺失按可选字段处理，不能固定 SELECT 所有调研字段。
- [x] 只读取明确支持的表；识别不支持版本和损坏数据库，记录来源错误并继续使用其他可用来源。
- [x] 合并索引 ID、历史表 ID、活动/归档 JSONL 和索引引用路径，避免仅靠数据库或仅靠文件枚举。
- [x] 提取 `session_meta` 的会话 ID、cwd 和辅助字段；无法识别 ID 或 ID 不一致时记录发现错误。
- [x] 按方案处理同 ID 多文件：确定首选来源、全部留存 raw、建立用于后续合并的标识映射。
- [x] 补充可用父子关系，保持归档和子代理可见，不自动沿父子关系扩大筛选范围。

验收：人工构造“仅索引、仅文件、仅历史表、两类都有、归档、多文件”来源，得到去重且可追踪的会话集合；缺失数据库可降级，损坏/未知版本不能被当成不存在。

### 阶段 3：目录与会话筛选

- [x] 用户目录展开 `~`，相对目录按启动 cwd 解析，词法规范化，不调用真实路径解析来折叠符号链接。
- [x] 按数据库 cwd 优先、session_meta 回退确定有效绝对 cwd，保留冲突和来源信息。
- [x] 默认包含目录本身及子树，精确模式只比较相等；比较路径组成部分。
- [x] 多目录 OR、多个 ID OR、目录与 ID AND；过滤后按 ID 去重。
- [x] 目录不要求存在；未知 cwd 单独统计；指定 ID 不存在和目录零命中分别处理。

验收：满足方案第 4 节表格，并通过相对路径、尾部分隔符、`..`、同前缀目录、符号链接别名、已删除目录、多目录重叠和父子代理不同 cwd 的场景。输出标准化不改变目录筛选语义。

### 阶段 4：原始资料留存、适配与标准 rollout 生成

- [x] 原子创建唯一批次目录，生成 `in_progress` 清单，安全映射会话目录名。
- [x] raw/rollouts 保留固定长度的原始字节快照，包括末尾片段，记录读取区间、源变化和独立摘要。
- [x] raw/database.jsonl 保留所选会话的表名和原始行，item_json 字符串保持原值，不复制整库。
- [x] 原生适配器处理消息、函数调用/返回、item_completed、轮次、上下文及压缩等受支持事件，保留对应 payload 信息。
- [x] 数据库适配器覆盖方案第 6.3 节全部已知 item 类型；生成原生 response_item、item_completed 和有依据的轮次事件，不能简单输出数据库行。
- [x] Skill、本地图片引用保留于结构化 item；无原始 call ID/参数时不猜造函数调用，无推理正文不伪造。
- [x] 以 session/turn/item/call ID 关联及去重；不同 ID 的相同文本仍保留，明确的新完成状态优于旧进行中状态。
- [x] 合并缺失条目与可确认的更新状态；内容/顺序歧义保留 raw，标记 partial，不任意覆盖。
- [x] 两种来源用同一模型和序列化器输出标准 rollout，连续编号；相同 item 的每种事件视图最多生成一次。
- [x] 孤立 item 不伪造轮次开始/结束；活动轮次不补完成；时间和生成 ID 的回退依据写入来源映射。
- [x] 未知或损坏记录留 raw 和错误，已知有效内容输出合法 partial 文件；仅索引生成 session_meta 并标记 metadata_only。
- [x] 发布前校验完整标准文件到 payload 层级；失败不发布 rollout，保留 raw 和错误。禁止往标准文件写自定义错误行。
- [x] 通过同目录临时文件和原子改名提交产物，所有文件相对路径可在清单中定位。

验收：同一逻辑会话分别从 JSONL、数据库、混合来源输入，用同一个读取器得到一致的消息和操作。raw 字节可核对，标准文件 schema 一致；混合来源不重复消息或丢失工具结果，不要求标准文件与 raw 字节相同。

### 阶段 5：元数据、汇总与失败恢复

- [x] 按方案生成 metadata，记录 cwd、匹配目录、格式版本、source_mode、完整性、标准文件和 raw 的独立摘要。
- [x] 落实 generated/error 与 available_content/partial/metadata_only，原始资料成功不能抵消标准转换失败。
- [x] 按输出序号或连续范围记录原始行/数据库键、转换规则、生成字段和缺失信息；未转换记录明确报告。
- [x] 汇总数据源状态、筛选条件、计数、发现错误、请求 ID 错误和产物路径。
- [x] 每完成一个会话更新清单；结束写 `completed` 或 `completed_with_errors`；中断尽力写 `interrupted`。
- [x] 实现 0/1/2/130 退出码及 stdout 摘要、stderr 错误；输出不足以落盘时直接报告实际失败。

验收：仅 session_meta 的文件明确标为 metadata_only，不能当成已恢复对话；标准格式有效与信息完整性分别报告。单会话失败不阻断其他会话，清单数量与实际产物一致，重复运行不修改上次批次。

### 阶段 6：工作区接入与使用文档

- [x] 使用 `uv add --package my-py-utils-cli codex-session-export` 接入聚合依赖，检查生成的 workspace source；通过 uv 更新并提交 `uv.lock`。
- [x] 在聚合包 `[project.scripts]` 中重新导出命令，核对 `[tool.uv.sources]`。
- [x] 更新根 README、聚合包 README 的命令列表及布局，新增包 README。
- [x] 包 README 说明目录匹配、标准 rollout 读取规则、raw 溯源、历史缺失和退出码；下游不需要按底层来源分支解析。
- [x] 验证 schema 已打包，标准读取器能通过 workspace 和聚合安装入口验证同一组人工样本。
- [x] 检查新增产物不含真实会话、源数据库、个人导出文件和生成缓存。

验收：从 workspace 和聚合安装包都能调用该命令，用户按文档可完成按目录导出。

## 3. 必须覆盖的验收场景

测试使用 pytest 临时目录和标准库创建的最小 SQLite 数据库、手写 JSONL。WAL 场景保留数据库写连接，验证读取已提交但未 checkpoint 的数据。不要依赖真实 Codex 安装或网络。

| 场景 | 预期 |
| --- | --- |
| 项目根目录与子目录会话 | 默认都导出，精确模式仅根目录 |
| `project-backup` 与 `project` | 不误匹配 |
| 多目录重叠、多个 ID | 并集去重，与另一类条件取交集 |
| 目录已删除或含 `..` | 可词法匹配，无需创建历史目录 |
| 符号链接别名、大小写差异 | 不自动合并为同一路径 |
| DB cwd 缺失、无效或与 JSONL 冲突 | 正确回退/优先级，记录来源及告警 |
| cwd 完全未知 | 有目录条件则排除并统计，无条件则保留 |
| 归档、父子会话跨目录 | 各按自身 cwd 筛选，不隐式扩展 |
| 只有原始文件、只有数据库历史 | 都生成同一 schema 的标准 rollout，原始资料另存 raw |
| 同一逻辑会话的两种来源 | 一个读取器得到相同对话/操作内容，不依赖 source_mode |
| 两种来源同时存在 | 合并补齐，按 ID 去重，每种事件视图仅一次 |
| 相同文本的不同消息 | 不误去重 |
| 只有元数据 | 生成合法 session_meta，metadata_only，消息数为 0 |
| 历史表存在但索引中无该 ID | 仍发现会话，cwd 未知规则适用 |
| 同 ID 多个 rollout | 全部留 raw，关联补齐；内容/顺序歧义标记 partial |
| JSONL 缺失 ID 或索引引用 ID 不一致 | 清单含发现错误，返回 1 |
| 完整文件含非 ASCII、不同空白或换行 | raw 保留字节；标准文件统一序列化，分别核对摘要 |
| 读取期间追加、末尾半行 | 固定快照保留 raw；标准文件排除半行并标记 partial/告警 |
| 有效但末尾无换行的 JSON | 标准文件正常规范化输出 |
| 原始 JSONL 中间损坏 | raw 保留，未转换记录报错；其余标准内容有效，返回 1 |
| 数据库已提交 WAL、可选列缺失 | 可读取，缺失可选值为 null |
| 数据库不存在、损坏或不支持版本 | 区分可用性告警与错误，不声称全量成功 |
| 全部已知 item 类型、Skill/图片引用 | 原生字段映射正确，无无故丢失或虚构内容 |
| item JSON 损坏或未知类型 | raw 保留，转换明确报错；标准文件不混入不合法 payload |
| 孤立消息、缺时间、活动轮次 | 回退可追溯，不伪造历史发生时间和完成状态 |
| 同 item 旧进行中、新完成状态 | 合并可确定的新状态，失败不转成功 |
| 工具调用细节不可恢复 | 保留结构化 item，不猜造 call ID 和参数 |
| 标准 schema 不合法而 raw 成功 | 不发布无效 rollout，会话仍按失败统计 |
| 请求 ID 不存在、普通目录零命中 | 前者返回 1；后者空清单返回 0 |
| 单会话失败、磁盘错误、用户中断 | 状态/退出码明确，其他已完成产物保留 |
| 两次运行、同一时刻运行 | 独立批次，不覆盖之前产物 |
| ID 含路径分隔符、输出位于源目录 | 安全目录映射；源目录内输出被拒绝 |

## 4. 检查与交付

实现期间按改动运行相关测试。完成代码和包接入后执行仓库规定检查：

```bash
uv run ruff check .
uv run pyright
uv run pytest
uv build --all-packages
```

在临时数据源执行以下端到端命令，核对标准 schema、统一读取结果、清单、来源映射及 raw/标准文件各自摘要：

```bash
uv run --package codex-session-export codex-session-export \
  --codex-home <fixture-codex-home> \
  --directory <fixture-project-directory> \
  --output <temporary-export-directory>
```

完成标准：

- [x] 本文阶段任务完成，验收场景有对应验证结果。
- [x] 四项仓库检查通过，聚合命令入口可用。
- [x] 仅文件、仅数据库、混合来源通过同一读取器验收，schema 已打包且与运行时校验一致。
- [x] 导出契约与方案文档一致，无未说明的降级或遗漏。
- [x] 更新文档状态及实际验证结果，记录仍存在的存储版本限制。

## 5. 当前验证记录

2026-10-29：首版实现完成，命令、转换器、schema、聚合入口和文档均已落地。

- 新增 32 个自动化测试，包含独立的数据库/原生条目样本及三来源一致性验收。
- 已验证只读 SQLite WAL 快照、目录边界、损坏数据保留、标准文件校验失败、中断状态、文件追加和替换、重复批次与摘要。
- 对本项目两个真实会话完成临时目录导出：一个数据库独有会话，一个混合来源会话；标准文件有效，无转换错误。临时结果位于系统临时目录，未进入仓库。
- 构建、全仓库检查和安装入口验证结果见交付记录；源版本和信息恢复边界保留在方案第 9 节。

交付检查结果：Ruff 通过；Pyright 0 错误；全仓库 pytest 154 项通过（新增 32 项）；全部 workspace 包构建通过。已验证两个发行包都注册了命令入口，wheel 包含 schema 和 py.typed，并在隔离导入环境读取了 wheel 内 schema。最后的筛选/去重调整后重新运行新增 32 项测试并重新构建，均通过。
