# private-pack v0.1.0 验收报告

日期：2026-11-04（Asia/Shanghai）。状态：Linux本机验收通过。

关联：[主方案](private-pack-design.md)、[实施清单](private-pack-implementation.md)、
[使用说明](../packages/private-pack/README.md)、[测试源码](../tests/test_private_pack.py)。

## 基线与环境

- 基线提交：`0afb6ef`，加本次未提交实现、依赖、测试和文档；未创建Git提交。
- 系统：Linux 6.18.54.1-microsoft-standard-WSL2，x86_64。
- Python：3.11.15；py7zr：1.1.4；cryptography：48.0.1；完整依赖见 `uv.lock`。
- 独立解压器：7-Zip 26.05 Linux x64，来自[官方发布](https://github.com/ip7z/7zip/releases/tag/26.05)。
- 测试材料：临时目录中的合成数据，包括中文名称、隐藏文件、空目录、空文件和二进制内容；未读取用户待打包资料。
- 所有测试密钥、明文配置、解包产物限于临时目录，不进入仓库。

## 首版版本标识（默认输出迭代前）

| 对象 | SHA-256 |
| --- | --- |
| `private_pack-0.1.0-py3-none-any.whl` | `bc00f7d3390d832526460ad20988388a3fb9b218e29b6a0983778460dd7ae607` |
| `my_py_utils_cli-0.1.0-py3-none-any.whl` | `52ff909ff8ccf9dffb3cab492c6e0c5ccb4e7136af5898b4354e9be476bb511f` |
| 独立7-Zip可执行文件 | `d32657a7a0e4cdc0a8aa57cafe474746944d76e861e753e9c902b9c9356fa886` |
| 12个实现、测试与依赖文件集合 | `8bf2c3d8e0eecd7a590336131160be2b787f0bb169cc05fba16a35b4e7c56bdd` |

文件集合包括：`packages/private-pack/src/` 下全部 `.py`、新包pyproject、
`tests/test_private_pack.py`、聚合包pyproject、node-modules-cleaner的cli.py以及 `uv.lock`。
按仓库相对路径字典序，逐个对“UTF-8路径 + NUL + 原始文件字节 + NUL”更新SHA-256。
此摘要不包含文档，便于后续补充说明而不改变实现身份。

## 实际检查结果

命令从仓库根目录执行。独立7-Zip路径是本机临时路径；其他环境需自行提供对应可执行文件。

| 检查 | 结果 | 摘要 |
| --- | --- | --- |
| `uv sync --all-groups --all-packages` | 通过 | 恢复全部workspace依赖，历史导入错误消失 |
| `uv run ruff check .` | 通过 | All checks passed |
| `uv run pyright` | 通过 | 0 errors、0 warnings |
| `PRIVATE_PACK_7Z=/tmp/private-pack-7z/7zz uv run pytest` | 通过 | 157 passed，7.21秒，0跳过 |
| `uv build --all-packages` | 通过 | 10个包的sdist及wheel均构建成功 |
| 独立wheel隔离安装 | 通过 | 仓库外执行keygen、pack、decrypt-config，检查两条映射及包SHA-256 |
| 聚合wheel隔离安装 | 通过 | 全部workspace依赖显式安装本地wheel；相同链路通过 |
| `uv run private-pack --help` | 通过 | 三个子命令已注册 |
| 文档结构、相对链接、JSON示例 | 通过 | 本轮收尾脚本检查 |

隔离安装使用uv创建两个 `/tmp/private-pack-*` 环境，未修改全局工具安装。
聚合安装先出现公开仓库同名video2audio候选，随后显式安装 `dist/*.whl` 将所有workspace包
固定为本次本地产物，再执行冒烟验证。报告中的聚合通过结论对应修正后的本地wheel环境。

## A1–A9证据映射

| 编号 | 测试与验证入口 | 结果及边界 |
| --- | --- | --- |
| A1 | `test_pack_roundtrip_and_repeat`、`test_independent_7zip` | 一级分别打包、嵌套/隐藏/空目录/空文件还原，字节一致 |
| A2 | `test_password_plan_and_repr`、完整往返 | 20位三类字符、配置顶层统一密码、每包16字节填充 |
| A3 | `test_independent_7zip` | 无密码/错密码不能列名；错密码不能解压；正确密码独立解压全部条目 |
| A4 | `test_pack_roundtrip_and_repeat` | 重复打包名称及MD5不同，原名、包大小、SHA-256匹配；概率验收 |
| A5 | 配置往返、六类认证破坏、非法JSON、重复/未完成条目、短密钥测试 | 拒绝损坏配置和不合法结构，错误密钥不会产生明文输出 |
| A6 | 七类非法输入、父级链接、符号链接输出、已有与并发目标测试 | 拒绝链接/FIFO/输入范围内密钥/硬链接；不覆盖原目标 |
| A7 | 三类失败注入、源变化、私有文件写失败 | 正常异常不发布最终目录且清理本次暂存；源不变 |
| A8 | `test_cli_and_private_outputs`、完整往返 | 密码不出现在CLI捕获输出或Manifest repr；0600/0700；最终目录只含包与密文配置 |
| A9 | 完整检查与两套安装冒烟 | workspace与wheel安装均可运行三个命令 |

新功能共35项测试，包含参数化场景；完整仓库共157项。
独立7-Zip不可用时测试会显式跳过兼容性项，本次已提供该依赖，未跳过。

## 已修正问题

1. 历史环境缺少codex-hook-ctl、PyYAML等workspace依赖：完整uv同步后Pyright通过。
2. 基线pytest为121通过、1失败：`find_node_modules` 返回os.walk发现顺序，
   与既有排序断言不稳定。返回列表排序后全套通过，没有改变删除范围。
3. 发布目标可能在预检后出现：使用Linux `renameat2(RENAME_NOREPLACE)`，
   已有及并发创建的目录都保持不变；不采用普通覆盖式rename作为回退。

## 证据边界

这是本机自动化和显式CLI冒烟记录，不是CI、用户真实资料体验或其他平台验收。
没有断电持久性、恶意并发修改整棵源目录、无限深目录或任意规模性能承诺。
源变化检测不等于快照，内容按字节还原，不承诺权限、时间戳及扩展属性保真。
文件名的反斜杠等不支持字符会被拒绝；受文件系统文件名长度限制的原始包名会报错并清理。
配置明文上限16 MiB，超限不提交。强制终止可能残留暂存目录，首版不恢复这些中间产物。

完整命令输出见本次会话，未另存完整日志；本报告保留必要摘要与产物摘要。
`dist/`、隔离环境和独立7-Zip临时文件均未提交，可能随清理失效。

## 默认输出目录迭代（2026-11-04）

`pack --output` 可选，默认在输入目录同级创建“输入目录名-packed”；仍拒绝覆盖已有目录。
新增3种输入形式的CLI测试（绝对路径、相对路径含末尾斜杠、`.`），每项也验证重复执行不会覆盖。
本轮完整测试160通过，Ruff与Pyright通过，10包构建通过。初版独立7-Zip测试继续实际执行。
上文35项新工具测试为首版历史记录；当前新工具测试38项。

- 当前源码集合SHA-256：`c2389600c31f73c13ff9f01529aed0284d7c359f4c62658b396ba2ea444fef03`（计算范围和方式同上）。
- 刷新后的private-pack wheel SHA-256：`b67941b1cf5a991edce5dfd1319ddc958b86916bcb27a1e21c7de6f3fa35dfd4`。
- 聚合包wheel未变；本轮未重复隔离安装，初版安装结果不作为新wheel独立安装证明。

本节为当前实现的增量证据，上文原始哈希及157项结果保留为历史记录。
