# 已结束方案索引

| 方案 | 结果与范围 | 完成日期 | 归档日期 | 实现 commit 与验收位置 |
|---|---|---|---|---|
| [repo-security 首版](repo-security-plan.md) | 已完成：Linux x64，CLI Python 3.11/3.14 单包与聚合验收；macOS 明确延期 | 2026-11-17 | 2026-11-17 | M1 `c6e8acf58c0f40265c707acdfd1e361bf5aab133`；M2/M3 `fd0e2015c71bd0d93f502a5bc056d0655dc73c44`；详见方案第 5、8、9.6 节及 [M3 验收](../repo-security-m3-validation.md) |
| [skill-sync 个人仓库纳管与软链接安装](skill-sync-personal-repository-plan.md) | 已完成：单仓库配置、add 回链、单个/批量安装、同名跳过与坏描述符隔离；Linux 本机已验收 | 2026-11-21 | 2026-11-21 | 最终实现 `977ca04588c7a21bfc10036b00b7f39c379370a8`；M1–M3 与首轮修复详见归档方案第 5、8、9.10 节 |

repo-security 归档摘要：新增仓库安全扫描接入、预览、幂等/恢复、诊断、可选 CI 与可复用 Linux wheel 验收；
实现和证据已对账。归档不扩大已测试平台，也不授权公开发布或自动升级。
