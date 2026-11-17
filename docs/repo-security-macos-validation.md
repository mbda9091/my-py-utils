# repo-security macOS 原生验收

状态：已完成。完成：是。
执行、更新及完成日期：2026-11-17（Asia/Shanghai）。

结论：原生 macOS arm64 的 Python 3.11/3.14 × 单包/聚合 wheel 四组合全部通过，
repo-security 包内测试 51 passed、0 skipped。首次全仓 Pyright 发现的两条平台类型错误已在后续修复，复验见下文。

本轮按用户“完成 macos 验收”的要求，补充此前明确延期的平台；
[Linux M3](repo-security-m3-validation.md) 的完成范围和历史结果保持原样。

## 基线与交付

- 运行时实现基线：`482e452e72e36cfed00a49bd74b138aee5573347`。
- 本轮实现及证据提交：`3255c515337c8eb481e12be0c23281e44cecea5a`。
- `packages/repo-security/src/`、本仓库 `.security/` 与根 `uv.lock` 均与该基线一致。
- 本轮变更：验收脚本支持原生 macOS、独立 clone 重建、固定 `--uv` 路径及证据摘要；更新支持说明和验收记录。
- 后续按用户要求修复 `tests/test_private_pack_platform.py` 的平台类型判断，与验收脚本、README 和两份证据共同提交。
- 验收时基线为上述运行时提交加本轮工作区差异；提交后逐文件核对脚本、README、平台测试及证据字节一致，脚本摘要与两份证据一致，运行时与根锁文件保持基线内容。本轮已完成提交及证据对账。

## 实际环境

| 项目 | 实测值 |
|---|---|
| 系统 | macOS 26.7.1（25G241），Darwin kernel 25.6.0 |
| 架构 | arm64；Gitleaks、semgrep-core 均为 Mach-O arm64 原生二进制 |
| uv | 验收固定使用 0.11.28；系统原有 0.8.17 保持原样 |
| CLI Python | 3.11.11、3.14.6 |
| 独立扫描器 Python | 3.13.3 |
| 扫描器 | Gitleaks 8.30.1、Semgrep 1.185.0、Presidio Analyzer 2.2.366 |

只覆盖本机原生 arm64；Intel Mac、Linux arm64、原生 Windows 和远端 GitHub CI 未在本轮执行。

## 四组合与真实行为

| CLI Python | 独立 repo-security wheel | 聚合 my-py-utils-cli wheel | 持久证据 |
|---|---|---|---|
| 3.11.11 | 通过 | 通过 | [macOS Python 3.11](repo-security-evidence/macos-python311.json) |
| 3.14.6 | 通过 | 通过 | [macOS Python 3.14](repo-security-evidence/macos-python314.json) |

四组合均从 checkout 外的新环境安装最终 wheel，完成以下真实检查：

1. 包版本、模块来自隔离环境、CLI 环境不含扫描器依赖；聚合形态加载 11 个 workspace 包和 9 个命令，仅执行各命令 `--help`。
2. 中文/空格路径 dry-run 零写入；setup 锁定安装、验证三工具版本与合成风险样本后进入 `ready`。
3. 离线重复 setup 保持配置、清单及 Git 配置 mtime；doctor 保持包括扫描器环境在内的目标文件 metadata。
4. 生成配置真实提交成功，HEAD/history 手动扫描通过。
5. 同时暂存密钥、PII、eval 合成风险，再将工作区改成安全内容；真实 commit 仍被阻止，HEAD 不变，输出不含匹配原文。
6. 临时仓库构造风险中间提交后恢复安全文件；真实 push 仍被阻止，原安全提交能推送到本地 bare 远端。
7. 新 clone 不携带本机环境、二进制或清单；独立 `setup.py --install-hooks` 重建并通过 doctor。
8. clone 再次运行不带启用参数的独立安装器，配置不变且已启用 hooks 保持。

所有推送均指向临时本地远端。本轮隔离验收不改变当前源码仓库的 hooks 接入状态。

## 回归与质量检查

| 检查 | 结果 |
|---|---|
| repo-security 三个测试文件 | 51 passed、0 skipped，98.93 秒；JUnit：`.tmp/repo-security-macos/pytest.xml` |
| Ruff 全仓检查、验收脚本 format 检查 | 通过 |
| Pyright：repo-security 与四个相关测试/验收脚本 | 0 errors、0 warnings |
| Pyright：全仓 | 后续修复后复验通过：0 errors、0 warnings |
| private-pack 平台测试文件（后续修复回归） | 13 passed、8 skipped，4.64 秒；跳过项为 Windows/Linux 专用测试 |
| 全包构建 | 11 个 wheel、11 个 sdist 通过 |
| wheel 资源 | 14 项资源摘要、`py.typed`、本机环境/二进制排除检查通过 |

首次全仓 Pyright 的两条错误均位于 `tests/test_private_pack_platform.py:179`：
Linux 专用测试直接引用 `os.setxattr`，macOS 类型存根不提供该属性。
首次验收时该文件与实现基线一致。当日后续按用户要求，在调用处添加
`if sys.platform == "linux":` 显式平台判断，保留原 pytest 跳过规则。
修复后全仓 Ruff、全仓 Pyright 及受影响平台测试文件复验通过；修复已纳入本轮实现提交。
Linux 专用 ACL 测试在本机跳过，不作为 Linux 运行时通过证据。

包内测试使用真实扫描器覆盖提交/推送、反绕过、二进制/编码、大文件与子模块边界；
超时和畸形报告属于单独的故障注入测试，不冒充真实超时集成结果。
后续修复只增跑 `uv run --frozen pytest tests/test_private_pack_platform.py -rs`，未执行全仓业务测试。

## 安装耗时与恢复

首次冷缓存 setup 使用系统 uv 0.8.17，依赖下载超过安装器 600 秒限制，返回 2；
清单保持 `incomplete/deployed`，hooks 未启用。
随后使用固定 uv 0.11.28 预热同一锁文件，97 个依赖下载耗时 18 分 58 秒；
在原不完整仓库重试 setup 成功，Gitleaks 归档摘要匹配、三工具自检通过后进入 `ready/ready` 并启用 hooks。

矩阵复用共享缓存；本轮不声称空缓存首次下载性能通过。
早期探索进程为避免重复下载主动停止，仅最终 `final-python311/`、`final-python314/` 结果计入四组合结论。
脚本的 `--uv` 参数复用已安装的固定工具，避免为启动器再下载一份 uv。

## 产物与摘要

| 产物 | SHA-256 |
|---|---|
| `repo_security-0.2.0-py3-none-any.whl` | `5f0b5def0678ae1583e17256bcd1a9a800cddf4db9dd01aa55186ead1b08cad8` |
| `my_py_utils_cli-0.1.0-py3-none-any.whl` | `369c576599cd99b2d74b09face1e61c575888b6151129b4800fb3558db9394c1` |
| 根 `uv.lock` | `2961e51859e496a875ef1f13991f6fed770e83a77c22ed074757f9268eaef0a0` |
| 冻结 runtime constraints | `f2a60cf395b4477d03473b816e6f999583f6ec4490a8749109da43279cc7c229` |
| 验收脚本 | `0b9e9d5c428c7ecc432578a9a455206bf37863fc7c52cedf8ad2201310929d8f` |

两份 JSON 均保存全部 11 个 wheel 的摘要、解释器、命令入口和 Gitleaks 归档/二进制来源摘要；
已逐项核对实际文件与脚本一致。README 更新改变单包 wheel metadata，运行时模板保持原样。

## 复现与证据位置

以下从源码根目录执行。`<fixed-uv>` 使用固定 uv 0.11.28 的绝对路径，
`<fresh-dist>` 与两个 `<new-output>` 使用 checkout 外的新路径；脚本拒绝覆盖已有输出目录。

```bash
<fixed-uv> build --all-packages --out-dir <fresh-dist>
<fixed-uv> export --all-packages --frozen --no-dev --no-emit-workspace \
  --no-hashes --no-header --no-annotate --output-file <constraints>
<fixed-uv> run --frozen python tests/repo_security_acceptance.py \
  --uv <fixed-uv> --dist <fresh-dist> --constraints <constraints> \
  --output <new-output-311> --python 3.11
<fixed-uv> run --frozen python tests/repo_security_acceptance.py \
  --uv <fixed-uv> --dist <fresh-dist> --constraints <constraints> \
  --output <new-output-314> --python 3.14
REPO_SECURITY_TEST_RUNTIME=<installed-security> uv run --frozen pytest \
  tests/test_repo_security.py tests/test_repo_security_install.py \
  tests/test_repo_security_runtime.py -rs \
  --junitxml=.tmp/repo-security-macos/pytest.xml
```

本机最终 wheel 和原始临时仓库位于 `/private/tmp/repo-security-macos-OYxM3w/final-dist/`、
`final-python311/`、`final-python314/`，可能随临时目录清理失效。
构建、类型检查、首次失败安装日志与 JUnit 保存在 `.tmp/repo-security-macos/`；
两份 JSON 摘要随本记录持久保存，不提交虚拟环境、二进制或合成风险仓库。
