---
status: accepted
---
# 外部 prompt 注入与 `.workspace/AGENTS.md` 分层

会话启动时由外部 launcher 组装 runtime prompt，只放当前 worktree 的事实、约束和这次 requirement 在该 worktree 内需要知道的目标；每个 worktree 另外生成自己的 `.workspace/AGENTS.md`，作为可重建的工作指令层。workspace root 的 `AGENTS.md` 作为入口指引，在新 workspace root 里由 `init` 注入 `usage` 模板；当需要改 workspace 控制面时，再用 `set-agent-mode` 切到 `development`。workspace controller 只准备 worktree 和做归档，不直接承担项目实现。

## Consequences

- 启动提示保持短，只承载事实和约束，不承载教程。
- per-worktree 指令文件统一落在 `.workspace/AGENTS.md`。
- 项目主 checkout 的 `AGENTS.local.md`（如果存在）以软链形式挂载到每个 worktree 根目录，供项目本地规则复用。
- 新 workspace root 的 `AGENTS.md` 由 `init` 默认注入 `usage` 模板。
- 当前 workspace root 的 `AGENTS.md` 可以通过 `set-agent-mode` 显式切换。
- launcher 只认当前目录，不向上找 workspace root。
