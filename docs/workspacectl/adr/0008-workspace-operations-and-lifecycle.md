---
status: accepted
---
# Workspace operations split init, update, requirement creation and archive

`init` 只做首次初始化。它从当前目录准备 workspace root，必要时幂等地补齐 `projects/`、`worktrees/`、`doc/`、`.workspace/` 这些骨架目录，不做 reconcile。`update` 是后续的校准命令；它可以做验证和刷新检查，但不改写 workspace 模型。`update` 允许重写轻量恢复信息，但不能改 requirement 语义或 worktree 结构。

如果当前目录还没有 `AGENTS.md`，`init` 会把 usage 模板注入进来，作为新 workspace root 的默认入口指引；已经存在的 `AGENTS.md` 不会被覆盖。

workspace root 已存在的 `AGENTS.md` 不会被 `init` / `update` 改写；它只在用户显式调用 `set-agent-mode` 时切换。

`create-requirement` 创建新的 requirement，分配 task root，并准备所需 worktree；它必须携带初始 project scope，只保留 source PRD URL，不在控制面直接产出 requirement doc，也不预创建 worktree 内的 `doc/` 结构。worktree 内部如何组织文档，由内部 agent 自己决定。`extend-requirement` 在不改变 requirement 身份的前提下，接受显式 `scope delta`，可以新增也可以移除 project；它会按变更同步 worktree，新增 project 补对应 worktree，移除 project 则立即删除对应 worktree，同时刷新剩余 worktree 的 `.workspace/AGENTS.md` 和 manifest。两者在校验 project scope 和准备 worktree 时都采用 all-or-nothing，任一 project 缺失、dirty 或不可读就整条失败，不留半成品。`archive-requirement` 把整个 worktree 的 `doc/` 树移到 `doc/<task-root-name>/`，然后删除可丢弃的 task root 和 worktree；如果要求的文档还没生成，默认拒绝，除非用户显式选择丢弃文档。`create-requirement` 成功后要明确返回 task root、worktree 路径、branch 和 source PRD URL；这些命令默认 human readable，同时预留 `--json`。

`status` 只读查看结构，不给操作建议。`doctor` 只读做健康检查和问题报告，不给修复建议。`cleanup` 是单独命令，必须在 archive 之后执行，负责删除 worktree、task root 和 `.workspace/task.yaml`，但不负责删 branch。

## Consequences

- `init` 保持窄而确定。
- `update` 是后续校准的唯一入口，不直接修复结构。
- Requirement 身份和 project scope 分开。
- `extend-requirement` 使用显式 scope delta。
- 归档只在 durable docs 已移动后发生。
