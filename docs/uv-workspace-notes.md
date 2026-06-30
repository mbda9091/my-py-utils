# Python Monorepo、uv Workspace 与 Package 概念梳理

这篇文档总结当前仓库采用的结构，以及背后的几个核心概念：monorepo、uv workspace、package、`uv sync`、`--all-groups`、`--all-packages` 和 pyright 类型检查。

## Monorepo 是什么

Monorepo 指的是：一个 Git 仓库里放多个项目。

对当前仓库来说，就是多个 Python CLI 工具可以放在一起：

```text
my-py-utils/
  packages/
    hello-cli/
    json-tool/
    image-tool/
    my-py-utils-common/
```

这种结构适合多个小工具、维护者相同、公共代码较多、希望统一工程规范的场景。

优点：

- 多个 CLI 工具集中管理。
- 公共逻辑可以放到共享包里。
- Ruff、pyright、pytest、构建命令可以统一配置。
- Python 版本和依赖升级更方便。
- 跨工具重构更容易。

缺点：

- 仓库会随着工具增多而变大。
- 目录结构和 package 约定更重要。
- workspace/package 模型比单项目稍复杂。

## uv Workspace 是什么

`uv workspace` 是 uv 对 monorepo 的支持。它允许一个仓库里包含多个 Python package，同时共享一个根配置和一个 `uv.lock`。

当前仓库结构类似：

```text
my-py-utils/
  pyproject.toml
  uv.lock
  packages/
    hello-cli/
      pyproject.toml
      src/hello_cli/
    my-py-utils-common/
      pyproject.toml
      src/my_py_utils_common/
  tests/
```

根目录的 `pyproject.toml` 里有：

```toml
[tool.uv]
package = false

[tool.uv.workspace]
members = ["packages/*"]
```

含义是：

- 仓库根目录只是管理项目，不作为可安装的 Python package。
- `packages/*` 下的子项目才是 workspace packages。

## Package 是什么

Python 里 package 这个词有几层含义，容易混在一起。

### Import Package

import package 是 Python 代码导入层面的包。

例如：

```text
src/my_py_utils_common/
  __init__.py
  text.py
```

代码里可以这样导入：

```python
from my_py_utils_common import greeting
```

这里的 `my_py_utils_common` 就是 import package。

### Distribution Package

distribution package 是安装和发布层面的包。

例如：

```toml
[project]
name = "my-py-utils-common"
version = "0.1.0"
```

这个名字用于安装或发布：

```bash
pip install my-py-utils-common
```

distribution name 和 import name 不一定相同：

```text
安装名：my-py-utils-common
导入名：my_py_utils_common
```

这在 Python 生态里很常见：

```text
pip install beautifulsoup4  -> import bs4
pip install pyyaml          -> import yaml
pip install pillow          -> from PIL import Image
```

### Workspace Package

在 uv workspace 中，每个拥有自己 `pyproject.toml` 的子项目，也叫 workspace package。

当前 workspace packages：

```text
packages/hello-cli
packages/my-py-utils-common
```

## uv sync 是什么

`uv sync` 的作用是：根据 `pyproject.toml` 和 `uv.lock` 同步 `.venv`。

它可以：

- 创建或更新 `.venv`。
- 安装锁文件中的依赖。
- 移除不应该存在的包。
- 安装项目本身。
- 注册 CLI 命令入口。

在单 package 项目里，`uv sync` 通常会把当前项目本身安装进 `.venv`。

例如：

```text
my-tool/
  pyproject.toml
  src/my_tool/
```

执行：

```bash
uv sync
```

之后这些命令可以正常工作：

```bash
uv run python -c "import my_tool"
uv run my-tool
```

## 为什么要安装项目本身

安装项目本身的意思是：告诉 `.venv` 这个 package 的代码在哪里。

开发时，uv 通常会用 editable mode 安装本地 package。它更像是在 `.venv` 中登记路径，而不是复制一份源码：

```text
my_py_utils_common -> packages/my-py-utils-common/src
hello_cli          -> packages/hello-cli/src
```

这样做有几个作用：

- Python import 可以正常工作。
- `[project.scripts]` 中定义的 CLI 命令可以被注册。
- pytest、pyright 等工具可以解析本地 package。

例如 CLI 入口：

```toml
[project.scripts]
hello-util = "hello_cli.main:main"
```

安装 `hello-cli` 后，可以这样运行：

```bash
uv run hello-util --name Codex
```

## `--all-groups` 是什么

`--all-groups` 表示安装所有 dependency groups。

例如：

```toml
[dependency-groups]
dev = [
    "pyright>=1.1.411",
    "pytest>=8.0",
    "ruff>=0.8",
]
```

执行：

```bash
uv sync --all-groups
```

会安装 `dev` 依赖组里的依赖。

如果以后增加更多依赖组：

```toml
[dependency-groups]
dev = ["ruff", "pytest", "pyright"]
docs = ["mkdocs"]
audit = ["pip-audit"]
```

那么 `--all-groups` 会安装 `dev`、`docs`、`audit` 这几个组。

## `--all-packages` 是什么

`--all-packages` 用于 workspace 项目。它表示把所有 workspace package 都安装进 `.venv`。

例如：

```text
packages/
  hello-cli/
  my-py-utils-common/
```

执行：

```bash
uv sync --all-packages
```

会安装：

```text
hello-cli 本身
hello-cli 的依赖
my-py-utils-common 本身
my-py-utils-common 的依赖
```

这对当前仓库很重要，因为根项目配置了：

```toml
[tool.uv]
package = false
```

也就是说，根项目不安装。真正有用的可安装单元是各个子 workspace package。

## 推荐同步命令

当前仓库推荐使用：

```bash
uv sync --all-groups --all-packages
```

含义是：

```text
安装所有开发工具依赖，以及所有 workspace 子 package。
```

`Makefile` 中将它封装成：

```bash
make dev
```

## pyright 作为类型门禁

当前仓库只使用 pyright 作为类型检查器。

目标是尽可能接近“强类型 Python”的开发体验。

当前配置：

```toml
[tool.pyright]
include = ["packages", "tests"]
pythonVersion = "3.11"
typeCheckingMode = "strict"
venvPath = "."
venv = ".venv"
```

含义是：

- pyright 检查 `packages` 和 `tests`。
- 启用 strict 模式。
- pyright 通过 `.venv` 解析本地包和依赖。

需要注意：Python 运行时仍然是动态类型语言。pyright 是静态检查工具，不是编译器。

如果想更接近强类型体验，需要配合这些习惯：

- 给函数参数和返回值都写类型。
- 尽量避免 `Any`。
- 在外部输入边界做校验。
- 对结构化数据使用 `TypedDict`、`dataclass` 或 `pydantic`。
- 在 CI 中强制执行 `pyright`。

## 常用命令

```bash
make dev
make check
make build
```

对应命令：

```bash
make dev
# uv sync --all-groups --all-packages

make check
# uv run ruff check .
# uv run pyright
# uv run pytest

make build
# uv build --all-packages
```

## 整体理解

可以这样理解当前仓库：

```text
my-py-utils/                 Git 仓库 / monorepo
  pyproject.toml             根配置，不作为 package 安装
  uv.lock                    统一锁文件
  packages/
    hello-cli/               CLI package
    my-py-utils-common/      共享库 package
  tests/                     workspace 级测试
```

核心思路：

```text
这个仓库用 uv workspace 管理多个 Python package。
uv sync --all-groups --all-packages 负责安装所有开发工具和所有子项目。
pyright strict 是类型门禁。
```
