# private-pack 跨平台增量验收记录

日期：2026-11-04。对象：提交`57d7b841d023c13beb32ef5ef14ff4f3ca25f6ad`。
结论：本机验证及[首轮原生与互操作CI](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983)均通过：8个原生任务、3个消费者任务全部成功。

## 实现范围

平台存储代码、私有目录创建、排他发布能力探测、重解析点拒绝、Windows句柄与DACL、
失败清理报告、旧v1配置夹具、原生测试及3×3互操作流水线均已加入。
配置v1、32字节密钥、20位密码和16字节填充保持不变，无新增依赖、无需迁移。

Windows使用ctypes，受保护DACL只授权当前用户和LocalSystem。
父目录句柄不设置FILE_SHARE_DELETE，实际读取使用OPEN_REPARSE_POINT并检查属性；
仅接受本地NTFS，UNC、设备路径、ADS和不支持的路径别名拒绝。
CPython 3.11的stat/fstat以卷号与文件索引构成身份，
见[CPython实现](https://github.com/python/cpython/blob/3.11/Python/fileutils.c)。
句柄与共享语义依据[CreateFileW文档](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)，原生Windows两个Python版本的专项测试已通过。

macOS使用renamex_np(RENAME_EXCL)。创建私有对象前检查持有的父目录fd：存在任何扩展ACL即拒绝。
Darwin的ACL读取对有效fd返回ENOENT表示无ACL属性；其他错误失败关闭。
依据[Apple ACL实现](https://github.com/apple-oss-distributions/Libc/blob/main/posix1e/acl_file.c)，原生macOS两架构、两个Python版本的专项测试已通过。
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

## 已通过的原生矩阵

| runner | 架构 | Python | 结果 |
| --- | --- | --- | --- |
| ubuntu-24.04 | x64 | 3.11 / 3.14 | 两版本均通过 |
| macos-15 | ARM64 | 3.11 / 3.14 | 两版本均通过 |
| macos-15-intel | x64 | 3.11 / 3.14 | 两版本均通过 |
| windows-2022 | x64 | 3.11 / 3.14 | 两版本均通过 |

runner标签依据[GitHub runner参考](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。
本次四种runner均获得资源并完成锁文件依赖安装及原生测试；macOS ARM64/Python 3.14曾排队，随后正常完成。
流水线记录platform、architecture、Python、ImageVersion、依赖版本、提交和SHA-256。
Windows必要链接用例缺权限时直接失败，不静默跳过。Linux执行全仓回归，其他平台仅运行private-pack测试。

## 互操作与CI证据边界

[工作流](../.github/workflows/private-pack.yml)使用三个生产者：Ubuntu、macOS ARM64、Windows；
三个消费者分别下载当前run的全部生产者产物，形成9组消费组合。
每组解密配置、核对归档成员、运行独立7-Zip、比较目录与文件字节并测试错误密码/密钥。
消费者不把配置内容当代码执行；只接受源码定义的固定合成文件集合。
Actions固定官方仓库提交，禁用checkout持久凭证，权限只读，artifact保留3天。

CI run链接：[41684132983](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983)，结论`success`。
测试提交：`57d7b841d023c13beb32ef5ef14ff4f3ca25f6ad`。本次无需代码修复或重跑。
以下结果来自GitHub REST返回的job结论与artifact元数据；未在本机下载逐项审阅JUnit和镜像信息，
具体系统镜像版本、依赖及文件摘要保存在该run的证据artifact中。artifact保留3天，以下摘要保留在仓库。

## 原生任务与互操作任务

| 任务 | 结果 | 证据 |
| --- | --- | --- |
| native (macos-15, 3.14) | success | [job 126085245684](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/job/126085245684) |
| native (windows-2022, 3.11) | success | [job 126085246017](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/job/126085246017) |
| native (windows-2022, 3.14) | success | [job 126085246171](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/job/126085246171) |
| native (macos-15, 3.11) | success | [job 126085246230](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/job/126085246230) |
| native (macos-15-intel, 3.11) | success | [job 126085246236](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/job/126085246236) |
| native (ubuntu-24.04, 3.14) | success | [job 126085246375](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/job/126085246375) |
| native (macos-15-intel, 3.14) | success | [job 126085246400](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/job/126085246400) |
| native (ubuntu-24.04, 3.11) | success | [job 126085246557](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/job/126085246557) |
| interchange (windows-2022) | success | [job 126088558625](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/job/126088558625) |
| interchange (macos-15) | success | [job 126088558626](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/job/126088558626) |
| interchange (ubuntu-24.04) | success | [job 126088558973](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/job/126088558973) |

每个interchange任务均消费Linux、macOS、Windows三个生产者，因此共9组验证，
包含6条跨系统方向；解密、独立7-Zip解包、成员与字节比较、错误密码和错误密钥断言均未失败。

## GitHub artifact摘要

摘要为GitHub返回的artifact ZIP SHA-256，不是某一个归档文件的SHA-256。

| Artifact | SHA-256 |
| --- | --- |
| [fixture-macos-15](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/artifacts/13254891426) | `648875d11b65dd94163e2367122c7e12c93f0b4b70100f3c528e7897d60251f1` |
| [fixture-ubuntu-24.04](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/artifacts/13254102209) | `3a0b0f281d8d3a8c8ffa1edc61db27eb4df1d663e2c29d4760be6f860f3041c0` |
| [fixture-windows-2022](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/artifacts/13254986083) | `c9ec5063388e6aee9e5d46d84f95c12e30ffd60d94f2093812aec74fadd74305` |
| [interchange-macos-15](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/artifacts/13254158032) | `f40346d20edf8e665ecdd0f8b185bd2f33f6a781c729beb1a7a191572ceef0e0` |
| [interchange-ubuntu-24.04](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/artifacts/13253828099) | `9c6f84ba8aa44a250b48e4e072ca9667b43801ced57f7326d1fa9ae3a7c878da` |
| [interchange-windows-2022](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/artifacts/13254802761) | `db61e849d4c108c64cfd14799ab96e5e061e35a7f22033dd7749d7e8442b5602` |
| [native-macos-15-3.11](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/artifacts/13254486709) | `39b4119bb30c03499ad74b0839c030bde9c80324520d258029b28a5e23b24843` |
| [native-macos-15-3.14](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/artifacts/13255420918) | `8f3e5cc850ada4616e6e9405c9424b6a68345d241836279ef51fa525d3d05a40` |
| [native-macos-15-intel-3.11](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/artifacts/13254826594) | `583a6bd17f5edd8ebc35cfb789415b9f3c763863007ef2e3117bffe169be78d2` |
| [native-macos-15-intel-3.14](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/artifacts/13255215948) | `4559684742a69894bf23c3d6dabf1f6171a77fd7af218e9166d34117306a7c2b` |
| [native-ubuntu-24.04-3.11](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/artifacts/13254816864) | `234802158e696b22c68b1d74742968b16474466f810e0a63954a2ba0ba9cba73` |
| [native-ubuntu-24.04-3.14](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/artifacts/13253044686) | `20b379d84c309439bcca095ba3410869581831e8ab95d88f2cc0e3aac7671b41` |
| [native-windows-2022-3.11](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/artifacts/13254676670) | `2e4f336b3aa5f85a513654945242829900e2c15d36b108dfc823136cb23b3d62` |
| [native-windows-2022-3.14](https://github.com/mbda9091/my-py-utils/actions/runs/41684132983/artifacts/13254606709) | `357dd068a915c42ca50df5671131bca5d7b1dd1bb5081ee87fb7430d5bac7891` |

## 证据边界与后续

本次已证明当前自动化测试覆盖的原生行为及跨系统互操作。未扩大到网络共享、云盘、
FAT/exFAT或Windows ARM64；特殊文件名、大小写/规范化冲突及长路径仍需专门边界测试。
本次未因CI全绿自动宣布所有尚未编写的边界测试完成，正式支持文案仍作为后续收尾项。
