# repo-security M2 验收记录

日期：2026-11-17（Asia/Shanghai）。关联：[方案](repo-security-plan.md)、[M1 记录](repo-security-m1-validation.md)。

结论：M2 在 Linux x64 完成本机验收，改动未提交。完整首版仍欠 M3 平台与聚合隔离安装验收。

## 基线与产物

- 源码基线：`b22881ea87bfe6eea320488d279f6b10004cc391`加当前未提交的 M2 包、测试、README、NOTICE、资源清单、版本与锁文件改动。实现提交：未提交。
- 版本：CLI/模板 `0.2.0`；扫描器仍为 Gitleaks `8.30.1`、Semgrep `1.185.0`、Presidio Analyzer `2.2.366`。
- 环境：Linux x86_64，uv `0.11.28`，CLI Python `3.11.15`，扫描器 Python `3.13.7`。独立 clone 安装器由系统 `python3` 引导，再由 uv 选择扫描器 Python。
- wheel：`dist/repo_security-0.2.0-py3-none-any.whl`。
- wheel SHA-256：`a09b49d4bbcc92d74d051710a08b5867e9d04c5329d2a41150f93941908a2c85`。
- wheel 中 14 项资源摘要均与[清单](../packages/repo-security/src/repo_security/templates/manifest.json)一致；无本机环境、二进制或缓存。
- 无新增 Python 依赖；根 `uv.lock` 仅更新 repo-security 版本。扫描器锁文件及安装版本保持不变。

## 测试范围与结果

遵循更新后的 AGENTS，仅运行三个 repo-security 测试文件，未执行其他 CLI 的业务测试。

```bash
REPO_SECURITY_TEST_RUNTIME=<installed-security> uv run pytest \
  tests/test_repo_security.py \
  tests/test_repo_security_install.py \
  tests/test_repo_security_runtime.py -rs
```

最终结果：**51 passed，0 skipped，32.11 秒**。包含真实扫描器、真实临时 Git 仓库及本地 bare 远端。
`REPO_SECURITY_TEST_RUNTIME` 仅提供已安装的扫描器依赖，扫描脚本、规则和 hooks 使用当前包资源。
未设置此变量时相关真实集成测试会跳过，不可视作三扫描器验收通过。

| 范围 | 已验证结果 | 证据层级 |
|---|---|---|
| 预览与输入 | 不写文件、配置、权限、锁或环境；冲突汇总；无效路径、bare、linked worktree 拒绝 | 文件快照、CLI 测试；安装函数注入禁止运行 |
| 幂等与认领 | 同版本重复接入，资源/清单/Git 配置 mtime 保持；无清单精确认领；保留 CRLF 忽略文件及无关文件 | 隔离文件测试及真实 wheel 重复接入 |
| 定制和旧版本 | 自定义规则、第三方 hooks、旧版清单、同名工作流拒绝覆盖 | 本地 Git/文件系统；global/local hooks 来源测试 |
| 失败与恢复 | 部署后失败记录 incomplete，重试不重写完整资源；已启用保护不因失败修复被停用 | 故障注入；真实损坏 Gitleaks 修复 |
| 下载安全 | 摘要错误不发布二进制；可信归档复用；损坏二进制重新获取；许可证保留 | 合成归档注入及真实固定发行包下载 |
| 文件与并发 | 符号链接拒绝；两个真实进程争用同仓库锁；安装期间外部 hooks 改动保留 | 文件系统、独立 Python 进程及配置故障注入 |
| 激活回退 | 激活后复核失败恢复本次 local 设置；并发写入的不同值不覆盖；最终清单写入失败也撤回初次激活 | 针对激活/元数据故障的注入测试 |
| 诊断 | 缺失、不完整、定制、修改过的运行时/二进制分别报告；不执行摘要不可信的运行时 | 单元测试及真实 wheel doctor |
| 提交 | 三工具真实命中、干净提交成功、风险提交阻止、部分暂存、匹配原文不输出 | 真实 git commit 与实际扫描器 |
| 推送 | 正常推送、中间提交 PII、新 ref、多 ref/标注 tag、删除 ref | 真实 git push 到临时本地 bare 远端，不访问互联网远端 |
| 扫描边界 | Gitleaks/Semgrep 源码豁免及忽略文件不绕过；大文件和子模块阻止；二进制/非支持编码提示缺口 | 实际扫描器；超时/畸形报告为单独故障注入 |
| 手动扫描 | staged、HEAD、history；浅克隆明确警告且不 fetch | 当前运行时、真实 Git 对象及浅克隆 |
| 可选 CI | 默认不生成，显式生成、保留其他工作流、后续无 flag 仍管理已接入工作流；结构经 PyYAML 解析 | 文件行为测试及扫描器环境中真实 YAML parser；未运行 GitHub CI |

## 隔离 wheel 端到端

从源码目录外执行本地构建 wheel 的 CLI，使用含中文及空格的临时仓库路径，完成：

1. `setup --dry-run --github-actions`：目标文件快照一致，未创建 `.security/`。
2. `setup --github-actions`：锁定安装、三扫描器合成样本验证、hooks 激活、ready 清单。
3. `UV_OFFLINE=1` 下重复 setup：配置资源、清单与 Git 配置内容/mtime 不变。
4. `doctor`：包括 `.venv` 在内的全部目标文件 metadata 快照一致；实际版本、规则与合成检测通过。
5. 人为损坏临时 Gitleaks：doctor 返回 1；setup 校验并恢复官方二进制摘要。
6. 将生成配置实际提交，随后 HEAD/history 扫描通过，零命中。
7. 新 clone 不带本机清单、环境或二进制，使用 `python3 .security/setup.py --install-hooks` 独立重建，再运行 wheel doctor 通过。
8. 对已启用 hooks 的 clone 运行不带 flag 的 standalone installer：保护保持，ready 状态及配置文件 mtime 不变。

实际 smoke 脚本及结果：`/tmp/repo-security-m2-wheel-gd4GFW/smoke.py` 和 `smoke-result.json`。
其 `env/` 为单包 wheel 隔离环境，`目标 repo/` 为真实生成仓库，`independent-clone/` 为独立引导验证仓库。
这些本机临时证据可能被清理；本记录保存必要摘要，不保存扫描器原始报告或业务敏感原文。

## 质量、构建与聚合入口

- `uv run ruff check .`：通过；新增和修改 Python 的 format 检查通过。
- `uv run pyright`：0 errors / 0 warnings；共享安装引擎为普通 typed Python，同步接受这些检查。
- `uv build --all-packages`：全部 sdist/wheel 构建通过。按 AGENTS 执行构建不代表运行全仓业务测试。
- 单包 wheel 资源逐项 SHA-256 校验、`py.typed` 和本机产物排除检查通过。
- `uv run --package my-py-utils-cli repo-security --help` 及聚合包 entry-point 加载检查通过；未独立安装聚合 wheel，留待 M3。

## Actions 固定提交

2026-11-17 使用 `git ls-remote <official-repository> refs/tags/<version>` 验证并固定：

| 官方仓库 | tag | 已生成工作流使用的 SHA |
|---|---|---|
| `https://github.com/actions/checkout.git` | v4 | `11d5960a326750d5838078e36cf38b85af677262` |
| `https://github.com/actions/setup-python.git` | v5 | `a26af69be951a213d495a4c3e4e4022e16d87065` |
| `https://github.com/astral-sh/setup-uv.git` | v6 | `d0d8abe699bfb85fec6de9f7adb5ae17292296ff` |

这证明生成时引用了明确的官方 tag 对应对象，不替代远端工作流实际运行或完整 Action 实现审计。

## 尚未覆盖

- macOS、Linux arm64 等平台及更多 CLI Python 组合；当前只有 Linux x64 实测。
- 聚合 wheel 的隔离安装形态、M3 的最终提交与证据对账。
- 真实 GitHub Actions、远端保护和真实用户目标仓库接入；本轮只生成 YAML，不推送或设置账号。
- 任意恶意并发目录替换、断电持久性及完整缓存回滚；文件/Git 配置变更在明确检查点复核，不宣称全文件系统事务。
- 自动升级、卸载、第三方 hook 串联及其他语言漏洞库，仍属既定非目标。

M1 到 0.2.0 不提供自动升级；同版本原样资源可认领，旧版或定制内容须由用户审查后迁移。
原始模板缺失许可证的对外发布边界仍按 NOTICE 保留，本次未发布任何产物。
