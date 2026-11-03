# private-pack

将输入目录下每个一级子目录或独立文件分别打成7z加密包，随机重命名，
并用独立主密钥保护名称映射与解压密码。首版打包支持Linux；不执行自动改回包名或解压。

## 使用

从仓库根目录运行 `uv sync --all-groups --all-packages` 后：

```bash
# 确保 secrets 目录已经存在且在输入目录之外
uv run private-pack keygen --output ./secrets/master.key

# output 必须不存在，父目录必须存在，且 output 位于 input 之外
uv run private-pack pack ./input --output ./output --key-file ./secrets/master.key

# 省略 --output 时，默认在输入目录旁生成 input-packed
uv run private-pack pack ./input --key-file ./secrets/master.key

# 仅解密配置；生成的明文 JSON 包含整批解压密码
uv run private-pack decrypt-config ./output/manifest.enc \
  --key-file ./secrets/master.key --output ./secrets/manifest.json
```

聚合安装入口与独立包均提供 `private-pack` 命令；不需要安装外部7-Zip才能打包。
可用普通7-Zip工具配合配置中的密码手工解压。

## 行为约定

- 子目录递归打包，保留顶层目录；隐藏文件和空目录包括在内。独立文件保留原名。
- 每批生成一个20位密码，仅含数字及大小写字母，三类都有，保存在配置顶层。
- 每包加入一个16字节安全随机填充文件，路径记录在配置中。
- 压缩采用LZMA2，内容使用7z内置AES加密，内部文件名也加密。
- 原始包名为目录名或完整文件名加 `.7z`，最终改为32位随机十六进制名加 `.7z`。
- 配置记录原名、随机包名、统一密码、填充路径、包大小与SHA-256；不保存机器绝对路径。
- 配置使用AES-256-GCM与独立32字节二进制密钥文件，每次生成随机nonce，认证失败不输出明文。
- 所有配置计划先在内存生成，成功后仅输出压缩包和 `manifest.enc`。打包过程不写明文配置。
- 输出目录以Linux `renameat2(RENAME_NOREPLACE)` 原子提交，不覆盖并发创建的目标。
- `pack --output` 可省略，默认是输入目录同级的“输入目录名-packed”；已有目录仍拒绝覆盖。
  输入为 `.` 时使用解析后的当前目录名，末尾斜杠不影响命名；文件系统根目录需显式指定输出。
- 既有文件、符号链接、特殊文件、空输入、输入中的密钥文件或密钥硬链接会被拒绝。
- 密钥和明文配置权限为0600，批次及暂存目录为0700；父目录需已存在。
- 可观察到的源内容变化会使整批失败；普通失败清理本次暂存，强制终止可能遗留 `.private-pack-*`。
- 输入源保持不变。内容按字节还原；文件权限、时间戳、扩展属性不作为备份保真承诺。

密钥与加密配置请分开备份。任一个丢失都可能无法恢复解压密码；同批密码泄露会影响同批所有包。
16字节填充仅用于重复打包的随机变化，不隐藏原始体积，也不提供数学上的MD5无碰撞保证。
外部仍能看出7z格式。配置解密限制为16 MiB JSON；大于该上限的批次会失败而不发布输出。
没有断点续跑、跨批次哈希库、自动恢复包名或自动解压功能。

## 配置封装 v1

二进制顺序为 `PRIVATEPACK`（ASCII，11字节）加版本字节 `0x01`、12字节nonce、
AES-GCM密文及16字节标签。前12字节作为AAD，明文为UTF-8 JSON。
主密钥为原始32字节，不是文本口令、十六进制字符串或Base64文本。
配置里的 `version: 1` 与外层版本分别校验；计划阶段的空大小/哈希不得进入最终配置。

## 验证

```bash
uv run pytest tests/test_private_pack.py
# 指定独立7-Zip后可运行交叉兼容测试；否则该项明确跳过
PRIVATE_PACK_7Z=/path/to/7zz uv run pytest tests/test_private_pack.py
```

[设计与迭代记录](../../docs/private-pack-design.md) ·
[实施清单](../../docs/private-pack-implementation.md)
