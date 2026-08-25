---
status: accepted
---
# 活跃 task root 保留机器可读 manifest

活跃 task root 的 `.workspace/task.yaml` 只保留一份小型机器可读 manifest，记录本任务选中的 project、当前 project scope、创建的 worktree、分支名、source PRD URL 和 prompt 版本。它不是任务快照，也不是长期归档；任务完成后，长期沉淀的是需求文档和实现方案。扩展 requirement 时，manifest 跟着更新到新的 scope；新增 project 补对应 worktree，移除 project 则移除对应 worktree。命令成功时会返回机器可读摘要，至少包括 task root、worktree 路径、branch 和 PRD URL。

## Consequences

- manifest 只服务活跃任务期间的 launcher/controller 交接。
- manifest 不保存完整会话历史。
- cleanup 会删除 task root 和 manifest。
- 需求文档来自内部 PRD 的转化结果，方案文档由 `grill-with-docs` 生成。
- 文档迁移到 `doc/<task-root-name>/` 时，保留原始文件名。
