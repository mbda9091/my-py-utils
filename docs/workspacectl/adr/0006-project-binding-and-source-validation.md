---
status: accepted
---
# Workspace config validates source bindings and stays extensible

`workspace.json` 直接按 JSON 解析，没有单独的 schema version 字段。每个 `repositories.<id>` 条目都绑定到本地 `projects/<id>` 路径，并校验这个 project 是否仍然存在、是否指向预期的 checkout 或 symlink，而不是盲信路径。`workspacectl init` 会拒绝任何有未提交改动的 source checkout。

## Consequences

- 控制器可以尽早拒绝失配或失效的本地 checkout。
- dirty source checkout 会直接失败。
- 新增 project 只是一条 config edit，不需要重写第二份派生清单。
- 以后如果真的需要破坏性 schema 变化，再显式处理，不改变 project 身份规则。
