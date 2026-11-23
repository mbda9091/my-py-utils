# skill-sync 强制安装、卸载、删除与更新方案已归档

2026-11-24 已完成四个里程碑：固定用户 agents 目录、强制安装持久备份、uninstall、remove，以及整个绑定仓库的仅快进 update。

完整设计、提交及验收对账见[归档方案](archive/skill-sync-lifecycle-plan.md)。原章节锚点请在归档版本查找。

Linux / Python 3.11.15：303 项 skill-sync 包测试、全仓静态检查、14 包构建及独立 wheel 的真实本地 Git/文件操作验收通过；其他平台及真实跨挂载点不计为已验收。最终实现提交为 `555eb9cd15a7348b0b931f0e78dd62af516ca8df`。

当前用法见 [skill-sync README](../packages/skill-sync/README.md)。Git 提交/推送和持久备份清理由用户管理。
