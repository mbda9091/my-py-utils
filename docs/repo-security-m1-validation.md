# repo-security M1 验收记录

日期：2026-11-17（Asia/Shanghai）。关联：[方案](repo-security-plan.md)。

本次结论：M1 最小链路在 Linux x64 通过本机验收，已提交为 `c6e8acf58c0f40265c707acdfd1e361bf5aab133`；不代表完整首版验收完成。

## 基线与产物

- 验收时源码基线：`b8413576192911693c7210ab0ce18940a297f618` 加当时未提交的 repo-security 包、测试、聚合注册、README、`uv.lock` 和方案/验收文档。
- 实现提交：`c6e8acf58c0f40265c707acdfd1e361bf5aab133`；已核对实现与验收时工作区文件一致。提交后仅改文档及 AGENTS 的测试范围约定，未改运行时代码，无须重跑功能测试。
- wheel：`dist/repo_security-0.1.0-py3-none-any.whl`。
- wheel SHA-256：`a884876e1f2ea7b8186477593549f5ef1fefa18546c650b4591a07212bff8676`。
- [资源清单](../packages/repo-security/src/repo_security/templates/manifest.json)：12 项原模板文件及 1 项新增自检资源；逐项保存原始和发布 SHA-256。原模板安装器及 pyproject 经适配，其余原文件原样保留。
- 版本：repo-security 0.1.0；Gitleaks 8.30.1；Semgrep 1.185.0；Presidio Analyzer 2.2.366。
- 环境：Linux x86_64；CLI Python 3.11.15；扫描器 Python 3.13.7；uv 0.11.28。

原模板不属于 Git 仓库，没有可记录的源提交；原始摘要用于固定来源快照。
来源许可证缺失及本地迁移范围已记录在 [NOTICE](../packages/repo-security/NOTICE.md)。
本次未对外发布任何产物。

## 已执行检查

| 检查 | 执行方式 | 结果与边界 |
|---|---|---|
| 资源完整性 | 校验清单每个发布摘要；编译全部 Python 模板 | 通过；不依赖原机器目录 |
| 资源损坏拒绝 | 替换测试 bundle 的资源内容，验证在写目标前失败 | 通过；目标 `.security/` 未创建 |
| 安装前冲突 | 已有目录/断链、忽略文件符号链接、已有 hook/hooksPath | 通过；不改忽略内容、不部署新目录 |
| 真实首次接入 | `uv run --package repo-security repo-security setup <temp-repo>` | 通过；新建独立 Python 环境，锁定同步，下载摘要/版本验证，三工具自检后启用 hooks |
| 真实样本 | 安装器自检，干净文件与三类合成命中 | 通过；验证 Gitleaks、Semgrep、Presidio 都实际执行，不输出原文 |
| Git 提交 | 真实扫描器 + 临时 Git 仓库，分别提交干净/密钥/危险代码/PII 样本 | 通过；检查索引版本，工作区保留安全的未暂存修改，未修改索引 |
| 反绕过及错误 | nosemgrep、`.semgrepignore` 样本；损坏 Semgrep 规则 | 通过；豁免不绕过，配置错误返回 2 |
| 全仓测试 | `REPO_SECURITY_TEST_RUNTIME=<installed-security> uv run pytest -rs` | 189 passed / 9 skipped，17.70 秒；跳过不算通过 |
| Ruff | `uv run ruff check .`，新增文件 `ruff format --check` | 全部通过；模板数据另做语法编译和真实运行校验 |
| Pyright | `uv run pyright` | 0 errors / 0 warnings；模板依赖不进入 CLI 环境 |
| 全仓构建 | `uv build --all-packages` | 所有 workspace 包的 sdist/wheel 构建通过 |
| wheel 内容 | 直接读取 zip，逐个检查 13 项资源摘要、NOTICE、py.typed 及本机产物排除 | 通过；不包含虚拟环境、二进制或缓存 |
| wheel 隔离接入 | 临时 uv 环境安装单包 wheel，从仓库外执行第二次 setup | 通过；无需源模板目录或源码导入 |
| 配置自身可提交 | 将生成 `.security/`、忽略文件及干净样本暂存，实际 git commit | 通过；14 files、0 findings、0 PII coverage gaps |
| wheel 手动 HEAD 扫描 | 隔离环境 `repo-security scan <temp-repo> --mode head` | 退出 0；1 unique snapshot、14 files、0 findings |

真实测试通过 `REPO_SECURITY_TEST_RUNTIME` 指向已安装的 `.security/`，仅在临时夹具中
复用其只读扫描器环境。未指定该变量时，五项真实扫描测试明确跳过，不能把默认 pytest
结果当作三个扫描器已经验收。全仓九项跳过均来自既有 private-pack：一项需配置
`PRIVATE_PACK_7Z`，一项需 macOS 扩展 ACL，七项需原生 Windows。它们不计入通过证据。

## 本机临时证据

原始输出保存在本次会话，未额外存完整日志。以下目录仅供当前机器复核，可能被清理：

- `/tmp/repo-security-m1-KYPZQ3`：源码入口真实安装及集成测试所用扫描器环境。
- `/tmp/repo-security-m1-wheel-env`：Python 3.11 隔离 wheel 安装。
- `/tmp/repo-security-m1-wheel-repo-8wOT6S`：隔离 wheel 生成仓库、实际提交及 HEAD 扫描。
- `/tmp/repo-security-m1-final-peHJfl`：最终 wheel 在错误输出与全局 hooks 测试补齐后的接入、14 文件配置提交及 HEAD 扫描复验，均通过；对应本文最终产物摘要。

临时测试仓库中的提交只是夹具，不是本项目实现提交；不能将其 SHA 作为实现基线。

## 尚未覆盖

- M2：只读预览、诊断、幂等认领、安装清单、故障注入/重试恢复、并发配置变更、可选 CI 生成。
- 真实 push 的中间提交、多 ref、删除 ref 等完整场景；pre-push 代码已迁移但本轮未验收。
- 所有异常边界、浅克隆提示和完整 history CLI 行为。
- 聚合 wheel 的隔离安装；本轮已注册并构建，尚未独立消费聚合产物。
- macOS、Linux arm64、其他解释器组合；仅 Linux x64 为本轮实测环境。
- 真实 GitHub Actions、远端保护及发布；本轮未执行。

M1 允许对新仓库完成首次安装；遇到已有 `.security/` 会拒绝接入。安装失败可能保留
生成文件与部分依赖，当前无自动恢复或回滚清单。M2 前使用须按 README 的预览边界处理。
