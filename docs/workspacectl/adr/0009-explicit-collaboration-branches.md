---
status: accepted
---

# Collaboration task 使用显式的现有分支映射

## Context

`create-requirement` 从远端基线为多个 project 创建统一命名的独立开发分支。加入他人已经开始的工作时，用户需要在同一个 task root 中直接使用多个 repository 各自已有的远端分支，而不是再派生个人 requirement branch。

## Decision

新增 typed `collaboration` task。`create-collaboration` 接受必填 slug 和可重复的 `<project-id>:<branch>` mapping；一次可准备多个 repository，但同一个 repository 只能绑定一个 branch。branch 必须存在于 `origin`，本地使用同名 tracking branch。系统只允许安全 fast-forward，不自动 reset、rebase、force-update 或 push，也不处理已经分叉或被其他 worktree 占用的 branch。

Collaboration 复用 `worktrees/<date>-<slug>/<project-id>-<slug>/`、project hook、生成指令、status、update、doctor 和 cleanup。Manifest 通过 `kind: collaboration` 与 requirement 区分；缺少 `kind` 的旧 manifest 继续按 requirement 读取。

Collaboration mapping 创建后保持不变。`extend-requirement` 和 `archive-requirement` 在变更前拒绝 collaboration task；collaboration 只通过 cleanup 收尾，cleanup 不删除任何 branch。

## Consequences

- 用户可以一次准备跨 repository 的现有共享 branch。
- Requirement 的生成分支和 archive 生命周期保持不变。
- Task kind 成为 status、生成 AGENTS 和生命周期校验的显式分派依据。
- 远端跟踪信息可能在失败的创建预检中被 fetch 更新，但不会留下部分 task root 或 worktree。
