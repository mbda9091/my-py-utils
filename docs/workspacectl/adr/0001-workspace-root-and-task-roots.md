---
status: accepted
---
# Workspace 以长期根目录和任务父目录组织 worktree

我们采用一个长期存在的 workspace root，且它本身是独立的 workspace repo。一次 requirement 对应一个 `worktrees/<date>-<slug>/` 任务父目录，父目录下按需放多个 project worktree。任务分支统一用 `merchant/<git username>/<slug>`。

## Consequences

- `projects/` 只承载登记项目，不是需求面。
- requirement 和 project 是两条正交维度，不存在主次 project。
- 同名 slug 先问是否为同一 requirement；同一则复用，不同则另起 slug。
- task root 不保留执行快照。
- 归档时保留文档的相对目录结构，整树迁移，不做平铺。
- 归档前如果需求文档或实现方案还没生成，默认拒绝；只有用户显式选择丢弃文档时才继续。
- 归档时只迁移需求文档和实现方案到 `doc/<task-root-name>/`，保留原始文件名。
- 归档后 task root 可以删除。
- task root 的日期部分使用 `YYYY-MM-DD`，按 workspace 当前时区的本地日期。
- 同一个 requirement 下同一个 project 只允许一个 worktree。
- 同一个 git 用户下如果 slug 冲突，必须换 slug，不允许靠人工复用 branch 名。
- 具体 worktree 子目录命名为 `worktrees/<date>-<slug>/<project-id>-<slug>/`。
- 归档后执行 `cleanup` 时，不自动删除 branch。
- branch 只按 requirement slug 命名，不带 project id。
- `create-requirement` 使用显式传入的 requirement title 和 slug；slug 必须符合小写 ASCII 字母、数字和单个连字符的格式。
