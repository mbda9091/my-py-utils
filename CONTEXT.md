# Codex Hook 管理上下文

本上下文描述可被单独记录并按名称注入 Codex 配置层的自定义生命周期 hook。`record` 接受全局或指定目录的来源范围，扫描全局 `~/.codex/hooks.json` 或目录 `<dir>/.codex/hooks.json`，并为其中每个自定义 hook 创建独立 record。

## Language

**Hook**:
一个由代码文件和一个或多个 Codex 生命周期注册项组成的可管理单元。
_Avoid_: 只把某一条事件注册项称为 hook

**Hook code**:
Hook 执行时调用的本地代码文件；首版仅支持 Codex `command` handler。
_Avoid_: inline script

**Registration**:
把 hook code 绑定到 Codex 事件及 matcher 的声明，包含执行命令和该事件需要的注册字段。
_Avoid_: trigger, subscription

**Hook record**:
一个只包含单个自定义 hook 的定义，包含该执行文件和它自己的一个或多个 Codex 注册项；hook record 的独立目录名不带执行文件后缀，多个 hook record 彼此独立。
_Avoid_: package, bundle

**Executable file**:
被 hook registration 调用的具体自定义文件；它是 record 的身份来源，record 目录使用其不带后缀的文件名。
_Avoid_: arbitrary command

**Record schema**:
描述单个 hook record 必要字段的结构，至少包含执行文件、注册时机和来源标记；每个 `hook.json` 只描述自己的 hook。
_Avoid_: aggregate schema

**Global injection**:
把选定的 hook record 注入用户默认的 `~/.codex` 配置层，使其对用户的 Codex 会话全局生效。
_Avoid_: system hook, managed hook

**Directory injection**:
把选定的 hook record 注入用户明确指定的配置目录，使其仅在该目录对应的项目上下文中生效；工具不推断或追加其他目录。
_Avoid_: project install

**Injection target**:
一次按 hook 名选定的生效配置层：全局用户层或指定目录层。
_Avoid_: source directory

**Record source**:
记录 hook 时被扫描的现有 Codex 配置层；来源由 `record` 的全局标识或指定目录决定，工具自动扫描其中的全部自定义 hook。首版把 command 引用该层 `.codex/hooks/` 下文件的注册项视为自定义 hook，外部命令不记录。
_Avoid_: injection target

**Local record**:
从指定目录记录的 hook，保留该目录仓库的 Git origin URL；origin 不存在时使用目录名作为来源标记。
_Avoid_: global record

**Global record**:
从用户默认 `~/.codex` 层记录的 hook，来源标记为 global。
_Avoid_: local record

**Force overwrite**:
只有用户在 `record` 或 `inject` 中显式提供 `-f` 时，才覆盖已存在的同名记录或目标 hook；普通操作遇到重名时失败并提示该选项。
_Avoid_: update, implicit overwrite

**Source layer**:
被搜索和记录的 Codex 配置层，首版仅指用户全局层 `~/.codex/hooks.json` 或指定目录层 `<dir>/.codex/hooks.json`。
_Avoid_: runtime layer

**Trust boundary**:
Codex 对非托管 hook 的 review/trust 生命周期；hook 管理工具不修改或代办该状态。
_Avoid_: install permission
