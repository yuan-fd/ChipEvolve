# 风险和阻塞

## 已确认风险

1. Rocky 8 的 SQLite 是 3.26.0；仓库迁移回归测试直接使用 `ALTER TABLE ... DROP COLUMN`，在此服务器失败（该语法需要较新的 SQLite）。这是测试/环境兼容问题，不是运行时迁移结果的证据。
2. swap 已用尽，且服务器上有其他用户长期 EDA 作业；单机资源 admission 只能做平台级采样限制，不能替代 cgroup 隔离。
3. Innovus/Genus 依赖 module、OA 和 license；没有 module/OA 的裸启动不能算可用。Genus 的旧 build expiry 和 unsupported-OS warning 需要管理员确认。
4. ICC2 动态库不兼容；不能把“路径存在”写成“工具可用”。PrimeTime 尚未通过最小启动检查。
5. 本仓库没有 ORFS/商业工具源码；真实工具链必须在 Toolkit 部署环境中提供。

## 不在本阶段实现

- Kubernetes、多节点调度、远程对象存储、license manager、通用兼容层；
- kernel 内的 placement/CTS/route 策略或 QoR 决策；
- 修改共享 EDA 安装、替换服务器动态库或静默降级 Synopsys 工具。
