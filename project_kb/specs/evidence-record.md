# Evidence record 规范

每条可检索记录必须包含：

- verified=true；
- 非空 claim；
- `artifact:`、`run:` 或 `docs/evidence/` 持久引用及 64 位 SHA-256；
- design_id、platform、pdk_id、toolchain_id；
- scope=`exact_design` 或经审查的 `platform_general`；
- 可选 proposed_action，但必须通过 RepairAction 并引用同一证据。

检索顺序固定为版本硬过滤→词项排序。回放固定为 DB record→fingerprint→上下文→RepairAction 四重校验，并始终返回 `executed=false`。
