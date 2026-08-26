---
status: accepted
---
# 活跃 task root 保留机器可读 manifest

活跃 task root 的 `.workspace/task.yaml` 只保留一份小型机器可读 manifest，记录本任务选中的 project、当前 project scope、创建的 worktree、分支名、可选的 source PRD URL 和 prompt 版本。它服务于 active task 的控制面；archive 时随整个 task root 一起移动。扩展 requirement 时，manifest 跟着更新到新的 scope；新增 project 补对应 worktree，移除 project 则移除对应 worktree。命令成功时会返回机器可读摘要，至少包括 task root、worktree 路径、branch 和 PRD URL（如有）。

## Consequences

- manifest 只服务活跃任务期间的 launcher/controller 交接。
- manifest 不保存完整会话历史，但会随 task root 整体归档。
- cleanup 只删除尚未 archive 的 active task root 和 manifest。
- archive 不解析或分类 requirement 目录中的产物。
