# system-alarm

通过 macOS「快捷指令」创建原生「时钟」App 闹钟，提供 Python 接口和 CLI。
当前支持 macOS。Windows 已预留独立入口，调用时会明确提示尚未实现。

## 首次配置（必须）

需要安装「时钟」App 的 macOS（macOS 13+），并且本机「快捷指令」中能够搜索到
「时钟」的 **添加闹钟 / Add Alarm**（旧版为 **创建闹钟 / Create Alarm**）动作。如果该动作在本机系统版本不可用，本工具
不会退化成通知提醒，也不会修改时钟的私有数据库。

在「快捷指令」App 新建名为 **My Py Utils - Create Alarm** 的快捷指令，依次配置：

1. **从输入中获取文本 / Get Text from Input**：输入选「快捷指令输入」。
2. **从输入中获取词典 / Get Dictionary from Input**：输入选上一步文本（JSON）。
3. **获取词典值 / Get Dictionary Value**：从该词典获取 `time`，保存为变量 `AlarmTime`。
4. **获取词典值 / Get Dictionary Value**：仍从第 2 步词典获取 `label`，保存为变量 `AlarmLabel`。
5. **当前日期 → 格式化日期**：使用自定义格式 `yyyy-MM-dd`，再用「文本」动作拼成
   `[格式化日期]T[AlarmTime]:00`（方括号部分插入魔法变量，不是字面文本）。
6. **从输入中获取日期 / Get Dates from Input**：解析上一步文本，确保只有一个日期。
   再将日期用自定义格式 `HH:mm` 格式化，与 `AlarmTime` 比较；若不一致则停止快捷指令，
   不执行创建。这可以防止区域设置或夏令时跳时引起的错误时间；不要以零点加小时代替。
7. **添加闹钟 / Add Alarm**（或 **创建闹钟 / Create Alarm**）：时间绑定第 6 步解析出的日期
   （不是格式化后的文本），标签绑定 `AlarmLabel`，重复留空/设为「永不」。
   确保创建的是已启用闹钟；不要使用「每次询问」或额外的交互动作。

若第 6 步主动停止，系统可能仍返回成功；调用方必须根据下文限制核验「时钟」中的结果。

各系统版本的动作名称可能略有不同。先在快捷指令内临时用以下「文本」动作作为第 1 步输入，
手动执行一次，完成可能出现的权限授权，并在「时钟」确认时间、标签和启用状态；
验证结束后删除测试闹钟，并将输入改回「快捷指令输入」。

```json
{"time": "07:30", "label": "起床"}
```

也可以使用其他快捷指令名称或 UUID，通过 `--shortcut` / `shortcut=` 指定。
仅运行你信任的快捷指令，它可以执行超出创建闹钟的其他动作。

## CLI

在仓库根目录运行：

```bash
uv run --package system-alarm system-alarm create --time 07:30 --label "起床"
uv run --package system-alarm system-alarm create --time 18:00 --label "下班" --shortcut "My Py Utils - Create Alarm" --timeout 120
```

聚合安装后可以直接运行：

```bash
system-alarm create --time 07:30 --label "起床"
```

`--time` 必填且严格使用 24 小时制 `HH:MM`，标签默认为 `Alarm`。
`--shortcut` 和 `--timeout` 用于 macOS 快捷指令，超时默认 60 秒。
命令成功返回 0，执行/参数值错误返回 1，CLI 语法错误返回 2。

## Python 接口

```python
from system_alarm import AlarmError, create_alarm

try:
    result = create_alarm("07:30", label="起床")
except AlarmError as exc:
    print(f"创建请求失败：{exc}")
else:
    print(result.time, result.label, result.shortcut)
```

`create_alarm(time, *, label="Alarm", shortcut=DEFAULT_SHORTCUT, timeout=60.0)`
返回不可变 `AlarmResult(time, label, shortcut)`，macOS 返回实际调用的快捷指令名称或 ID。
验证失败、平台不支持、系统工具缺失、
权限/快捷指令错误、超时和 I/O 错误统一抛出 `AlarmError`。

## 行为和限制

- 时间使用 **Mac 当前本地时区**，不是 UTC，也不读取调用方浏览器的时区。
  原生闹钟是时刻而不是日期预约；已经过去的时刻会等待下次到达，不支持指定年月日。
- 首版只支持不重复闹钟；不提供重复规则、铃声、贪睡设置、查询或删除接口。
  实际属性取决于你配置的快捷指令；后续在「时钟」中管理。
- 每次调用都会执行创建动作；不做去重，也不自动重试。超时/失败时可能已经创建，
  请先检查「时钟」再重试，避免重复闹钟。
- 返回成功只表示 `shortcuts run` 退出码为 0，**不代表工具独立读取并验证了闹钟**。
  配置错误或空快捷指令可能正常退出而没有创建闹钟，首次必须手动核验。
- 创建过程使用私有临时目录内的 UTF-8 JSON 文件传递参数，不拼接 shell/AppleScript，
  完成或失败后清理输入文件。超时限制的是 CLI 等待时间，不保证系统侧动作已撤销。
- 不提供唤醒/开机能力；关机、睡眠、合盖、音量等可能影响响铃。重要提醒请先在目标设备验收，
  不要将本工具视为可靠唤醒或安全关键提醒服务。

Apple 命令行接口说明：[Run shortcuts from the command line](https://support.apple.com/guide/shortcuts-mac/apd455c82f02/mac)。

## 平台实现

`core.py` 校验时间和标签，并按当前系统加载对应模块。`macos.py` 处理快捷指令参数、输入文件和执行错误；
`windows.py` 接收时间和标签，预留给后续的 Windows 原生实现。两者使用相同的 `AlarmResult` 和 `AlarmError`。
