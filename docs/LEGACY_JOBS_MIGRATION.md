# Legacy jobs 非破坏性迁移设计

status: P1 implemented projection / import deferred

## 边界

现有 `jobs`/`job_events` 数据库是只读历史证据，不能就地增加 v1 表、修改状态或补写推断字段。P1 的 `project_legacy_jobs()` 使用 SQLite `mode=ro` 与 `query_only` 生成内存投影；`RuntimeStore` 只允许初始化空数据库，遇到未版本化或 legacy 数据库会拒绝写入。

## 映射

| legacy 字段 | v1 投影 | 规则 |
| --- | --- | --- |
| `jobs.id` | `TaskSpec.task_id` | 加 `legacy:` 前缀，保留来源 ID |
| `request_json` | `TaskSpec.inputs/parameters` | 仅复制已记录值，不猜测缺失值 |
| `queued/running/...` | `RuntimeStatus` | `preparing` 映射为 `running`，同时保留原状态 |
| `result_json/error` | projection result/error | 原样解析，不伪造 Artifact/Attempt |
| 缺失工具链信息 | provenance | 明确写 `unknown` |

固定标为 `unknown` 的字段包括 source/toolchain/PDK revision、environment digest 和 adapter version。缺失 top/project identity 使用字面值 `unknown`，不从路径或历史环境猜测。

## 后续显式 importer

P2 或单独迁移任务若需要把投影写入 v1，应采用 source DB 只读、目标 DB 独立的两阶段流程：

1. 对 source DB 和每条投影生成哈希清单并人工确认目标库。
2. 为 Run、StageRun、Attempt 分配新的 v1 ID，保存 source job ID 映射。
3. 只有可验证存在且哈希匹配的文件才能登记为 Artifact。
4. 在单个目标事务内导入一条 job；冲突时回滚，不修改 source。
5. 导入后比较数量、状态、哈希和 unknown 字段，再切换任何读取方。

P1 不实现写入 importer，因为 P2 的 ORFS plugin/stage 语义尚未定版；提前把 legacy 单任务猜成多阶段 Attempt 会制造不可逆的伪事实。
