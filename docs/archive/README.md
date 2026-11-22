# 已结束方案索引

| 方案 | 结果与范围 | 完成日期 | 归档日期 | 实现 commit 与验收位置 |
|---|---|---|---|---|
| [repo-security 首版](repo-security-plan.md) | 已完成：Linux x64，CLI Python 3.11/3.14 单包与聚合验收；macOS 明确延期 | 2026-11-17 | 2026-11-17 | M1 `c6e8acf58c0f40265c707acdfd1e361bf5aab133`；M2/M3 `fd0e2015c71bd0d93f502a5bc056d0655dc73c44`；详见方案第 5、8、9.6 节及 [M3 验收](../repo-security-m3-validation.md) |
| [skill-sync 个人仓库纳管与软链接安装](skill-sync-personal-repository-plan.md) | 已完成：单仓库配置、add 回链、单个/批量安装、同名跳过与坏描述符隔离；Linux 本机已验收 | 2026-11-21 | 2026-11-21 | 最终实现 `977ca04588c7a21bfc10036b00b7f39c379370a8`；M1–M3 与首轮修复详见归档方案第 5、8、9.10 节 |
| [session-scan 首版](session-scan-plan.md) | 已完成：macOS arm64，DSH / Codex 实际文件发现、目录 / 消息时间筛选、按需读取及固定 JSON；首轮 94 个包测试和 515 个真实文件核对通过，评审修复后 105 个包测试通过 | 2026-11-21 | 2026-11-21 | M1 `1ce9eb1`；M2 `15553d6`；M3 `089647e`；评审修复 `af7d1c5`；详见归档方案第 9.6、9.7 节及 rebase 提交映射 |

repo-security 归档摘要：新增仓库安全扫描接入、预览、幂等/恢复、诊断、可选 CI 与可复用 Linux wheel 验收；
实现和证据已对账。归档不扩大已测试平台，也不授权公开发布或自动升级。

session-scan 首版变更摘要：本机只读扫描、压缩记录适配、内部消息时间筛选及聚合包安装；
本轮不输出对话正文，不恢复缺失记录，不扩大其他平台验收或执行外部发布。
