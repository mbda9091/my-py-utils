# repo-security M3 Linux 验收记录

日期：2026-11-17（Asia/Shanghai）。关联：[方案](repo-security-plan.md)、[M2 验收](repo-security-m2-validation.md)。

结论：Linux x64 的单包与聚合 wheel、CLI Python 3.11/3.14 四种组合均通过真实验收，实现提交和证据已对账，本轮已完成。
用户明确要求“没有 mac，先跳过吧，先支持 linux 验收”，因此本轮以 Linux 为完成边界；
macOS、其他架构及原生 Windows 均不计为通过，也不再阻塞本轮完成。

## 基线与完成范围

- 实现 commit：`fd0e2015c71bd0d93f502a5bc056d0655dc73c44`。验收时基线为 `b22881ea87bfe6eea320488d279f6b10004cc391` 加 M2/M3 工作区改动；提交后已核对与对应实现文件一致。
- repo-security 运行时、规则及锁文件与 M2 已测 wheel 逐文件一致；M3 修改的是支持说明、验收脚本和文档，不新增扫描器依赖。
- M2 的 51 项包内测试结果沿用已核对的运行时代码基线；本轮新增的是独立 wheel 原生验收，未执行其他 CLI 的业务测试。
- 完成项：聚合注册、最终使用说明、两种 Python 下的单包/聚合独立安装、真实 hooks、文件资源校验、提交与证据对账。
- 不包含对外发布、远端 GitHub CI 或账号保护设置；没有推送代码或上传合成风险样本。

## 实际环境

| 项目 | 实测值 |
|---|---|
| 系统及架构 | Linux x86_64，Ubuntu 22.04.5 LTS 用户态 |
| 内核 | `6.18.58.1-microsoft-standard-WSL2` |
| libc | glibc 2.35 |
| 临时文件系统 | `stat -f` 报告 `ext2/ext3`；未测试 Windows 挂载盘 |
| uv | 0.11.28 |
| CLI Python | 3.11.15、3.14.6 |
| 扫描器 Python | 独立的 3.13.7 |
| 扫描器版本 | Gitleaks 8.30.1、Semgrep 1.185.0、Presidio Analyzer 2.2.366 |

这是在 Linux 进程/内核上运行真实工具的结果；不等于原生 Windows 验收，也不推断未测发行版、
文件系统或架构全部兼容。保留的 macOS 下载摘要只是模板数据，不构成支持声明。

## 验收矩阵与持久证据

| CLI Python | 独立 repo-security wheel | 聚合 my-py-utils-cli wheel | 证据 |
|---|---|---|---|
| 3.11.15 | 通过 | 通过 | [Linux Python 3.11 结果](repo-security-evidence/linux-python311.json) |
| 3.14.6 | 通过 | 通过 | [Linux Python 3.14 结果](repo-security-evidence/linux-python314.json) |

四种组合全部从源码 checkout 外的全新 uv 环境安装 wheel，并完成：

1. 确认 repo-security 从独立环境加载，不借用源码/可编辑安装；CLI 环境不包含 Semgrep 或 Presidio。
2. 核对单包版本；聚合形态核对 11 个 workspace 包版本、9 个 console-script 注册及加载，仅执行各命令 `--help`。
3. 中文/空格路径仓库的 dry-run 零写入、真实 setup 和三工具自检，ready 清单与本地 hooks 生效。
4. 离线重复 setup 配置 mtime 不变；doctor 对包括扫描器环境在内的所有目标文件不产生 metadata 变化。
5. 生成配置真实提交成功，HEAD/history 手动扫描通过。
6. 暂存密钥、PII、eval 三类合成风险，工作区改回安全内容；真实提交被阻止，HEAD 不变，输出不含原文。
7. 在临时仓库中绕过提交 hook 构造风险中间提交，再恢复安全文件；真实推送仍被阻止。推送原安全提交到本地 bare 远端成功。

原生验收不是 mock。回退、超时、畸形报告等故障路径的注入证据仍见 M2；未把它们冒充成真实故障耗时。

## 产物与摘要

| 产物 | SHA-256 |
|---|---|
| `repo_security-0.2.0-py3-none-any.whl` | `a1836fc30f808f590cbd2631e7a89e3ae1a36f1c6793af83f1c79d6bfbeeede2` |
| `my_py_utils_cli-0.1.0-py3-none-any.whl` | `369c576599cd99b2d74b09face1e61c575888b6151129b4800fb3558db9394c1` |
| 根 `uv.lock` | `2961e51859e496a875ef1f13991f6fed770e83a77c22ed074757f9268eaef0a0` |
| 冻结导出的 runtime constraints | `f2a60cf395b4477d03473b816e6f999583f6ec4490a8749109da43279cc7c229` |
| 验收脚本 | `189c6a63ff6b80bae08e2a650b4f0388e97dc1bcf1db0187965bb86d5d66e0bd` |

每份持久 JSON 同时保存全部 11 个 workspace wheel 的 SHA、真实系统/解释器和命令注册。
相比 M2 wheel，当前单包产物的支持说明等 metadata 已更新；包内全部运行时/模板字节仍与 M2 一致。
最终验收使用本表的新产物；早先探索轮次的产物不混入完成结论。

## 可复现命令

以下从仓库根目录执行；`<fresh-dist>`、`<constraints>` 和各 `<new-output>` 使用 checkout 外的新路径。
验收脚本拒绝覆盖已有输出目录。首次安装需要联网，Python/包依赖可能使用 uv 共享缓存。

```bash
uv build --all-packages --out-dir <fresh-dist>
uv export --all-packages --frozen --no-dev --no-emit-workspace \
  --no-hashes --no-header --no-annotate --output-file <constraints>
uv run python tests/repo_security_acceptance.py \
  --dist <fresh-dist> --constraints <constraints> \
  --output <new-output-311> --python 3.11
uv run python tests/repo_security_acceptance.py \
  --dist <fresh-dist> --constraints <constraints> \
  --output <new-output-314> --python 3.14
```

本机原始产物和临时 Git fixture 位于 `/tmp/repo-security-m3-Bxl09i/final-dist/`、
`final-python311/`、`final-python314/`，可能随清理失效。JSON 摘要已保存入仓库；
不提交虚拟环境、二进制、合成风险提交或完整原始报告。

静态检查 `uv run ruff check .`、新增代码 format 检查和 `uv run pyright` 均通过，
`uv build --all-packages` 的全部 sdist/wheel 构建通过。聚合验证只检查依赖与命令入口，
没有运行 private-pack 或其他包的业务测试，也未触发旧的远端全仓工作流。

## 延期与后续范围

- macOS 原生验收由用户明确延期；Linux arm64、原生 Windows及其他文件系统未验收。
- 原模板对外许可证未声明，发布边界仍见 NOTICE；本轮只完成个人仓库的本地交付。
- 真实 GitHub Actions 和远端保护保持按需建设，生成 YAML 不代表远端检查已经运行。
- 自动升级、卸载及第三方 hook 串联仍为既定非目标，不作为本轮欠项。
