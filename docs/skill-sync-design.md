# skill-sync 设计方案

状态：草案

日期：2026-08-09

## 1. 目标

`skill-sync` 是一个个人使用的 skill 远程同步 CLI。

第一版只解决两件事：

1. 把本地 skill 目录发布到一个 GitHub Git 仓库。
2. 从这个 GitHub Git 仓库把 skill 安装回本地 `~/.agents/skills`。

核心模型参考 `team-skill`：

- 用 Git 仓库作为远端 skill registry。
- 本地维护一个 registry checkout 缓存。
- 发布时把本地 skill 复制到 registry checkout，然后 commit、push。
- 安装时从 registry checkout 复制到本地 agent skill 目录。

定制版只保留个人同步需要的能力，不做团队协作、GUI、悬赏、评价、精选运营等功能。

## 2. MVP 功能

### 2.1 功能列表

| 功能 | 命令 | 说明 |
| --- | --- | --- |
| 初始化配置 | `skill-sync init <git-url> --branch main` | 保存远端仓库地址和分支 |
| 查看状态 | `skill-sync status` | 查看配置、本地缓存、安装记录 |
| 同步远端 | `skill-sync sync` | clone 或 pull 远端 registry |
| 列出 skill | `skill-sync list` | 扫描远端缓存中的 skill |
| 校验本地 skill | `skill-sync validate <local-skill-dir>` | 校验本地目录是否可发布 |
| 发布 skill | `skill-sync publish <local-skill-dir> [--update] [--no-push]` | 本地 skill -> GitHub registry |
| 安装 skill | `skill-sync install <skill-name> --target codex [--force]` | GitHub registry -> `~/.agents/skills` |

### 2.2 MVP 不做

- 不自动创建 GitHub 仓库。
- 不做自动双向 merge。
- 不支持项目级安装。
- 不支持 Codex 之外的客户端安装。
- 不兼容 npm `skills` 的仓库布局。
- 不管理 Codex plugin 或 marketplace。
- 不做 GUI。
- 不上传使用评价、对话、日志或评测数据。

### 2.3 延后功能

```bash
skill-sync diff <skill-name-or-local-skill-dir>
skill-sync show <skill-name>
skill-sync remove <skill-name>
skill-sync doctor
```

## 3. 核心概念

### 3.1 远端 Registry

远端 registry 是一个普通 Git 仓库，例如：

```text
git@github.com:you/agent-skills.git
```

它只负责存放被发布的 skill，不负责执行 skill。

### 3.2 `<local-skill-dir>`

`<local-skill-dir>` 表示本地 skill 目录路径，用在 `validate` 和 `publish`。

示例：

```bash
skill-sync publish ~/.agents/skills/grill-me
```

这里的 `<local-skill-dir>` 是：

```text
~/.agents/skills/grill-me
```

要求：

- 必须是目录。
- 目录下必须有 `SKILL.md`。
- 不能传单个 `SKILL.md` 文件路径。
- 目录最后一级名称就是 skill 的唯一名称。

### 3.3 `<skill-name>`

`<skill-name>` 表示 skill 名称，本质上是远端 `skills/` 下面的一级目录名。

示例：

```bash
skill-sync install grill-me --target codex
```

这里的 `<skill-name>` 是：

```text
grill-me
```

它对应：

```text
远端路径：skills/grill-me/
本地路径：~/.agents/skills/grill-me/
```

要求：

- 必须满足 `^[a-z0-9]+(-[a-z0-9]+)*$`。
- `SKILL.md` frontmatter 里的 `name` 必须和目录名一致。
- 不再维护第二套标识。

### 3.4 命名映射

假设本地 skill 是：

```text
~/.agents/skills/grill-me/
  SKILL.md
  references/
```

发布：

```bash
skill-sync publish ~/.agents/skills/grill-me
```

工具取目录名 `grill-me`，发布到远端：

```text
agent-skills/
  skills/
    grill-me/
      SKILL.md
      skill-sync.json
      references/
```

安装：

```bash
skill-sync install grill-me --target codex
```

工具读取远端缓存：

```text
skills/grill-me/
```

并安装到：

```text
~/.agents/skills/grill-me/
```

## 4. 数据结构

### 4.1 远端仓库结构

MVP 固定使用下面的远端结构：

```text
agent-skills/
  README.md                  # 可选，给人看的说明
  skills/
    <skill-name>/
      SKILL.md               # 必须，skill 主说明
      skill-sync.json        # 必须，工具生成的同步元数据
      scripts/               # 可选
      references/            # 可选
      assets/                # 可选
```

规则：

- `skills/` 是固定目录。
- `skills/` 下一级目录名就是 `<skill-name>`。
- 不支持分类嵌套，例如 `skills/debug/grill-me/`。
- 不支持把 skill 放在仓库根目录，例如 `agent-skills/grill-me/`。
- 每个 `skills/<skill-name>/` 必须包含 `SKILL.md`。
- MVP 不提交 `registry.json`，`list` 运行时扫描 `skills/*/skill-sync.json`。

### 4.2 `skill-sync.json`

`skill-sync.json` 是远端 registry 专用元数据，由工具生成。

示例：

```json
{
  "schemaVersion": 1,
  "name": "grill-me",
  "description": "A relentless interview to sharpen a plan or design.",
  "compatibility": ["codex"],
  "updatedAt": "2026-08-09T00:00:00Z"
}
```

规则：

- `name` 必须等于 skill 目录名。
- `description` 来自 `SKILL.md` frontmatter。
- 不写本地绝对路径。
- 用户不需要手动维护。

### 4.3 安装内容

`skill-sync.json` 只属于远端 registry，不安装进本地 skill 目录。

远端：

```text
agent-skills/
  skills/
    grill-me/
      SKILL.md
      skill-sync.json
      references/
```

本地安装后：

```text
~/.agents/skills/
  grill-me/
    SKILL.md
    references/
```

如果从本地安装目录再次 `publish`，工具会重新生成新的 `skill-sync.json`。

### 4.4 本地状态

默认状态目录：

```text
~/.skill-sync/
  config.json
  repository/
  installations.json
```

`config.json`：

```json
{
  "schemaVersion": 1,
  "repository": "git@github.com:you/agent-skills.git",
  "branch": "main",
  "checkout": "~/.skill-sync/repository"
}
```

`installations.json`：

```json
{
  "codex:grill-me": {
    "skill": "grill-me",
    "target": "codex",
    "commit": "abc1234",
    "path": "/Users/example/.agents/skills/grill-me"
  }
}
```

本地路径可以写进本地状态，但不能写进远端 registry 元数据。

### 4.5 `registry.json` 评估

`registry.json` 是一个可选全局索引文件，可以集中列出所有 skill 摘要。

如果引入，大概是：

```json
{
  "schemaVersion": 1,
  "skills": [
    {
      "name": "grill-me",
      "description": "A relentless interview to sharpen a plan or design.",
      "compatibility": ["codex"],
      "updatedAt": "2026-08-09T00:00:00Z"
    }
  ]
}
```

优点：

- `list` 可以直接读一个文件。
- 方便后续 GUI、网页目录、远端 catalog。
- 人打开仓库时能快速看到所有 skill 摘要。

缺点：

- 和 `skills/<skill-name>/skill-sync.json` 重复。
- 多人或多机器发布时更容易产生 Git 冲突。
- 如果写入失败，可能和实际目录不一致。
- 个人仓库扫描 `skills/*/skill-sync.json` 成本很低。

MVP 决策：

- 不提交 `registry.json`。
- `list`、`install`、`status` 都以 `skills/*/skill-sync.json` 为准。
- 后续如果要做 GUI 或网页目录，再增加 `skill-sync index` 或自动生成 `registry.json`。

## 5. 命令定义

### 5.1 `skill-sync init`

```bash
skill-sync init <git-url> [--branch <branch>]
```

参数：

- `<git-url>`：远端 Git 仓库地址，例如 `git@github.com:you/agent-skills.git`。
- `--branch <branch>`：远端分支，默认 `main`。

行为：

- 写入 `~/.skill-sync/config.json`。
- 不创建 GitHub 仓库。
- 不强制 clone，clone 由 `sync` 完成。
- 已有相同配置时视为成功。
- 已有不同配置时拒绝覆盖。

失败场景：

- `<git-url>` 为空。
- `--branch` 为空。
- 已存在不同配置。
- 配置文件无法写入。

### 5.2 `skill-sync status`

```bash
skill-sync status
```

行为：

- 展示 registry Git 地址。
- 展示 registry 分支。
- 展示本地缓存目录。
- 展示缓存仓库是否存在。
- 如果缓存存在，展示当前 commit 和 dirty 状态。
- 展示已记录安装数量。
- 不访问网络，不修改文件。

失败场景：

- 尚未执行 `init`。
- 配置文件损坏。
- 缓存目录存在但不是 Git 仓库。

### 5.3 `skill-sync sync`

```bash
skill-sync sync
```

行为：

- 如果缓存不存在，执行 `git clone`。
- 如果缓存存在，校验 `origin` 和配置一致。
- 确保当前分支是配置分支。
- 执行 `git pull --rebase --autostash`。
- 同步成功后输出当前 commit。

失败场景：

- 尚未执行 `init`。
- 本机找不到 `git`。
- 没有远端访问权限。
- 远端分支不存在。
- 缓存仓库 remote URL 和配置不一致。
- 缓存仓库有未提交改动。
- rebase 冲突。

### 5.4 `skill-sync list`

```bash
skill-sync list
```

行为：

- 扫描 `~/.skill-sync/repository/skills/*/skill-sync.json`。
- 按 `name` 排序输出。
- 输出 `name`、`description`、`compatibility`、`updatedAt`。
- 不访问网络，不修改文件。

失败场景：

- 尚未执行 `init`。
- 缓存仓库不存在。
- 某个 `skill-sync.json` 损坏。
- `skill-sync.json` 中的 `name` 和目录名不一致。

### 5.5 `skill-sync validate`

```bash
skill-sync validate <local-skill-dir>
```

行为：

- 校验 `<local-skill-dir>` 是目录。
- 校验存在 `SKILL.md`。
- 解析 `SKILL.md` frontmatter。
- 要求 frontmatter 包含 `name` 和 `description`。
- 校验目录名满足 `^[a-z0-9]+(-[a-z0-9]+)*$`。
- 要求 frontmatter `name` 等于目录名。
- 不访问网络，不修改文件。

成功输出建议：

```text
skill 可发布：
name: grill-me
description: A relentless interview to sharpen a plan or design.
path: /absolute/path/to/grill-me
```

失败场景：

- 路径不存在。
- 路径不是目录。
- 缺少 `SKILL.md`。
- frontmatter 缺失或格式错误。
- 缺少 `name` 或 `description`。
- 目录名不符合规则。
- frontmatter `name` 和目录名不一致。

### 5.6 `skill-sync publish`

```bash
skill-sync publish <local-skill-dir> [--update] [--no-push]
```

参数：

- `<local-skill-dir>`：本地 skill 目录。
- `--update`：允许覆盖远端已有 `skills/<skill-name>`。
- `--no-push`：只创建本地 registry commit，不 push。

行为：

- 执行 `validate`。
- 对即将发布的文件执行隐私和安全校验。
- 确保 registry 缓存存在，并在发布前同步远端。
- 从 `<local-skill-dir>` 的目录名得到 `<skill-name>`。
- 如果 `skills/<skill-name>` 已存在且没有 `--update`，拒绝发布。
- 把本地 skill 复制到 staging 目录。
- 忽略 `.git`、`.venv`、`__pycache__`、`.DS_Store`、`*.pyc`。
- 写入 `skill-sync.json`。
- 原子替换远端缓存中的 `skills/<skill-name>`。
- 如果内容没有变化，不创建 commit，返回成功并提示远端已是最新。
- 如果有变化，创建 commit。
- 如果没有 `--no-push`，执行 push。

默认 commit message：

```text
Publish skill: <skill-name>
```

更新已有 skill 时：

```text
Update skill: <skill-name>
```

失败场景：

- 尚未执行 `init`。
- 本地 skill 校验失败。
- 隐私和安全校验发现高风险内容。
- registry sync 失败。
- 远端 skill 已存在但未传 `--update`。
- staging 或原子替换失败。
- Git commit 失败。
- Git push 失败。

push 失败处理：

- 本地 commit 必须保留在 registry 缓存仓库。
- 工具提示缓存目录和失败原因。
- 工具不自动 reset、不自动删除 commit。

### 5.7 `skill-sync install`

```bash
skill-sync install <skill-name> --target codex [--force]
```

参数：

- `<skill-name>`：远端 `skills/` 下的一级目录名。
- `--target codex`：安装目标，MVP 只支持 `codex`。
- `--force`：允许覆盖本地已有 `~/.agents/skills/<skill-name>`。

行为：

- 不自动访问网络。用户需要先执行 `skill-sync sync` 获取最新 registry。
- 从 `~/.skill-sync/repository/skills/<skill-name>` 读取 skill。
- 如果 registry 中没有这个 skill，安装失败。
- 如果本地 `~/.agents/skills/<skill-name>` 已存在且没有 `--force`，拒绝安装。
- 把 registry skill 复制到目标目录下的 staging 目录。
- 剥离 registry 专用文件 `skill-sync.json`。
- 原子替换本地目标目录。
- 写入 `installations.json`，记录安装来源 commit。

失败场景：

- 尚未执行 `init`。
- 缓存仓库不存在。
- `<skill-name>` 不合法。
- registry 中没有这个 skill。
- 本地目标已存在但未传 `--force`。
- 目标目录无法写入。
- 替换失败。

## 6. 实现方案

实现按功能拆分。每个功能对应明确模块，避免所有逻辑堆在 CLI 里。

### 6.1 包结构

```text
packages/skill-sync/
  pyproject.toml
  README.md
  src/skill_sync/
    __init__.py
    catalog.py
    cli.py
    config.py
    errors.py
    git_repo.py
    installer.py
    publisher.py
    security.py
    validation.py
    py.typed
```

CLI 入口：

```toml
[project.scripts]
skill-sync = "skill_sync.cli:main"
```

MVP 使用 Python 标准库：

- `argparse`：命令行参数。
- `json`：配置和元数据。
- `pathlib`：路径。
- `shutil`、`tempfile`：复制、staging、原子替换。
- `subprocess`：调用 Git。

### 6.2 通用模块

#### `errors.py`

定义可直接展示给用户的异常：

```python
class SkillSyncError(RuntimeError):
    """可以直接展示给 CLI 用户的错误。"""
```

#### `cli.py`

职责：

- 定义命令和参数。
- 调用功能模块。
- 把 `SkillSyncError` 转成 stderr 和退出码。
- 保持命令函数薄。
- MVP 不做交互式 prompt。

退出码：

- `0`：成功。
- `1`：预期内操作失败。
- `2`：参数或用法错误。

### 6.3 功能实现：初始化和状态

对应命令：

```bash
skill-sync init <git-url> --branch main
skill-sync status
```

模块：

- `config.py`
- `git_repo.py`

`config.py` 职责：

- 解析 `~/.skill-sync`。
- 支持环境变量 `SKILL_SYNC_HOME` 覆盖状态目录。
- 读写 `config.json`。
- 读写 `installations.json`。
- 原子写 JSON 文件。

`status` 读取：

- `config.json`。
- `~/.skill-sync/repository/.git`。
- 当前 commit。
- 工作区 dirty 状态。
- `installations.json` 条目数量。

### 6.4 功能实现：同步远端

对应命令：

```bash
skill-sync sync
```

模块：

- `git_repo.py`

职责：

- 缓存不存在时 clone registry 仓库。
- 校验缓存仓库的 `origin` URL 和配置一致。
- 确保当前分支是配置分支。
- 阻止 dirty 工作区 pull。
- 执行 `pull --rebase --autostash`。
- 返回当前 commit。

push 相关职责也放在 `git_repo.py`：

- `git add` 指定路径。
- staged 内容为空时返回“无变化”。
- 创建 commit。
- push。
- push 失败后尝试一次 `pull --rebase --autostash` 再 push。
- 再失败时保留本地 commit，并返回缓存仓库路径。

### 6.5 功能实现：校验本地 skill

对应命令：

```bash
skill-sync validate <local-skill-dir>
```

模块：

- `validation.py`

职责：

- 校验路径存在且是目录。
- 读取目录名作为 skill name。
- 校验目录名格式。
- 要求 `SKILL.md` 存在。
- 解析 frontmatter。
- 要求 `name` 和 `description`。
- 要求 frontmatter `name` 等于目录名。
- 返回标准 metadata：`name`、`description`、`path`。

MVP frontmatter 解析可以保持简单，但缺失或格式错误时必须给出清晰错误。

### 6.6 功能实现：列出远端缓存 skill

对应命令：

```bash
skill-sync list
```

模块：

- `catalog.py`

职责：

- 遍历 `skills/*/skill-sync.json`。
- 加载 metadata。
- 校验 metadata `name` 和目录名一致。
- 按名称排序。
- 为 CLI 提供 `list` 输出数据。
- 按名称查找 skill，供 `install` 使用。

MVP 不写 `registry.json`，catalog 只运行时扫描 registry 目录。

### 6.7 功能实现：发布 skill

对应命令：

```bash
skill-sync publish <local-skill-dir> [--update] [--no-push]
```

模块：

- `validation.py`
- `security.py`
- `publisher.py`
- `git_repo.py`
- `catalog.py`

流程：

1. `validation.py` 校验本地 skill。
2. `security.py` 扫描待发布文件。
3. `git_repo.py` 确保 registry 缓存存在，并同步远端。
4. `publisher.py` 判断远端 `skills/<skill-name>` 是否存在。
5. 如果已存在且没有 `--update`，拒绝覆盖。
6. 复制本地 skill 到 staging 目录。
7. 写入 `skill-sync.json`。
8. 原子替换 registry 缓存里的 `skills/<skill-name>`。
9. `git_repo.py` 提交 `skills/<skill-name>`。
10. 如果没有 `--no-push`，push 到远端。

忽略规则：

- `.git`
- `.venv`
- `__pycache__`
- `.DS_Store`
- `*.pyc`

### 6.8 功能实现：发布前隐私和安全校验

对应命令：

```bash
skill-sync publish <local-skill-dir>
```

模块：

- `security.py`

校验范围：

- 只扫描 publish 最终会复制到远端的文件。
- 忽略规则和 publisher 复制规则一致。

高风险内容会阻止发布：

- 私钥和证书文件：`id_rsa`、`*.pem`、`*.key`、`*.p12`。
- 环境和认证配置：`.env`、`.npmrc`、`.pypirc`、`.netrc`。
- 明显 token 或密钥模式：`ghp_`、`sk-`、`AKIA`、`BEGIN PRIVATE KEY`、`Authorization: Bearer`。
- 本地绝对路径：例如 `/Users/<name>/...`。文档和代码示例里应使用占位符。
- 大型二进制或压缩包：`*.zip`、`*.tar`、`*.dmg`。后续如果要发布资产，再单独设计资产策略。

低风险内容只告警，不阻止发布：

- 文档中出现 `token`、`password`、`secret` 等词，但没有明显真实值。
- 文档中出现公司或内部系统名称，但没有凭据。
- 文件较大但不是明确禁止类型。

输出要求：

- 输出命中的文件路径和原因。
- 不打印完整敏感值。
- MVP 不提供绕过参数。

### 6.9 功能实现：安装 skill

对应命令：

```bash
skill-sync install <skill-name> --target codex [--force]
```

模块：

- `catalog.py`
- `installer.py`
- `config.py`
- `git_repo.py`

流程：

1. 校验 `<skill-name>` 格式。
2. 从本地 registry 缓存查找 `skills/<skill-name>`。
3. 读取当前 registry commit。
4. 解析目标目录 `~/.agents/skills/<skill-name>`。
5. 如果目标存在且没有 `--force`，拒绝安装。
6. 复制 registry skill 到 staging 目录。
7. 剥离 `skill-sync.json`。
8. 原子替换目标目录。
9. 写入 `installations.json`。

安装目标：

```text
~/.agents/skills/<skill-name>
```

项目级安装延后，不进 MVP。项目级安装指安装到：

```text
<project>/.agents/skills/<skill-name>
```

第一版不解析当前项目、不提供项目级安装参数，也不写入项目目录。

## 7. 测试计划

新增测试文件：

```text
tests/test_skill_sync_validation.py
tests/test_skill_sync_config.py
tests/test_skill_sync_catalog.py
tests/test_skill_sync_publisher.py
tests/test_skill_sync_security.py
tests/test_skill_sync_installer.py
tests/test_skill_sync_cli.py
```

测试重点：

- 合法 skill 返回名称和 description。
- 缺少 `SKILL.md`、frontmatter、`name`、`description` 时失败。
- 目录名非法时失败。
- frontmatter `name` 和目录名不一致时失败。
- 空 registry 可以正常 list。
- 损坏的 `skill-sync.json` 报错。
- `list` 可以扫描 `skills/*/skill-sync.json`。
- publish 写入 `skills/<skill-name>/SKILL.md`。
- publish 写入 `skill-sync.json`。
- 已存在远端 skill 且没有 `--update` 时失败。
- 忽略文件不会被复制。
- 高风险隐私或安全内容会阻止发布。
- 没有 staged change 时提示 nothing to publish。
- 远端 skill 不存在时 install 失败。
- 本地目标存在且没有 `--force` 时 install 失败。
- install 后不保留 `skill-sync.json`。
- install 后写入 `installations.json`。
- 替换失败时尽可能恢复旧目录。

文件系统行为用临时目录测试。Git 行为默认 mock subprocess，除非某个测试确实需要真实 Git 仓库。

## 8. 实施阶段

### 阶段 1：包脚手架

验收：

```bash
uv run --package skill-sync skill-sync --help
```

### 阶段 2：配置和状态

实现：

- `init`
- `status`
- `config.py`
- `errors.py`
- `cli.py` 基础结构

验收：

```bash
uv run --package skill-sync skill-sync init git@github.com:you/agent-skills.git --branch main
uv run --package skill-sync skill-sync status
```

### 阶段 3：Git 缓存和同步

实现：

- `sync`
- `git_repo.py`

验收：

```bash
uv run --package skill-sync skill-sync sync
```

### 阶段 4：本地 skill 校验

实现：

- `validate`
- `validation.py`

验收：

```bash
uv run --package skill-sync skill-sync validate ~/.agents/skills/foo
```

### 阶段 5：Catalog 和 list

实现：

- `list`
- `catalog.py`

验收：

```bash
uv run --package skill-sync skill-sync list
```

### 阶段 6：发布和安全扫描

实现：

- `publish`
- `publisher.py`
- `security.py`

验收：

```bash
uv run --package skill-sync skill-sync publish ~/.agents/skills/foo --no-push
uv run --package skill-sync skill-sync publish ~/.agents/skills/foo --update
```

### 阶段 7：安装

实现：

- `install`
- `installer.py`

验收：

```bash
uv run --package skill-sync skill-sync install foo --target codex
uv run --package skill-sync skill-sync install foo --target codex --force
```

### 阶段 8：文档和检查

补充：

- `packages/skill-sync/README.md`

运行：

```bash
uv run ruff check .
uv run pyright
uv run pytest
uv build --all-packages
```

## 9. 风险和约束

风险：

- 意外把私有或内部 skill 内容发布到公开仓库。
- 把本地缓存或生成文件一起发布。
- 未经明确确认覆盖远端 skill。
- 未经明确确认覆盖本地已安装 skill。
- 本地 commit 成功但 push 失败。
- 远端分支不一致。
- registry 缓存仓库 dirty。
- 部分写入导致 metadata 损坏。
- 发布前未发现敏感文件、密钥或本地绝对路径。

MVP 缓解：

- 覆盖远端必须传 `--update`。
- 覆盖本地必须传 `--force`。
- 忽略常见生成目录和文件。
- publish 前强制执行隐私和安全校验。
- 使用 staging 目录和原子替换。
- push 失败时保留本地 registry commit。
- 远端 metadata 不写本地绝对路径。

## 10. MVP 完成定义

下面流程可以端到端工作：

```bash
skill-sync init git@github.com:you/agent-skills.git --branch main
skill-sync sync
skill-sync validate ~/.agents/skills/foo
skill-sync publish ~/.agents/skills/foo
skill-sync list
skill-sync install foo --target codex --force
```

并且 workspace 检查通过：

```bash
uv run ruff check .
uv run pyright
uv run pytest
uv build --all-packages
```

## 11. 当前决策

- skill 的唯一标识是目录名，不维护第二套标识。
- 用户级安装目录是 `~/.agents/skills/<skill-name>`。
- 项目级安装延后。
- `skill-sync.json` 只保留在远端 registry，安装时剥离。
- publish 默认是真发布，发布前必须强制做隐私和安全校验。
- MVP 不提交 `registry.json`。
- npm `skills` 兼容性暂不考虑。
