# P8 里程碑：TaiWei 插件，真实 3D 外部阻塞

captured_at: 2026-08-04
status: integration completed; real gate blocked

- `taiwei-pin-3d@1.0.0` 固定 `db201367...`，生产 profile 强制 ORFS-Research `568eb04...` / OpenROAD `305d3ba...`。
- Adapter 在 Attempt 内 `git archive` 上游并只允许 `ord/asap7_3D/gcd`；eval/summary/GDS/snapshot/log 都是成功必需项。
- 进程级 fixture 验证协议、workspace、哈希和上游 clean，不是 3D QoR。
- GitHub 三次探测超时；自有 2D 和可见共享 module 均版本不匹配；无 provenance 的本地片段不准入。
- 真实 3D 未执行，`docs/evidence/P8_TAIWEI_ACCEPTANCE.json` 保持 `accepted=false`；全量 78 passed。
