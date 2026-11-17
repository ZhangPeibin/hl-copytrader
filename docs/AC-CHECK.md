# AC 自查（AC-01 … AC-20）

状态口径：

- **已验证**：自动化测试覆盖，或本地/真实主网只读环境实测。
- **已实现，待实操**：代码与单测完成，但需要 testnet 资金与真实 leader 才能端到端观察。
- **环境限制**：本机条件导致无法复现，已注明替代验证方式。

| AC | 要求 | 状态 | 证据 |
| --- | --- | --- | --- |
| AC-01 | 单服务 compose、多阶段镜像、启动建表、`/api/health` 就绪 | 已验证 | `docker compose up -d` 后容器 8 秒内 healthy（127.0.0.1:8090）；health/index/静态资源均 200；`down` 再 `up` 后已导入钱包仍在（named volume 持久化） |
| AC-02 | 看板直连 HL `/info`，3s 缓存，DTO 不含私钥 | 已验证 | 主网实测钱包余额/仓位；`/api/wallets/{id}/account` 数值为字符串且无密钥字段 |
| AC-03 | `start(sync)` 立即对账并含 `updateLeverage` | 已实现，待实操 | `test_first_entry_sets_leverage_then_places_order`：先 leverage 后 order |
| AC-04 | `start(new_only)` 把 leader 非零币种写锁定，对比页显示 | 已验证 | `test_new_only_locks_positions_at_start`、`test_compare_respects_locked_state` |
| AC-05–08 | WS `userFills` 触发增量重算，10s 内加仓/减仓/反手 | 已实现，待实操 | WS 主网订阅已实测连通；`test_apply_fill_*`、`test_add_reduce_and_close_converge` 覆盖路由与收敛 |
| AC-09 | 目标 0 时按实际持仓全平，无残仓 | 已验证 | `test_close_all_uses_actual_position_not_delta`、`test_close_task_cancels_orders_and_closes_positions` |
| AC-10 | 只看 fills，挂单变化不触发动作 | 已验证 | 触发源只有 fill/tick/command；`openOrders` 仅用于展示与停止前撤单 |
| AC-11 | 双来源解锁（对账归零 + fills 归零） | 已验证 | `test_reconcile_unlocks_when_leader_goes_flat`、`test_fill_that_closes_position_unlocks_before_reconcile` |
| AC-12 | 30s 定时对账，`source=reconcile` | 已验证 | 1s 周期实测 `last_tick_ms` 持续前进；`test_tick_uses_reconcile_source` |
| AC-13 | 改参数立即重算 | 已验证 | `test_param_update_submits_command`、`test_add_reduce_and_close_converge` |
| AC-14 | 重启恢复 + cloid 幂等 | 已验证 | `test_restart_replays_running_tasks_with_the_same_cloid`、`test_duplicate_cloid_is_treated_as_success` |
| AC-15 | paused 短路；stop+close 全平 | 已验证 | `test_paused_task_is_not_touched`、`test_lifecycle_stop_with_close_submits_close_event` |
| AC-16 | 按 leader 共享缓存，任务互不影响 | 已验证 | `HlInfoClient` 按地址缓存；`test_one_task_failure_does_not_block_another` |
| AC-17 | 超 `maxNotional` 截断并记原因 | 已验证 | `test_max_notional_truncates_target`、`test_truncated_target_can_fall_below_min_notional` |
| AC-18 | 指数退避、原始错误落库、per-task 隔离 | 已验证 | `test_failure_enters_backoff_without_repeat_logs`、`test_retry_after_backoff_window` |
| AC-19 | 所有触发统一写 `exec_logs` | 已验证 | 下单/跳过/失败均落库；`/api/logs` 过滤与分页测试 |
| AC-20 | Fernet 密文、DTO 白名单、日志遮罩、`key_state` 分支、SQLite 无明文 | 已验证 | `test_create_wallet_hides_secret_and_encrypts`（DB 字节不含明文）、`test_masking_filter_preserves_arg_tuple_shape`、`test_unreadable_key_marks_wallet_and_stops` |

## 未覆盖的验证缺口

1. **真实下单链路**：AC-03、AC-05–08 依赖已授权的 testnet API wallet 与资金。当前环境只有
   主网只读访问，因此只验证到“构造 payload + 解析响应 + 决策与日志”。
2. **HL 错误文本**：正则基于经验值；`scripts/probe.py` 采集后需回填。
3. ~~compose 端口~~：默认端口已改为 8090，`docker compose up -d --build` 已在本机实测。

## 降级顺序（与 PLAN §11.1 一致）

先砍：挂单镜像/统计类加分项 → 前端视觉细节 → 多 leader 覆盖 → 单测覆盖率。
绝不砍且已保留：cloid 幂等、退避、读失败不误平仓、私钥加密、一键启动、统一日志。
