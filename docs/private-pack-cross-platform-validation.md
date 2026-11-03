# private-pack 跨平台增量验收记录

日期：2026-11-04。对象：`dab4134`基础上的当前工作区变更。
结论：实现与CI已落地，Linux验证通过，**跨平台尚未验收完成**。

## 实现范围

平台存储代码、私有目录创建、排他发布能力探测、重解析点拒绝、Windows句柄与DACL、
失败清理报告、旧v1配置夹具、原生测试及3×3互操作流水线均已加入。
配置v1、32字节密钥、20位密码和16字节填充保持不变，无新增依赖、无需迁移。

Windows使用ctypes，受保护DACL只授权当前用户和LocalSystem。
父目录句柄不设置FILE_SHARE_DELETE，实际读取使用OPEN_REPARSE_POINT并检查属性；
仅接受本地NTFS，UNC、设备路径、ADS和不支持的路径别名拒绝。
CPython 3.11的stat/fstat以卷号与文件索引构成身份，
见[CPython实现](https://github.com/python/cpython/blob/3.11/Python/fileutils.c)。
句柄与共享语义依据[CreateFileW文档](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)，尚需原生测试证明实现效果。

macOS使用renamex_np(RENAME_EXCL)。创建私有对象前检查持有的父目录fd：存在任何扩展ACL即拒绝。
Darwin的ACL读取对有效fd返回ENOENT表示无ACL属性；其他错误失败关闭。
依据[Apple ACL实现](https://github.com/apple-oss-distributions/Libc/blob/main/posix1e/acl_file.c)，需原生用例复核。
输入与密钥路径的父组件也拒绝链接，因此macOS测试显式使用`/private/tmp`，避免`/tmp`系统链接。

## 本机证据

| 项目 | 环境/结果 |
| --- | --- |
| OS | WSL2 Linux 6.18.54.1-microsoft-standard-WSL2，x86_64，glibc 2.35 |
| Python/uv | CPython 3.11.15 / uv 0.11.28 |
| 核心依赖 | py7zr 1.1.4，cryptography 48.0.1 |
| 独立解包器 | 7-Zip 26.05 x64 |
| `uv run ruff check .` | 通过 |
| `uv run pyright` | 0 errors |
| `PRIVATE_PACK_7Z=… uv run pytest` | 173 passed，4 skipped |
| 跳过说明 | Windows junction/父目录锁、Windows DACL、Windows文件占用、macOS ACL；Linux本机不适用 |
| Linux ACL | 直接设置包含命名用户的默认ACL，私有对象权限受mode掩码限制；通过，无缺工具跳过 |
| `uv build --all-packages` | 全部10个包的sdist和wheel通过 |
| 隔离wheel | 新uv venv安装构建出的private-pack wheel；三个真实CLI命令、默认/显式输出通过 |
| 本机互操作 | wheel生产夹具后，由独立7-Zip消费；所有成员及字节、填充、摘要、错误密钥/密码断言通过 |
| `git diff --check` | 通过 |

本机会话临时证据位于`/tmp/private-pack-full-suite.xml`及
`/tmp/private-pack-cross-cli-consumed/result.json`；临时文件不会随仓库分发。
合成旧v1夹具保存在`tests/fixtures/private-pack/legacy-v1.json`，由基线`dab4134`的manifest代码生成。
其中密钥和密码是公开测试数据，无真实资料。

## 待执行的原生矩阵

| runner | 架构 | Python | 结果 |
| --- | --- | --- | --- |
| ubuntu-24.04 | x64 | 3.11 / 3.14 | 待CI；本机WSL不替代此runner |
| macos-15 | ARM64 | 3.11 / 3.14 | 未运行 |
| macos-15-intel | x64 | 3.11 / 3.14 | 未运行 |
| windows-2022 | x64 | 3.11 / 3.14 | 未运行 |

runner标签依据[GitHub runner参考](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。
仓库实际配额、runner访问、Windows链接权限、APFS/NTFS行为和全部锁文件依赖安装仍待实测。
流水线记录platform、architecture、Python、ImageVersion、依赖版本、提交和SHA-256。
Windows必要链接用例缺权限时直接失败，不静默跳过。Linux执行全仓回归，其他平台仅运行private-pack测试。

## 互操作与CI证据边界

[工作流](../.github/workflows/private-pack.yml)使用三个生产者：Ubuntu、macOS ARM64、Windows；
三个消费者分别下载当前run的全部生产者产物，形成9组消费组合。
每组解密配置、核对归档成员、运行独立7-Zip、比较目录与文件字节并测试错误密码/密钥。
消费者不把配置内容当代码执行；只接受源码定义的固定合成文件集合。
Actions固定官方仓库提交，禁用checkout持久凭证，权限只读，artifact保留3天。

CI run链接：**无，尚未推送或触发运行**。
跨平台artifact摘要：**无，不能用Linux自消费代替**。
macOS/Windows代码不因编写完成而视为原生验收通过，README/CLI正式支持范围尚未扩大。

## 后续验收

运行完整矩阵及互操作作业，核对所有必要测试未被跳过，并补齐原生路径边界、权限继承、
占用/清理及真实文件系统行为证据。若runner或依赖不可用，记录对应受阻项。
全部A1–A8必要项通过后，才更新正式平台支持声明并标记方案完成。
