# Workspace Execution Runbook

这份文档只记录可回验的执行流程，不记录会话快照。

## 目标

- 让 workspace 控制面和 worktree 内执行分工清晰。
- 让每次 requirement 或 collaboration 的输入、动作和收尾结果都能回看。
- 让后续实现只依赖稳定文件，而不是依赖聊天过程。

## 可回验来源

回验只看这些东西：

1. `workspace.json`
2. `projects/<id>`
3. `worktrees/<date>-<slug>/<project-id>-<slug>/`
4. `worktrees/<date>-<slug>/.workspace/task.yaml`（active task）
5. `doc/<task-root-name>/`（archived task）
6. task manifest 记录的 git branch

## root AGENTS 模式

workspace root 的 `AGENTS.md` 只负责当前工作方式的入口，不混进 worktree 细节。

- `usage`：给 workspace 使用者看的模板，只保留命令和生命周期。`init` 在新 workspace root 里会默认注入它。
- `development`：给 workspace 维护者看的模板，保留实现、测试和文档约束。
- 切换命令：
  ```bash
  workspacectl set-agent-mode usage
  workspacectl set-agent-mode development
  ```
- 这个命令只重写当前 workspace root 的 `AGENTS.md`，不影响 worktree 内的 `.workspace/AGENTS.md`。

## 生命周期

### 1. `init`

- 只做初始化。
- 只看当前目录，不向上找。
- 幂等补齐 `projects/`、`worktrees/`、`doc/`、`.workspace/` 这些骨架。
- 同步初始化独立 Git 仓库，并在 `.gitignore` 中忽略 `projects/` 与 `worktrees/`；workspace 控制面和后续 skill 文件保持可跟踪。
- 如果当前目录还没有 `AGENTS.md`，写入 usage 模板。
- 不做 reconcile，不改需求语义。

### 2. `update`

- 只做后续校准。
- 允许刷新轻量恢复信息。
- 不改 task 身份、project scope、branch mapping 或 worktree 结构。

### 3. `create-requirement`

- 输入：requirement title、必填 slug、可选 PRD URL、显式初始 scope。
- slug 只能使用小写 ASCII 字母、数字和单个连字符，且必须以字母或数字开头和结尾；不合规时直接拒绝。
- 创建 task root。
- 只校验显式 scope 中的 project；`workspace.json` 中未被选中的登记条目不会阻止本次创建。
- 为 scope 内每个 project 准备 worktree。
- 如果项目主 checkout 存在 `AGENTS.local.md`，在 worktree 根目录创建指向它的软链；没有该文件时不创建悬空软链。
- source repo 可以有未提交改动；默认只执行 `git fetch origin HEAD`，再从本次 fetch 得到的 `FETCH_HEAD` 创建 worktree。传入 `--upstream` 时，将其作为 `<upstream>` 执行 `git fetch origin <upstream>`。
- 按项目类型自动识别并执行 worktree post-create hook；可用 `--project-type` 覆盖识别结果。
- 写入活跃 task root 的 `.workspace/task.yaml`。
- 返回 task root、worktree、branch 和 PRD URL（如有）的摘要。

### 4. `create-collaboration`

- 输入：必填 slug，以及一个或多个显式 `<project-id>:<origin-branch>` mapping。
- 同一个 project 只能出现一次；不同 project 可以使用不同 branch。
- 只接受 `workspace.json` 已登记且 `projects/<id>` 可用的 source repo，不负责 clone 或临时登记仓库。
- 先完成全部 project 和 branch 预检，再创建任何 worktree；任一远端 branch 缺失则整条失败。
- 远端 branch 必须已存在于 `origin`。本地 branch 缺失时创建同名 tracking branch；落后时只做 fast-forward；领先时保留；分叉、upstream 冲突或已被其他 worktree 占用时拒绝。
- 不创建 requirement branch，不执行 reset、rebase、force-update 或 push。
- 在同一个 `worktrees/<date>-<slug>/` 下为每个 mapping 创建 `<project-id>-<slug>/`，复用 exclusions、`AGENTS.local.md` 和 post-create hook。
- 写入 `kind: collaboration` 的 task manifest。创建失败时删除本次 worktree、task root 和本次新建的本地 branch，不删除已有 branch 或任何远端 branch。
- collaboration mapping 创建后保持不变，不支持 `extend-requirement` 或 `archive-requirement`。

### 5. worktree 内执行

- 进入单个 worktree 后，由内部 agent 继续工作。
- workspace 控制面只负责准备和收尾。
- requirement 的持久化产物放在 task root 中，不放进代码 worktree。
- worktree 只承载代码实现和临时执行环境。
- collaboration worktree 直接在显式共享 branch 上协作；不要派生 requirement branch。

### 6. `extend-requirement`

- 输入：显式 scope delta。
- 允许新增 project，也允许移除 project。
- 新增 project 补 worktree。
- 移除 project 立即删对应 worktree。
- 同步更新 `.workspace/task.yaml`。
- 只接受 requirement task；collaboration task 会在变更前被拒绝。

### 7. `archive-requirement`

- 先校验 task manifest、child worktree 注册状态、dirty 状态和 archive 目标。
- 按 manifest 删除 requirement 下的 child Git worktree；dirty worktree 需要显式 `--force`。
- 将剩余的整个 task root 直接搬到 `doc/<task-root-name>/`。
- 不识别文件内容，不区分文档类型，不做蒸馏或额外快照。
- archive 完成后不需要再执行 cleanup 来删除这些 child worktree。
- 只接受 requirement task；collaboration task 不进入 `doc/`。

### 8. `cleanup`

- 只处理尚未 archive 的 active task root；archive 后的 task root 已经被移动，不再重复 cleanup。
- 删除前执行对应的 pre-clean hook；Android 默认执行 `kkr clear`，default 项目在可用时清理 Watchman watch。
- 删除 worktree、task root 和 `.workspace/task.yaml`。
- 不负责删 branch。
- collaboration 以 cleanup 作为唯一完成入口；它不支持 extend 或 archive。

## 归档后仍可看的东西

- `doc/<task-root-name>/` 下完整保留的 requirement 目录。
- 目录内的原始文件、子目录和 task manifest。
- requirement 产物中的 execution review appendix（如有）。

## execution review appendix

实现文档里必须保留一个简短附录，用来回验过程。建议至少包含：

- `task_root`
- `source_prd_url`（可选；未提供时为空）
- `requirement_title`
- `branch`
- `scope_before`
- `scope_after`
- `key_commands`
- `validation_result`
- `archive_result`
- `cleanup_result`

附录只保留必要事实，不写完整过程日志，不再另建第三份长期文档。

## 验证规则

- 任何命令返回时，都要能对照当前文件系统状态。
- `--json` 输出和 human readable 输出都要能对应同一份事实。
- `status` 只读展示现状。
- `doctor` 只读报告健康问题。
- 回验优先看 `workspace.json`、task manifest 和归档文档，不看临时会话痕迹。
- 旧 task manifest 没有 `kind` 时按 requirement 读取；新 manifest 显式记录 `kind`。
