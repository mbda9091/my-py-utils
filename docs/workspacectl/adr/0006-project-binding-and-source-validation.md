---
status: accepted
---
# Workspace config validates source bindings and stays extensible

`workspace.json` 直接按 JSON 解析，没有单独的 schema version 字段。每个 `repositories.<id>` 条目都是项目标本和元数据；命令只在 project 被当前操作选中时，才按需绑定并校验对应的 `projects/<id>` 路径是否存在、是否指向 Git checkout 或 symlink。未被选中的登记条目不会阻止创建 requirement。创建 worktree 不要求 source checkout 干净，默认只 fetch `origin` 的 `HEAD`，并从本次 fetch 得到的 `FETCH_HEAD` 创建；诊断命令仍会报告所有登记项目的缺失、失效和 dirty 状态。

## Consequences

- 控制器只对当前操作需要的 project 拒绝失配或失效的本地 checkout。
- dirty source checkout 不阻止创建新的 worktree。
- 新增 project 只是一条 config edit，不需要重写第二份派生清单。
- 以后如果真的需要破坏性 schema 变化，再显式处理，不改变 project 身份规则。
