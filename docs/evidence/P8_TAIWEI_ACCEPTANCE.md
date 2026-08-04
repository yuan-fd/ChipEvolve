# P8 TaiWei-Pin-3D 黑箱接入验收

status: integration completed; real 3D externally blocked
captured_at: 2026-08-04

## 结论

`taiwei-pin-3d@1.0.0` 的固定源码、Task builder、独立工具链 profile 和 workspace 黑箱 Adapter 已完成，协议/失败路径通过。真实 `ord/asap7_3D/gcd` 未执行，P8 的真实 3D GDS/QoR 硬门仍是明确外部阻塞，因此本证据的 `accepted=false`，没有把 fixture 或错误工具版本包装成真实结果。

## 已实现边界

- 生产 manifest 同时校验 TaiWei `db201367...`、ORFS-Research `568eb04...`、bundled OpenROAD `305d3ba...` 和 clean 状态。
- 只允许开源 `ord/asap7_3D/gcd`，拒绝其它设计、技术或 commercial flow。
- 上游源码用 `git archive HEAD` 复制到 Attempt workspace 后执行，不在 `.external-src` 写产物。
- 成功必须同时存在非空 `openroad_eval.json`、`final_summary.txt`、GDS、toolchain snapshot 和 log；3D PNG view 可多份登记。
- 产物仍经过 ProcessAdapter 的 workspace containment、size 和 SHA-256 校验。

## 外部阻塞证据

1. 对 GitHub ORFS-Research/OpenROAD 的三次只读探测均无响应或 20 秒连接超时，无法下载用户已批准的固定源码。
2. 本用户现有 2D profile 是 ORFS `51ad123...` / OpenROAD `63ed2e0...`，不能用于 3D。
3. 可见共享 module 是 ORFS `5101376937a5...` / OpenROAD `138e57370c98...`，同样不匹配；平台没有复制或调用其他用户的私有构建。
4. `platform-plugins/TaiWei-components/ORFS-Research` 是无 `.git`、无固定 binaries 的源码片段，不能证明 commit，不能准入。
5. 用当前自有 profile 构造生产 manifest 的实测结果：`ValueError: TaiWei ORFS-Research commit mismatch`。

## 验证

进程级 fixture 使用三个独立 clean Git 仓库和假工具二进制，只证明版本门、源码快照、固定 CLI、产物发现/哈希和 Runtime 状态链；它明确不是 3D QoR。

```text
python -m pytest -q
78 passed
```

解除阻塞后无需改 Adapter：将固定 ORFS-Research/OpenROAD 构建放入项目私有 `.tools`，构造 profile 后运行一次 6 小时预算的 gcd 验收，并把 `accepted` 改为 true 的新证据作为追加里程碑；不得改写本阻塞记录。
