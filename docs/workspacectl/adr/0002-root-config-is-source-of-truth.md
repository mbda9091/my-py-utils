---
status: accepted
---
# 根配置是 JSON source of truth，项目目录是登记结果

单一根 JSON 配置 `workspace.json` 承载所有 `repositories` entry 的身份与元数据。`repositories` 的 key 就是稳定的 project id，也就是 `projects/<id>` 的目录名。条目里放 repository 元数据，例如 `git.origin`、`description`、`kind`，以及按需出现的平台或语言标签。`kind`、`platforms`、`languages` 只做描述，不参与路由、校验分支选择或启动决策。

workspace 不维护另一份 project 派生清单；项目含义直接写在 config 里，`projects/` 只负责承载现有 checkout 或 symlink。

## Consequences

- `repositories.<id>` 和 `projects/<id>` 必须一一对应。
- 不做别名映射，也不做隐藏清单。
- workspace 不负责创建 project checkout，只消费现成的 `projects/<id>` 路径。
- 缺失或不可读的 `projects/<id>` 直接报错。
- 新增 project 只需要新增 `repositories.<id>` 条目，其他地方不需要生成物联动。
- 描述字段不会影响控制面行为。
