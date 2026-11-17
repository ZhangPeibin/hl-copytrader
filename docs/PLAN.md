# 个人版 Hyperliquid 跟单网站 — 实现方案

状态：设计已定稿（第 13 节记录了全部取舍），待实现。目标是在 48 小时窗口内（实际投入 12–16 小时）交付一份可一键启动、
在 HL 主网正确跟单与调仓的实现，优先保证 14 条否决项。

---

## 1. 范围与取舍

必做 5 项（F1–F5）+ 第 3 节全部跟单规则 + 第 4 节工程要求。加分项只做**代价低、直接提升验收稳定性**的两条：

| 加分项 | 取舍 | 理由 |
| --- | --- | --- |
| WS 订阅 leader 成交 + 断线补漏 + 轮询兜底 | 做 | AC-05/06/07/08 都是 10 秒时限，纯轮询不稳 |
| cloid 幂等键 | 做 | 一句话成本，直接支撑 ST-4 的"不重复下单"和重试安全 |
| 按资金比例跟单 | 不做 | 与 F2 的固定比例语义冲突，容易在验收时解释不清 |
| 挂单镜像 / 多 leader 合并 / 统计 / 风控 / 告警 / 主网 E2E | 不做，README 写做法 | 时间不够，且不影响评分主体 |

明确不做：注册登录、多用户、充值提现、现货与 HIP-3 跟单、生产级部署。

---

## 2. 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 后端 | Python 3.12 + FastAPI | 官方 `hyperliquid-python-sdk` 是 Python，签名/序列化细节最少踩坑 |
| 跟单引擎 | **单线程同步串行循环**（独立线程） | 天然满足"同币种同一时刻只有一笔在途调仓"，不需要锁 |
| WS | `websockets` 独立线程，`asyncio` 事件循环 | 只负责收 `userFills`，收到后塞进引擎队列 |
| DB | SQLite（WAL）+ `sqlite3` + 薄仓储层 | 单文件 + Docker named volume 即可满足持久化；不开第二个容器 |
| 前端 | React 18 + Vite + TypeScript | 看板/对比页交互多，10 秒刷新好做；构建产物由 FastAPI 静态挂载 |
| 打包 | 多阶段 Dockerfile（node → python），`docker compose` 单服务 | AC-01 |

关键理由：官方 SDK 是同步 `requests` 实现，硬塞进 asyncio 需要全链路 `run_in_executor`。
把"读状态 / 算增量 / 下单 / 写日志"全部放进一条串行线程，正确性收益远大于并发收益，
且论文级的互斥问题直接消失。

---

## 3. 架构

```
                    ┌──────────────────────────── Docker: app ────────────────────────────┐
  Browser ──HTTP──▶ │  FastAPI (uvicorn, N workers=1)                                     │
                    │    ├─ /api/wallets /api/tasks /api/dashboard /api/compare /api/logs │
                    │    ├─ 只读 SQLite（WAL，独立连接）                                   │
                    │    └─ 实时读 HL /info（带 2–3s 缓存）+ 静态资源（React build）       │
                    │                                                                     │
                    │  EngineLoop（单线程，串行）                                          │
                    │    ├─ in-queue:  FillEvent | TickEvent | CommandEvent                │
                    │    ├─ LeaderState（按 leader 地址共享，TTL 2s）                       │
                    │    ├─ Planner（纯函数：目标/增量/决策）                              │
                    │    ├─ Executor（IOC 下单、cloid、退避）                              │
                    │    └─ 写路径：SQLite 单写者                                          │
                    │                                                                     │
                    │  WsListener（线程）：wss userFills ──▶ in-queue                       │
                    └─────────────────────────────────────────────────────────────────────┘
                                     │                        ▲
                                     ▼                        │
                              HL REST /info /exchange    HL WS /ws
                                     │
                             Docker volume: /data/app.db
```

数据流：

1. **读**：前端 → FastAPI →（余额/仓位/挂单/成交）直连 HL `/info`，3 秒内缓存；跟单对比与日志读 SQLite。
2. **触发**：WS 收到 leader 成交 → `FillEvent`；定时器 → `TickEvent`；前端操作 → `CommandEvent`。
3. **决策**：引擎对受影响的 task 重新拉取 leader 与 follower 全量状态 → 计算目标 → 逐币种下单。
4. **写**：每笔下单/跳过/失败落 `exec_logs`；`coin_states` 记录锁定与杠杆状态。

---

## 4. 核心设计决策

### D1 事件只是"提醒"，不是"指令"

`FillEvent` 只用来缩小对账范围，绝不携带"应该买卖多少"。每轮都**从 leader 与 follower 的最新全量仓位
重算增量**。这样 leader 连续多笔成交、IOC 部分成交、手动干预，都在同一条路径上收敛，
也自然杜绝"读到旧仓位重复下单"。

### D2 串行 = 互斥

`EngineLoop` 是唯一的下单者，`(task, coin)` 的在途状态就是"当前正在处理"本身。
若处理期间队列里又堆了同一币种的成交事件，它们会被合并为该币种的一次重算（去重键 `task_id:coin`）。
单进程单写者，不需要分布式锁；`docker compose` 也只起 1 个 app 副本。

### D3 目标仓位与取整

```
T_c = trunc_toward_zero(L_c * r, szDecimals_c)      # 向零截断，避免放大
Δ_c = round(T_c - F_c, szDecimals_c)                # 同样向零，避免残渣
若 |T_c| * markPx_c > maxNotional:  T_c = trunc(sign(T_c) * maxNotional / markPx, sz)  # 记 truncate 日志
```

用 `decimal.Decimal` 全程计算，禁止 `float` 参与下单数量/价格。`szDecimals` 来自 `/info meta`，
启动加载 + 60 秒刷新。

### D4 下单：IOC 限价模拟市价

- 买入价 = `mid * (1 + slippage)` 向下取到合法价位；卖出价 = `mid * (1 - slippage)` 向上取。
  取整方向选"不越过滑点上限"的一侧，宁可这一轮不成交，交给下一轮对账，也不突破用户给的滑点。
- 价格合法性：最多 5 位有效数字，小数位 ≤ `6 - szDecimals`，整数价永远合法；先按 5 位有效数字取整，再夹小数位。
- `mid` 来自 `allMids`（缓存 1 秒，权重 2）。
- 减仓/平仓一律 `reduceOnly=true`。

### D5 最小金额与平仓例外

- `|Δ| * markPx < 10` → 跳过，记 `skipped` + 原因 `below_min_notional`。
- 例外：`T_c == 0` 且 `F_c != 0` → 按**实际持仓绝对值**下 `reduceOnly` 单全平，不按 `Δ` 算，
  以避免截断残仓（AC-09）。
- 已知风险：HL 最小 $10 名义可能同时约束 reduceOnly 小单；若被拒，日志记录原始错误并按退避重试，
  README 写明。验收环境每笔 ≥ $500、比例 0.1/0.2，残值大概率高于 $10。

### D6 反手：先平后开，两笔

`reduceOnly` 单不能穿越零点，因此 `sign(T_c) != sign(F_c)` 时拆两步：
① `reduceOnly` 全平实际持仓；② 按新目标开 `|T_c|`。两笔都用确定性 cloid，
第 ② 笔失败时下一轮对账会补，不会产生反向穿越风险。README 明确写这个选择。

### D7 杠杆与保证金模式

首次在某币种开仓前调 `updateLeverage(asset, isCross, min(leaderLev, maxLeverage))`，
`isCross` 取 leader 的 `leverage.type`。`coin_states.leverage_set` 持久化，避免每轮重复调用，
参数变更（maxLeverage）时置 0 重新设置。

### D8 锁定与解锁（只跟新单）

- 启动 / 恢复任务时：leader 当前仓位 != 0 的币种写 `locked=1`，不下单不调仓。
- 解锁条件（两个来源，任一命中即解锁并持久化）：
  1. 对账时 leader 该币种仓位 == 0；
  2. WS `userFills` 中某笔成交换算后 leader 仓位归零（`startPosition + sz == 0`）。
  第 2 条覆盖"leader 在两次对账之间平掉又重开"的竞态，避免 AC-11 因 30 秒窗口而永久锁死。
- 定时器顺带校验：`locked=1` 且 leader 仓位已归零 → 解锁（覆盖停机期间归零，AC-14）。

### D9 幂等

- `cloid = blake2b(f"{task_id}|{coin}|{target}|{reverse_leg}")[:16]` 转 128-bit hex。
- 重试复用同一 cloid；HL 返回 duplicate cloid 视为"上次其实成功"，按成功处理并重新对账。
- nonce：每个 API wallet 维护单调递增计数器 `max(now_ms, last+1)`，与 cloid 一同封装在
  `exchange` 适配层，引擎不感知。

### D10 失败与退避

- 读 leader 失败/超时 → 本轮跳过，**绝不当作空仓**，日志 `skipped: leader_state_unavailable`。
- 下单被拒 → 记 HL 原始 `error` 文本，按 `(task, coin)` 指数退避 5s→10s→…→60s，
  `next_retry_at` 落库；退避窗口内不重复刷单，也不重复写日志（只写首次与恢复）。
- 单个 task 的失败不阻塞其他 task：循环内 per-task try/except，异常只影响当前 task 本轮。

### D11 限频

- 全局令牌桶按权重 1200/min（保守留 20% 余量到 900），`/info` 权重按请求类型计。
- leader 状态**按 leader 地址共享**：多个 follower 跟同一 leader 只拉一次，TTL 2 秒。
- `meta` 60 秒、`allMids` 1 秒、`clearinghouseState` 实时拉（每次对账一次，不按币种重复）。
- WS：每个 leader 一条连接（用户订阅最多 10 个地址的上限内），断线 1s/2s/5s 退避重连，
  重连后用 `userFillsByTime` 补齐断连期间的成交（按 `time` 去重）。

### D12 决策表（`planner` 的判定顺序）

按优先级从上到下，命中即返回，保证每个币种每轮**只有一个动作、一个 reason**。
`planner` 输出 `list[Action]`，`executor` 只执行、不判断。

| # | 条件 | 动作 | 日志 reason |
| --- | --- | --- | --- |
| 0 | `\|T\| * mark > maxNotional` | 截断 `T`（在第 5 步之前生效） | `truncated_by_max_notional` |
| 1 | `task.status != 'running'` | 无 | `task_paused` / `task_stopped` |
| 2 | leader 状态不可用（拉取失败或重试仍失败） | 无 | `leader_state_unavailable` |
| 3 | `next_retry_at` 未到 | 无 | `backoff` |
| 4 | `locked` 且 `L != 0` | 无 | `locked` |
| 5 | `locked` 且 `L == 0` | 置 `locked=0`（持久化）后重走本轮 | `unlocked` |
| 6 | `T == 0` 且 `F == 0` | 无 | `no_position` |
| 7 | `T == 0` 且 `F != 0` | `reduceOnly` 全平 `\|F\|` | `close_all` |
| 8 | `\|Δ\| * mark < 10` | 无 | `below_min_notional` |
| 9 | `sign(T) != sign(F)` | 两笔：先 `reduceOnly` 平，再按 `\|T\|` 开 | `reverse` |
| 10 | 首次在该币种开仓 | 先 `set_leverage`，再下单 | `set_leverage` |
| 11 | 其余 | IOC 单 `Δ` | `place` |

补充约定：

- 第 0 步的 `mark` 用 `allMids`（与第 8 步一致），只用同一个价源，避免两处判断不一致。
- 第 7 步的平仓数量用 `|F|` 而不是 `|Δ|`，保证无残仓（AC-09）。
- 第 10 步的 `leverage_set` 以 `(task_id, coin)` 持久化；改 `maxLeverage` 时置 0 重设。
- 同一轮内 `unlocked` 不产生订单，只更新状态；下一轮才建仓，避免"解锁瞬间同时开仓"的时序歧义。

### D13 签名、nonce 与 HL 错误分类

签名结构（与官方 SDK `sign_l1_action` 一致）：

```
EIP-712 domain: { name: "Exchange", version: "1", chainId: 1337,
                  verifyingContract: 0x0000000000000000000000000000000000000000 }
包裹体:  { source: "a", connectionId: keccak(api_wallet_address)[0:16],
           nonce, vaultAddress, expiresAfter }
内层:    Order / Cancel / UpdateLeverage / ...
```

- `source: "a"` + `connectionId` 共同声明"这是一把 agent 签的单，属于哪个代理"；节点据此找到主账户。
- nonce 按**签名地址**（`api_wallet_address`）维护单调计数器 `max(now_ms, last + 1)`，
  同一轮多笔连发也不会撞；重启后从当前毫秒继续，天然大于历史值。
- `expiresAfter` 留空（不设过期），依赖 IOC 本身的即时性。

错误分类（`hl/errors.py`，对 HL 原始文本做正则归类，原始文本一律进 `exec_logs.raw`）：

| 分类 | 典型文本特征 | 处理 |
| --- | --- | --- |
| `agent_unauthorized` | `does not exist` / `not approved` / agent 地址相关 | **停止该 task**，UI 提示重新授权/导入，不重试 |
| `invalid_nonce` | `Invalid nonce` / `nonce` | 重置 nonce 计数器后立即重签一次，仍失败则退避 |
| `insufficient_margin` | `Insufficient margin` | 指数退避，前端展示主账户可提现金额 |
| `below_min_notional` | `minimum value` / `$10` | 按 `below_min_notional` 跳过，不当成故障 |
| `reduce_only_violation` | `Reduce only order would increase` | 说明状态已被外部改动，触发一轮重新对账 |
| `price_out_of_range` | `invalid price` / tick 相关 | 按取整规则重算价格后重试一次 |
| `unknown` | 其余 | 退避 + 原文落库 |

**待线上实测**（拿到测试网凭据后的第一件事，约 30 分钟）：`scripts/probe.py` 故意构造
未授权 agent、重复 nonce、$10 以下小单、错误价格、超仓 reduceOnly 五类请求，把真实错误字符串
抓回来补全上表正则；同时确认 **reduceOnly 小单是否也受 $10 限制**。

### D14 数值精度与存储

- 计算全程 `decimal.Decimal`，**不下单参数、不比较、不落库过程中出现 float**。
- DB 中所有数量/价格/比例/杠杆列一律 `TEXT`，存 Decimal 的规范字符串（如 `"0.123"`、`"0.1"`）；
  SQLite 不参与算术语义，只做搬运与排序（需要排序的时间字段用 `INTEGER` 毫秒）。
- 取整统一用 `Decimal.quantize(..., ROUND_DOWN)`（向零）而非 `round()`（银行家舍入，方向不可控）。
- 从 DB 读回的数量字符串必须与下单前完全一致 → 幂等键 `cloid` 才有意义，重启后重算也得到同一目标。
- API 响应里数值字段输出**字符串**，前端只做展示与比较（不做累加），避免 JS 浮点污染。

---

## 5. 目录结构

```
hl-copytrader/
├─ docker-compose.yml
├─ Dockerfile
├─ .env.example
├─ Makefile                     # make dev / test / up / logs
├─ README.md
├─ docs/PLAN.md
├─ backend/
│  ├─ pyproject.toml
│  ├─ hlcopy/
│  │  ├─ main.py                # FastAPI 工厂、静态挂载、启动引擎
│  │  ├─ config.py              # 环境变量 → Settings
│  │  ├─ crypto.py              # Fernet 加解密私钥
│  │  ├─ db.py                  # schema/migrate + 仓储
│  │  ├─ hl/
│  │  │  ├─ info.py             # /info 客户端 + 缓存 + 限频
│  │  │  ├─ exchange.py         # 签名、nonce、order/cancel/leverage
│  │  │  ├─ ws.py               # userFills 监听 + 补漏
│  │  │  └─ rounding.py         # 价格/数量取整（纯函数）
│  │  ├─ engine/
│  │  │  ├─ loop.py             # EngineLoop：队列、分发、housekeeping
│  │  │  ├─ planner.py          # 纯函数：目标/增量/决策
│  │  │  ├─ executor.py         # 下单执行、反手、退避
│  │  │  └─ leader.py           # leader 状态缓存与解锁判断
│  │  └─ api/                   # wallets / tasks / dashboard / logs / meta
│  └─ tests/
│     ├─ test_rounding.py
│     ├─ test_planner.py        # 目标、截断、最小金额、反手
│     └─ test_lock.py           # 锁定/解锁（含"平掉又重开"）
└─ frontend/                    # Vite + React + TS
   └─ src/pages/{Wallets,Tasks,Dashboard,Compare,Logs}.tsx
```

依赖方向：`api / engine → hl adapter → 网络`；`planner` 不 import 任何 SDK，保证可纯单测。

---

## 6. 数据模型（SQLite）

```sql
CREATE TABLE wallets (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
  address TEXT NOT NULL UNIQUE,                            -- 主账户地址：所有查询用它；唯一
  api_wallet_address TEXT NOT NULL,                        -- API wallet 地址：校验/展示/nonce 归属
  secret_enc BLOB NOT NULL,                                -- Fernet 密文，永不返回
  key_state TEXT NOT NULL DEFAULT 'ok'                     -- ok | unreadable（换密钥后解不开）
    CHECK (key_state IN ('ok','unreadable')),
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);

CREATE TABLE tasks (
  id INTEGER PRIMARY KEY, wallet_id INTEGER NOT NULL REFERENCES wallets(id),
  leader TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('sync','new_only')),
  ratio TEXT NOT NULL, max_notional_usd TEXT NOT NULL,   -- Decimal 字符串，见 D14
  max_leverage TEXT NOT NULL, max_slippage TEXT NOT NULL DEFAULT '0.01',
  status TEXT NOT NULL CHECK (status IN ('stopped','running','paused')),
  close_on_stop INTEGER NOT NULL DEFAULT 0, created_at INTEGER, updated_at INTEGER);

CREATE TABLE coin_states (            -- 跟单过程中的每币种状态
  task_id INTEGER, coin TEXT, locked INTEGER DEFAULT 0, leverage_set INTEGER DEFAULT 0,
  last_target TEXT, last_action_at INTEGER, fail_count INTEGER DEFAULT 0,
  next_retry_at INTEGER, last_error TEXT, PRIMARY KEY (task_id, coin));

CREATE TABLE exec_logs (
  id INTEGER PRIMARY KEY, task_id INTEGER, wallet_id INTEGER, ts INTEGER NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('fill','reconcile','param','start','resume','stop')),
  coin TEXT NOT NULL, leader_pos TEXT, pos_before TEXT, target TEXT,
  side TEXT, qty TEXT, price TEXT,
  result TEXT NOT NULL CHECK (result IN ('filled','partial','failed','skipped')),
  reason TEXT, latency_ms INTEGER, oid TEXT, cloid TEXT, raw TEXT);

CREATE INDEX idx_logs_task_ts ON exec_logs(task_id, ts DESC);
CREATE INDEX idx_logs_ts ON exec_logs(ts DESC);        -- 日志按时间筛选/分页（F5）
```

数值列一律 `TEXT`（`Decimal` 字符串），原因与约束见 D14；`INTEGER` 只用于主键、时间戳、布尔与计数。

`docker compose down` 后 `up`：`/data/app.db` 保留 → 钱包、任务、锁定状态、日志全在；
进程启动时把 `status='running'` 的 task 自动拉起并立即对账一次（AC-14）。

---

## 7. API 草案

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/wallets` | 列表（私钥永不出参）/新增 |
| PUT/DELETE | `/api/wallets/{id}` | 编辑（私钥留空=不改）/删除 |
| GET/POST | `/api/tasks` | 列表/新增 |
| PUT/DELETE | `/api/tasks/{id}` | 改参数（立即触发 `CommandEvent(param)`） |
| POST | `/api/tasks/{id}/start|pause|resume|stop` | `stop` 带 `close_positions` |
| GET | `/api/tasks/{id}/dashboard` | 余额/仓位/挂单/成交（直连 HL，3s 缓存） |
| GET | `/api/tasks/{id}/compare` | 按币种：leader 仓 / 目标 / 实际 / 偏差 / 锁定 |
| GET | `/api/logs` | 分页 + `task_id`/`coin`/`from`/`to`/`result` 过滤 |
| GET | `/api/meta` | 币种、szDecimals、最大杠杆（前端下拉用） |
| GET | `/api/health` | 引擎心跳、WS 状态、队列积压 |

---

## 8. 私钥导入、存储与 API wallet

### 8.1 术语

`API wallet`、`agent wallet`、`agent 钥匙` 是同一个东西的两个名字，不是两种密钥：

- **API wallet** 是 HL 产品/文档与官方前端的叫法（More → API 页面）。
- **agent** 是协议层的叫法，留在签名结构里：授权动作 `approveAgent`、EIP-712 类型字段 `Agent`、
  签名里的 `connectionId = hash(agent 地址)`。
- 所以"agent 钥匙" = API wallet 的私钥，"agent 地址" = API wallet 页面显示的 address。
- 与 AI agent 无关：这里的 agent 指"被主账户授权的代理签名人"。

代码里**统一用 `api_wallet` 命名**（表字段、变量、DTO），只有 `hl/exchange.py` 适配层出现
`approveAgent` 等协议术语，避免读者误以为项目内置了 LLM agent。

与两个易混概念的区别：

| 概念 | 是什么 | 本项目 |
| --- | --- | --- |
| API wallet / agent | 主账户的代理签名人，共用主账户资金，不能提现/划转 | **用这个** |
| 子账户（sub-account） | 独立账户，有自己的余额与仓位，用自己的私钥 | 不用 |
| Vault | 多人资金池 | 不用 |

### 8.2 权限边界与来源

- 能：下单、撤单、改单、设杠杆、改保证金模式、TWAP 等交易动作。
- 不能：提现、账户间划转、授权新的 API wallet。
- 生成路径（写进 README）：官方前端 → 连接主钱包 → More → API → Generate API wallet →
  主账户签名确认。页面显示 `private key` 与 `address`，**私钥只显示一次**。
- 首次授权 `approveAgent` 必须由主账户签名，用户在前端完成；导入流程只要 agent 私钥。
- 一个主账户可授权多把 API wallet（上限以页面提示为准）；agent 久不使用会失效，
  失效后重新生成并在 UI 中更新私钥即可。
- 主账户私钥**任何情况下都不需要、也不允许导入**：能交易也能提现，风险不可接受。

### 8.3 导入流程（`POST /api/wallets`）

```
1) 规范化：去 0x 前缀，校验 64 位 hex、32 字节
2) eth_account.Account.from_key(key) 反推地址
3) 与用户填写的 api_wallet_address 比对，不一致 → 400（避免粘错私钥到首次下单才暴露）
4) 主账户 address 做 checksum 校验
5) Fernet 加密 → 只把密文写入 secret_enc，明文变量随函数返回丢弃
6) 响应只有 id/name/address/api_wallet_address/key_state
```

前端表单：`type=password`、`autocomplete=off`、绝不写 localStorage、提交后立即清空。
`PUT` 私钥留空 = 不修改；填了 = 解密校验后重新加密覆盖。

### 8.4 存储形态

```
wallets.secret_enc        Fernet 密文（AES-128-CBC + HMAC，自带随机 IV 与时间戳）
.env HL_SECRET_ENC_KEY    加密密钥：仅存在于宿主机环境/挂载文件，不进镜像、不进 git
```

- 启动时 `HL_SECRET_ENC_KEY` 缺失 → 直接退出并打印生成命令，**不存在明文兜底分支**。
- `docker-compose.yml` 用 `env_file: .env` 注入，`.env` 在 `.gitignore` 中，`.env.example` 留空。
- 换密钥或数据迁移导致解不开 → 该钱包置 `key_state='unreadable'`，任务跳过并提示
  "请重新导入私钥"，不抛未捕获异常、不影响其他钱包。
- 备选模式（题目允许、本项目不采用）：私钥只放 `.env`。新增钱包需改文件并重启，
  破坏 F1 的界面增删改体验，故放弃。

### 8.5 解密时机

只在 `hl/exchange.py` 构造签名器前一刻：

```python
key   = crypto.decrypt(wallet.secret_enc)          # 按需解密，不常驻内存
acct  = Account.from_key(key)
exch  = Exchange(acct, wallet=wallet.address)      # 谁签字 / 动谁的钱
```

- 引擎启动时不解密全部钱包；每个 task 每次下单使用短生命周期对象。
- 解密结果不进入日志、DTO、异常消息；异常统一包装为 `SigningError(wallet_id=..)`。
- 日志过滤器兜底：正则遮罩 `0x?[0-9a-fA-F]{64}` 形态字符串，防 SDK 把 key 带进 error。

### 8.6 地址与 nonce 约定

- **查询**：一律用主账户 `address`（`clearinghouseState`、`openOrders`、`userFills` 等）。
  用 api_wallet 地址查询只会得到一个空账户。
- **签名**：用 api_wallet 私钥，`wallet=主账户地址`。
- **nonce**：按签名地址（`api_wallet_address`）维护单调递增计数器 `max(now_ms, last+1)`，
  不是按主账户地址；同一毫秒连发多笔必须自行递增，否则被拒。实现细节与 `Invalid nonce`
  的处置见 D13。
- **cloid 幂等**：由 `(task_id, coin, target, leg)` 确定性生成，重试复用；返回重复 cloid
  视为"上次已成功"（见 D9）。

### 8.7 访问控制与日志

- `docker-compose.yml` 默认 `127.0.0.1:8080:8080`；同时支持可选 `APP_PASSWORD`
  （HTTP Basic + session cookie）。两条都做，答辩现场从容器网络访问也不至于裸奔。
- 结构化日志（stdout JSON）字段白名单：`wallet_id` 而非地址全量、绝不出现密钥材料。
- `exec_logs.raw` 保存 HL 原始错误文本时同样过一遍遮罩。

### 8.8 这套方案的边界（如实写进 README）

- Fernet 密钥与 DB 在同一台机器：拿到 `.env` + `app.db` 就能解出私钥。真实防护靠的是
  "agent 私钥不能提现 + 端口只绑本机 + 容器内无多余进程"。
- 更强方案是启动时输入口令（Argon2 派生密钥），但破坏 AC-01 的无人值守一键启动，
  故不采用。
- Python 无法可靠擦除内存中的字符串。这点照实写在 README 的"已知限制"里，不假装做到。

### 8.9 多钱包语义与约束

**一个 follower 钱包 = 一个主账户 + 它的一把 agent 私钥。**"配置多个钱包"指的是配置**多个不同的主账户**，
而不是同一个主账户的多把钥匙。

原因是资金与仓位都在主账户上：

- 同一主账户的任何 agent 下单，仓位、保证金、手续费都记在同一个账户里 → 两把钥匙会互相踩目标仓位，
  "各跟各的 leader"根本无法成立（AC-16 会直接失败）。
- 看板的余额/可提现/已用保证金也完全共享，用户无法分辨哪部分是哪个任务的。

对应实现约束：

1. `wallets.address` 加 `UNIQUE`；API 新增/编辑时重复 → `409 address_already_registered`，
   并在 UI 上提示"该主账户已存在，请为其授权新的 API wallet 或复用已有配置"。
2. 引擎以 `task` 为单位隔离：独立 `coin_states`、独立退避、独立日志；
   只有 leader 行情/仓位缓存按 leader 地址共享（限频要求）。
3. 至少 2 个钱包同时跟单由引擎天然支持：串行循环逐个 task 处理，单个 task 出错不影响其他 task。

**已知限制（写进 README）**：如果用户想在**同一个主账户内**跑两种策略（不同 leader 或不同比例），
正确做法是使用 HL 的**子账户**（各自独立余额与仓位），这超出本题"个人版"范围，不在实现内。



---

## 9. 测试计划

纯逻辑单测（`make test`，pytest，无网络）：

1. 取整：szDecimals 各值下向零截断；5 位有效数字 + 小数位夹取；整数价。
2. 目标仓位：正负 leader、比例 0.1/0.2、`|T|*mark > maxNotional` 截断。
3. 最小金额：< $10 跳过；目标 0 时按实际持仓全平。
4. 反手：多翻空/空翻多 → 两笔计划，最终仓位 == 新目标。
5. 锁定：sync 立即建仓、new_only 首轮锁定、leader 归零解锁、**平掉又重开**解锁。
6. 决策表：锁定期/暂停/退避中/leader 读失败 → 全部 `skipped` + 正确 reason。
7. 钱包导入：非法 hex、长度错误、私钥反推地址与填写不一致 → 全部拒绝且不入库。
8. 密钥异常：`HL_SECRET_ENC_KEY` 变化后解密失败 → 钱包 `key_state='unreadable'`、
   任务跳过、其他钱包不受影响；DTO 序列化结果不含任何密钥字段。
9. 多钱包：同一主账户地址重复导入 → 拒绝；两个不同主账户的 task 并行运行互不干扰。
10. 数值往返：`Decimal` → DB 字符串 → 读回，数量/价格逐位一致；`cloid` 在往返后保持不变。
11. nonce：同一毫秒连发多笔 → 严格递增；`Invalid nonce` 后重置计数器只重签一次。
12. 错误分类：六类典型错误文本 → 归到正确分类与对应处置（D13 表格逐行断言）。

集成自测（有凭据后）：先跑 `scripts/probe.py` 采集真实错误文本，再在 testnet 跑 AC-03–AC-09 主链路。

---

## 10. AC 用例映射

| AC | 实现落点 |
| --- | --- |
| AC-01 | 单服务 compose + 多阶段镜像，启动即建表，`/api/health` 就绪 |
| AC-02 | dashboard 直连 HL `/info`（每 3s 缓存），DTO 不含私钥 |
| AC-03 | `start(sync)` → 立即 `CommandEvent` 全量对账，含 `updateLeverage` |
| AC-04 | `start(new_only)` → 非零币种写 `locked=1`，compare 页显示"锁定" |
| AC-05–08 | WS `userFills` → 增量重算；D3/D4/D6 的截断、IOC、先平后开 |
| AC-09 | D5 例外分支：按实际持仓 reduceOnly 全平 |
| AC-10 | 只以 fills 为准，`openOrders` 不触发任何动作 |
| AC-11 | D8 双来源解锁（对账归零 + fills 归零） |
| AC-12 | 30s 定时对账（<60s 要求留余量），日志 `source='reconcile'` |
| AC-13 | 改参数落库 + `CommandEvent(param)` 立即重算 |
| AC-14 | 启动加载 `status='running'` → 立即对账；cloid 幂等防重复单 |
| AC-15 | `paused` 状态在 planner 直接短路；stop+close 走全平流程 |
| AC-16 | task 以 `leader` 为键共享缓存，互不影响 |
| AC-17 | D3 截断 + `reason='truncated_by_max_notional'` |
| AC-18 | D10 退避 + 原始错误落库，per-task 隔离 |
| AC-19 | 所有触发统一走 `exec_logs`，字段覆盖 F5 |
| AC-20 | §8 全套：Fernet 密文存储、DTO 白名单、日志遮罩、`key_state` 异常分支、SQLite 内无明文 |

### 10.1 验收演示脚本（15 分钟）

固定顺序，每步都指向一个 AC，避免现场临场想：

1. `docker compose up -d --build` → 打开页面（AC-01）。
2. 加钱包 A、B，故意粘贴错误私钥一次 → 被拒绝；正确导入后私钥不回显（AC-02, AC-20）。
3. A 建 sync/0.1，B 建 new_only/0.2，都跟 L1 → 对比页显示 BTC/ETH 为锁定（AC-03, AC-04）。
4. L1 开 SOL → 10 秒内 A、B 建仓（AC-05）；再加仓（AC-06）、减半（AC-07）。
5. L1 一笔反手 → A、B 收敛到新目标（AC-08）。
6. L1 全平 SOL → 无残仓（AC-09）。
7. 手动平掉 A 一半 ETH → 展示日志 `source='reconcile'`（AC-12）。
8. A 比例改 0.2 → 展示立即调仓（AC-13）。
9. `docker compose stop/start` → 展示恢复与 B 的 ETH 解锁（AC-14）。
10. 停止 B 并勾选平仓（AC-15）；最后打开日志页翻一遍（AC-19）。

---

## 11. 时间预算（12–16h）

| 阶段 | 时长 | 产出 |
| --- | --- | --- |
| S0 脚手架 | 1.5h | Dockerfile/compose/env、FastAPI 骨架、建表、健康检查 |
| S1 HL 适配层 | 2.5h | info + 签名 + nonce + 下单/设杠杆，本地脚本冒烟 |
| S2 引擎 | 3.5h | planner/executor/loop，五种触发，反手与退避 |
| S3 API + 前端 | 4h | 钱包、任务、看板、对比、日志五页 |
| S4 WS + 健壮性 | 2h | 补漏重连、共享缓存、限频、停机恢复 |
| S5 测试 + README | 2h | 单测、AC 自查、文档 |

> S1 里额外预留 0.5h：`scripts/probe.py` 抓 HL 真实错误文本，回填 D13 的正则表。

### 11.1 降级顺序（做不完时先砍什么）

从先砍到后砍：**挂单镜像/统计类加分项 > 前端视觉细节 > 多 leader（AC-16 为否决项时提前）> 单测覆盖率**。
**绝不砍**：幂等（cloid）、退避、读失败不误平仓、私钥加密、一键启动、写日志。
README 的"未完成项"按真实完成度写，每条补一句"如果继续做我会怎么做"。

---

## 12. 已知风险

1. **10 秒时限**：主网 IOC 可能需要二次补单；靠"WS 触发 + 收到订单响应即重算"压时延，
   兜底是 30s 对账。
2. **SDK 同步 + WS 异步**：两套 I/O 模型，边界只在 `WsListener → queue`，其余全同步。
3. **最小金额 $10**：小仓位平仓可能被拒，只能记日志 + 退避；主网验证一次实际行为。
4. **反手两笔非原子**：中途断开时临时敞口为 0 或半仓，下一轮对账修复；可接受。
5. **测试网领水**依赖主网充值记录，题目说明由出题方提供已充值钱包。
6. **HIP-3 过滤**：仅使用默认 perp universe 的 `meta`，不在其中的币种一律忽略。
7. **HL 错误文本未实测**：D13 的正则表基于经验，需在拿到凭据后先用 `scripts/probe.py` 采集真实文本修正；
   在修正前，未知错误一律走 `unknown` + 退避，不会误停 task。
8. **同一主账户只能配一个 follower**：仓位在主账户上合并，多把 agent 会互相覆盖（§8.9）。

---

## 13. 已定取舍

| # | 项 | 决定 | 依据 |
| --- | --- | --- | --- |
| 1 | 反手 | **先平后开两笔**：①`reduceOnly` 全平 ②按新目标开仓 | `reduceOnly` 不跨越零点，两笔各自可失败可重试；一笔穿越会把仓位放大到目标之外，风险不可控。README 写明 |
| 2 | `stop` 默认行为 | **默认保留仓位**，UI 上"停止时平掉跟随币种"默认不勾选 | 题目要求"可选"，默认不主动市价平仓更安全（不 surprise 用户）；AC-15 会显式勾选。仅 IOC 下单，无需撤单流程；保险起见 stop+close 前仍尝试撤销该钱包在跟随币种上的残留挂单 |
| 3 | 对账周期 | **30 秒** | 要求 ≤60s，30s 给 AC-12/13/14 留一倍余量；成本可忽略（每 task 每次 ~2 权重，两个 task 每分钟 ~8，预算 1200） |
| 4 | 网络切换 | **只由环境变量 `HL_NETWORK` 决定**，页面只读展示当前网络徽标 | 符合第 4 节要求；页面切网会让"当前 DB 里的钱包属于哪个网络"产生歧义 |
| 5 | 访问控制 | **两个都做**：默认绑 `127.0.0.1`，同时支持可选 `APP_PASSWORD` | 题目是"或"关系，两条都满足不增加成本，答辩现场更稳（评委可能从容器网络访问） |
| 6 | cloid | 做，确定性生成 + 重试复用 | 重复 cloid 被拒可判定为"上次已成功"，是 AC-14 幂等的直接证据 |
| 7 | WS | 做 `userFills` + 重连补漏，轮询兜底 | 10 秒时限下轮询不稳；且是加分项 |
| 8 | 前端 | React + Vite | 看板四块数据 + 对比页 + 日志分页，模板渲染写起来更慢 |
| 9 | 数值存储 | 所有数量/价格/比例列存 **`TEXT`（Decimal 字符串）**，取整用 `ROUND_DOWN` | 原稿 DDL 用 `REAL` 与"全程 Decimal"自相矛盾，会产生取整漂移与浮点残渣；见 D14 |
| 10 | 决策表 | 11 条优先级判定，命中即返回，`planner` 只出 `Action` | 把规则写成一张表，代码即文档，答辩时可直接对着讲；见 D12 |
| 11 | 错误处理 | 六类错误分类 + 差异化处置（授权类停 task，nonce 类重签一次） | "一律退避"会让未授权 agent 无限刷单；见 D13 |
| 12 | DRY_RUN 影子模式 | **不做** | 会额外花约 1 小时且不在必做项；用 `scripts/probe.py` + 纯单测替代验证 |
| 13 | 演示脚本 | 固定 10 步（见 §10.1） | 答辩 15 分钟演示不临场发挥，每步对应一个 AC |

风格约定（实现时统一遵守）：

- 数量、价格、比例全程 `decimal.Decimal`，只在 DTO 序列化时转 `str`，**任何下单参数不经过 float**，
  DB 往返也是字符串（D14）。
- `planner.py` 不 import `hyperliquid`、不碰 DB、不碰网络 → 单测可直接断言。
- 引擎里每个 task 的循环体包 try/except，异常只记录并跳过本轮，不外抛。
- 所有外部错误的原始文本落 `exec_logs.raw`，前端可展开查看（AC-18）。
- 时间戳统一毫秒 `INTEGER`；日志里的"延迟"= follower 下单时刻 − leader 成交时刻（`latency_ms`）。

