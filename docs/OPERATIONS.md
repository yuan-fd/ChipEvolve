# 本地平台运维手册

## 范围与不变量

本手册只覆盖本机演示与验收，不授权 push 或部署。Web/API 只查询状态和写入取消请求；EDA 进程只能由 Workflow Runtime 启动。live SQLite 必须位于节点本地 `/tmp`，Attempt workspace 和大产物位于项目的 ignored 目录。2D 与 TaiWei 3D 工具链不得混用或互相覆盖。

## 从零重放三条演示链

```bash
cd /share/home/yuanwenjie/openroad-platform
python scripts/run_platform_demo.py \
  --output-root .tools/platform-demo/$(date +%Y%m%d-%H%M%S)
```

该入口依次执行：

1. 固定 RTLScout 离线 Agent → Verilator/Yosys gate → 固定 2D ORFS → GDS；
2. 固定 AgenticPD proposal → 两成员有界 Campaign → 两次真实 ORFS → QoR；
3. 固定 TaiWei/ORFS-Research/OpenROAD → gcd 3D → GDS/via/指标；
4. timeout、失败、官方 detached child cancel；
5. loopback HTTP API/Web、artifact SHA、Campaign 和数据库恢复验收。

每次必须给空 output 目录；脚本拒绝覆盖旧证据。默认 live DB 自动建在唯一 `/tmp/openroad-platform-demo-*` 目录。

## 启动查询界面

使用某次验收产生的 live DB 和 Campaign DB：

```bash
python apps/api/app.py --host 127.0.0.1 --port 8000 \
  --runtime-db /tmp/openroad-platform-p8-real-EXAMPLE/runtime.db \
  --campaign-db /tmp/openroad-platform-p8-real-EXAMPLE/campaign.db
```

检查：

```bash
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/runtime/runs
curl -fsS http://127.0.0.1:8000/api/campaigns
```

P12/P13 APIs are data/control endpoints; API does not directly run EDA:

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/spec/sessions \
  -H 'Content-Type: application/json' \
  -d '{"provider":"codex-cli","model":"gpt-5.6-terra","message":"..."}'

curl -fsS -X POST http://127.0.0.1:8000/api/campaigns/stage-aware \
  -H 'Content-Type: application/json' \
  -d '{"design_id":"...","parameter_grid":{"core_utilization_pct":[20,30]},"max_parallel":2}'
```

Spec session 的 `/turn` 只追加提案；`/execute` 必须提交
`{"confirmed":true}`。Codex Provider 使用本机登录态做验收，但不得读取、记录或复制
认证文件。生产服务应配置独立 API Provider 和密钥轮换策略。

仅绑定 `127.0.0.1`；远程浏览使用 SSH tunnel。此内置服务器没有登录和 TLS，不能直接暴露到公网。

## 停止与取消

- 页面或 `POST /api/runtime/runs/:id/cancel` 只写 durable cancel request；Runtime 负责回收进程树。
- 前台 API 用 `Ctrl-C` 正常停止。
- 不要用宽泛 `pkill openroad`，同机存在其他用户的 EDA 进程。
- 若 worker 异常退出，先保存 DB，然后用 `expire_leases`/Runtime 恢复流程把过期 Attempt 标为 `lost`；新执行必须创建新 Attempt，不得覆盖旧 workspace。

## 备份与恢复

live WAL 数据库不能直接在运行中用普通 `cp` 作为可信备份。使用 SQLite backup API；P8-Real 验收器已经生成一致性 snapshot。恢复演练：

P22 提供了通用的非覆盖式备份/恢复命令。每个 `--database` 都通过
SQLite online backup API 复制并运行 `PRAGMA integrity_check`；恢复只允许
写入新的空目录，并先校验大小与 SHA-256：

```bash
python3 scripts/platform_state.py backup \
  --database /tmp/openroad-platform-$(id -u)/runtime.db \
  --database /tmp/openroad-platform-$(id -u)/campaign.db \
  --output .tools/backups/platform-$(date +%Y%m%d)

python3 scripts/platform_state.py restore \
  --manifest .tools/backups/platform-$(date +%Y%m%d)/backup.manifest.json \
  --target-root /tmp/openroad-platform-restore-check
```

```bash
python scripts/verify_runtime_backup.py \
  --snapshot .tools/p8-real-acceptance/runtime-20260805/runtime.db.snapshot \
  --run-id dacffccb314e439aba6f1c9cd6c1d1fc \
  --output .tools/p8-real-acceptance/runtime-20260805/restore-check.json
```

通过标准为 `integrity_check=ok`、无 foreign-key violation、成功 run 可查询、所有 artifact SHA 仍匹配。数据库只保存索引；恢复时对应 Attempt workspace/Artifact Store 也必须保留。

## 工具链核验与升级

当前 3D lock 位于 `integrations/taiwei_pin_3d/environment.lock.json`，构建入口为 `scripts/build_taiwei_official_toolchain.sh`。升级步骤：

1. 停止新的提交并备份 live DB；
2. 在新的 `.tools` profile 中构建，不原地覆盖现有版本；
3. 校验 source commit、binary SHA-256、许可证、编译器、RPATH 和动态库；
4. 重跑 3D smoke、P8-Real、resilience、三链 demo 和全量测试；
5. 追加新 evidence/lock，不改写旧失败或旧 release 证据；
6. 人工切换 API/worker profile。未经明确授权不 push、不部署。

## 安全检查

```bash
python scripts/check_tracked_secrets.py
python -m pytest -q
git diff --check
git status --short
```

凭据只能在项目外私有配置或进程环境中注入，不得写入 TaskSpec、SQLite snapshot、日志、lock、evidence 或 Git。
