# Workspace Context

这个 context 只固定名词，不写流程。

## 术语

**workspace root**:
长期存在的 workspace 根目录。

**workspace repo**:
承载 workspace 自身配置、文档和模板的独立 git 仓库。

**workspace config**:
根目录下的 JSON 配置文件，作为 source of truth。当前形态以 `workspace.json` 为准。

**repositories**:
workspace config 里的顶层对象，键是已登记 repository 的稳定 id。

**repositories.<id>**:
某个已登记 repository 的配置条目。这个 id 同时也是 `projects/<id>` 的目录名。

**projects/**:
workspace 下的项目汇总目录。里面放各个已登记 project 的本地 checkout 或指向外部 checkout 的链接。

**project**:
`projects/<id>` 下的一个登记项目，对应一个 source repo。

**requirement**:
一次需求改动的维度；一个 requirement 可以覆盖一个或多个 project。

**collaboration**:
加入他人已经推送到 `origin` 的现有分支所形成的协作任务。一次 collaboration 可以包含多个 project，但每个 project 只绑定一个显式分支，创建后映射保持不变。

**requirement title**:
需求的可读标题，记录在 requirement manifest 中；它不自动生成 slug。

**project scope**:
某个 requirement 当前覆盖的 project 集合。

**scope delta**:
`extend-requirement` 使用的显式变更集，包含要新增和要移除的 project。

**task root**:
按 `worktrees/<date>-<slug>/` 命名的任务父目录，承载同一 requirement 或 collaboration 下的多个 worktree。

**task slug**:
创建 requirement 或 collaboration 时显式提供的任务标识，用于 task root 和 child worktree 名称；requirement 还会用它生成 branch 名称。必须符合
`^[a-z0-9]+(?:-[a-z0-9]+)*$`：只能使用小写 ASCII 字母、数字和单个连字符，且必须以字母或数字开头和结尾。

**worktree**:
为一次具体改动创建的可写 checkout。

**branch**:
worktree 使用的分支。requirement branch 格式为 `merchant/<git username>/<slug>`，不带 project id；collaboration branch 是用户为每个 project 显式指定的现有 `origin` 分支。

**.workspace/AGENTS.md**:
每个 worktree 内生成的工作指令文件。

**root AGENTS.md**:
workspace root 的工作指引文件。当前这个 workspace repo 里它保持 `development` 模式；当 `init` 初始化一个新的 workspace root 时，如果 `AGENTS.md` 不存在，会注入 `usage` 模板。

**usage mode**:
根目录 `AGENTS.md` 的使用者模板，只保留 workspace 的命令和生命周期。

**development mode**:
根目录 `AGENTS.md` 的开发者模板，保留 workspace 控制面实现、测试和文档约束。

**set-agent-mode**:
将当前 workspace root 的 `AGENTS.md` 显式替换为 `templates/agents/usage.md` 或 `templates/agents/development.md` 的命令。

**.workspace/task.yaml**:
活跃 task root 内的最小机器可读记录。`kind` 区分 `requirement` 与 `collaboration`；旧 manifest 缺少 `kind` 时按 requirement 读取。文件只保留任务身份、project scope、worktree、分支、可选的 source PRD URL 和 prompt 版本这类恢复所需信息。

**durable deliverables**:
task root 中不属于 child code worktree 的持久化产物；archive 时随整个 task root 一起迁移。

**execution review appendix**:
implementation doc 里的可回验附录，记录最终 scope、关键决策、主要命令和收尾结果，不单独形成第三份长期文档。

**doc/**:
workspace 根目录下的归档目录，保存整体迁移后的 requirement 目录。

**startup prompt**:
launcher 注入给 worktree agent 的运行时提示词，只携带当前 worktree 的事实和约束，不含 workspace 级说明。

**launcher**:
读取 workspace config 并启动 worktree 会话的外部入口。

**workspace controller**:
只负责 worktree 准备和后期归档的控制面。

**JSON mode**:
命令的机器可读输出模式，和 human readable 输出并存。

**cleanup**:
处理 active task root，删除 child worktree 和 task root，但不删 branch。它是 collaboration 唯一的完成入口，也可处理尚未 archive 的 requirement；requirement archive 后不再重复 cleanup。

**status**:
只读查看 workspace 结构的命令。

**doctor**:
只读做健康检查和问题报告的命令。

**orphaned project**:
config 里仍存在、但本地 project 路径失效或不可用的登记项，保留待修复。
