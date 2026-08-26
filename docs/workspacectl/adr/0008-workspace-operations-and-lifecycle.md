---
status: accepted
---
# Workspace operations split init, update, requirement creation and archive

`init` 只做首次初始化。它从当前目录准备 workspace root，必要时幂等地补齐 `projects/`、`worktrees/`、`doc/`、`.workspace/` 这些骨架目录，初始化独立 Git 仓库，并将 `projects/` 与 `worktrees/` 写入 `.gitignore`，不做 reconcile。`update` 是后续的校准命令；它可以做验证和刷新检查，但不改写 workspace 模型。`update` 允许重写轻量恢复信息，但不能改 requirement 语义或 worktree 结构。

如果当前目录还没有 `AGENTS.md`，`init` 会把 usage 模板注入进来，作为新 workspace root 的默认入口指引；已经存在的 `AGENTS.md` 不会被覆盖。

workspace root 已存在的 `AGENTS.md` 不会被 `init` / `update` 改写；它只在用户显式调用 `set-agent-mode` 时切换。

`create-requirement` 创建新的 requirement，分配 task root，并准备所需 worktree；它必须携带初始 project scope，source PRD URL 可选，不在控制面直接产出 requirement doc，也不预创建 worktree 内的 `doc/` 结构。控制器只校验显式 scope 中的 project，不会因为 `workspace.json` 中未被选中的登记条目缺失而失败；source repo 是否 dirty 也不影响创建。默认只执行 `git fetch origin HEAD`，从本次 fetch 得到的 `FETCH_HEAD` 创建 worktree；传入 `--upstream` 时执行 `git fetch origin <upstream>`。worktree 内部如何组织文档，由内部 agent 自己决定。创建和扩展会按项目类型自动识别并运行 post-create hook，也允许通过 `--project-type` 覆盖。`extend-requirement` 在不改变 requirement 身份的前提下，接受显式 `scope delta`，可以新增也可以移除 project；它会按变更同步 worktree，新增 project 补对应 worktree，移除 project 则立即删除对应 worktree，同时刷新剩余 worktree 的 `.workspace/AGENTS.md` 和 manifest。两者在校验 project scope 和准备 worktree 时都采用 all-or-nothing，任一被选中的 project 缺失或不可读就整条失败，不留半成品。`archive-requirement` 先按 manifest 删除 requirement 下的 child Git worktree，再把整个剩余 task root 直接移到 `doc/<task-root-name>/`；它不检查特定文档、不识别内容，也不做蒸馏。`create-requirement` 成功后要明确返回 task root、worktree 路径、branch 和 source PRD URL（如有）；这些命令默认 human readable，同时预留 `--json`。

`status` 只读查看结构，不给操作建议。`doctor` 只读做健康检查和问题报告，不给修复建议。`cleanup` 只处理尚未 archive 的 active task root；archive 已经删除 child worktree 并移动 task root，不需要再次 cleanup。所有删除动作都不负责删 branch。

## Consequences

- `init` 保持窄而确定。
- `update` 是后续校准的唯一入口，不直接修复结构。
- Requirement 身份和 project scope 分开。
- `extend-requirement` 使用显式 scope delta。
- 归档以整个 task root 为单位，删除 child worktree 后整体移动。
