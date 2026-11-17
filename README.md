# HL Copytrader

个人版 Hyperliquid 跟单网站：用多个自有钱包按比例跟随 HL 上任意地址的永续合约交易，
`docker compose` 一键启动。

> 当前状态：T01–T12 全部落地。本地逻辑、真实主网只读数据与 WebSocket 订阅已验证；
> 带资金的下单链路需要 testnet 凭据或自有小额账户实操确认，见 [docs/AC-CHECK.md](docs/AC-CHECK.md)。

## 能做什么

- 导入多个 follower 钱包（每个是不同主账户 + 它的一把 API wallet 私钥），私钥加密存储
- 配置跟单任务：leader、按比例跟随、最大名义、最大杠杆、最大滑点、`sync` / `new_only`
- 引擎串行对账：leader 的成交经 WebSocket 触发，30 秒定时兜底，每轮重算全量状态
- 自动处理加仓、减仓、全平、反手（先平后开）、超限截断、低于最小名义跳过、失败退避
- 任务启停、停止并平仓、改参数即时生效、进程重启自动恢复
- 看板：钱包余额/仓位/挂单、任务与 leader 对比、执行日志（含原始错误文本）

## 技术栈

- 后端：Python 3.12 + FastAPI + SQLite(WAL)；`hyperliquid-python-sdk` 只用于 EIP-712 签名，
  `/info` 走轻量 `requests` 客户端（官方 `Info` 构造时会强制联网）
- 引擎：单线程同步串行循环（同币种下单天然互斥）；WS 线程只负责把 `userFills` 投进队列
- 前端：React 18 + Vite + TypeScript，构建产物由 FastAPI 静态挂载

## 快速开始

```bash
cp .env.example .env      # 必填 HL_SECRET_ENC_KEY
docker compose up -d --build
open http://127.0.0.1:8080
```

生成加密密钥：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

缺少 `HL_SECRET_ENC_KEY` 时进程直接退出，没有明文兜底。

本地开发（不打包）：

```bash
make dev          # API 127.0.0.1:8080 + Vite 127.0.0.1:5173
make test         # pytest，207 个用例，无网络
```

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `HL_NETWORK` | `mainnet` | `mainnet` / `testnet`，只由环境变量决定，页面只展示 |
| `HL_SECRET_ENC_KEY` | 无（必填） | Fernet 密钥，加密 API wallet 私钥 |
| `APP_PASSWORD` | 空 | 可选访问密码（HTTP Basic + session cookie） |
| `APP_BIND` | `127.0.0.1` | compose 的宿主绑定地址；需要局域网访问时改 `0.0.0.0` |
| `DATA_DIR` | `/data` | SQLite 与运行数据目录 |
| `RECONCILE_INTERVAL_SEC` | `30` | 定时对账周期 |
| `LEADER_CACHE_TTL_SEC` | `2` | leader 状态缓存 TTL（代码内默认 3s，可配） |
| `MAX_SLIPPAGE_DEFAULT` | `0.01` | 新任务默认滑点 |

## 每个 follower 钱包需要准备什么

1. 官方前端连接主账户 → **More → API → Generate API wallet**，主账户签名确认。
2. 记录页面显示的 **private key**（只显示一次）和 **address**。
3. 在网站「钱包」页新增：名称 + **主账户地址**（查询用）+ **API wallet 地址** + **API wallet 私钥**（签名用）。
4. 导入时系统用私钥反推地址并与填写的地址比对，不一致直接拒绝。

`API wallet`（协议里叫 `agent`）只能交易、不能提现。主账户私钥**不需要也不要导入**。
私钥经 Fernet 加密后存库，接口与页面永不回显；日志按 64-hex 规则遮罩。

需要多钱包跟单时，**每个 follower 钱包必须是不同的主账户**（资金和仓位都在主账户上，
同一主账户的多把钥匙会互相覆盖仓位）。同一主账户地址重复导入会被拒绝。

## 使用流程

1. **钱包**：导入 follower，`key_state` 显示为「正常」。
2. **任务**：选钱包、填 leader、比例与上限；`sync` 立即对齐 leader 现有仓位，`new_only`
   在启动时锁定 leader 已有仓位，只跟之后的新单。
3. **对比**：任务列表点「对比」，看到每个币种的 leader 仓 / 目标 / 实际 / 偏差 / 锁定与判定原因。
4. **日志**：按任务、币种、结果筛选；失败行的「详情」展开 HL 原始错误（已遮罩）。
5. **生命周期**：暂停/恢复/停止；「平仓」会先撤该钱包在跟随币种上的挂单，再按实际持仓
   `reduceOnly` 全平。

## 跟单规则摘要

- 事件只是提醒：每轮都从 leader 与 follower 的最新全量状态重算，读不到 leader 绝不当空仓。
- 每币种每轮只有一个动作和一个原因，按固定优先级：超限截断 → 暂停/停止 → leader 不可用
  → 退避 → 锁定 → 无仓位 → 全平 → 最小名义 → 反手 → 首次设杠杆 → 下单。
- 反手拆两笔：先 `reduceOnly` 平掉实际持仓，再按新目标开仓。
- 平仓数量用实际持仓 `|F|`，不用增量 `Δ`，避免截断残仓。
- 下单用 IOC 限价模拟市价：买入不高于 `mid*(1+slippage)`，卖出不低于 `mid*(1-slippage)`，
  价格按 5 位有效数字与 `6 - szDecimals` 小数位取整。
- 失败按 `(task, coin)` 指数退避 5s → 10s → … → 60s；`invalid nonce` 重置后只重签一次；
  未授权 agent 直接停止该任务；重复 cloid 视为上次已成功。
- 数值全程 `Decimal`，DB 数值列是规范字符串，API 数值字段输出字符串。

## 已知限制

- **最小名义 $10**：更小的加仓与残余平仓可能被 HL 拒绝，只能记录原始错误并退避重试。
- **反手不是原子操作**：两笔之间进程中断会短暂空仓或半仓，下一轮对账修复。
- **Fernet 密钥与 DB 同机**：拿到 `.env` 与 `app.db` 就能解出私钥；防护依赖“agent 私钥
  不能提现 + 默认只绑本机 + 容器内无多余进程”。
- **Python 无法可靠擦除内存中的私钥**，本项目不假装做到。
- **同一主账户只能配一个 follower**：仓位在主账户上合并。需要同账户多策略请用 HL 子账户。
- **只跟默认 perp universe**：不在 `/info meta` 里的 HIP-3 币种一律忽略。
- **单副本单 worker**：多副本会让“单写者 + 串行互斥”失效。

## 未完成项

按真实完成度列出，每条补一句继续做会怎么做：

1. **HL 真实错误文本未全部实测**：`scripts/probe.py` 可在 testnet 构造五类错误采集原文；
   拿到凭据后运行，把缺失的正则补进 `hlcopy/hl/errors.py`。
2. **带资金的 testnet 端到端未跑**：需要用已充值测试网账户走一遍 AC-03–AC-09，确认
   IOC 成交、反手两笔、$10 边界与 reduceOnly 行为；若与预期不符，先改决策表再改代码。
3. **演示脚本未在真实 leader 上录制**：脚本见下，步骤与断言已固定，只差一次实操。
4. **没有统计/告警/挂单镜像**：按原计划不做；继续做会先加“跟单与 leader 的滑点归因”报表。

## 验收演示（约 15 分钟，testnet）

| # | 操作 | 对应 AC |
| --- | --- | --- |
| 1 | `docker compose up -d --build`，打开页面，`/api/health` 为 ok | AC-01 |
| 2 | 钱包页先粘贴错误私钥（被拒），再正确导入 A、B，页面不回显私钥 | AC-02 / AC-20 |
| 3 | A 建 `sync` 0.1，B 建 `new_only` 0.2，都跟 L1；B 的对比页显示 BTC/ETH 锁定 | AC-03 / AC-04 |
| 4 | L1 开 SOL → A、B 在 10 秒内建仓 | AC-05 |
| 5 | L1 加仓 → 再减半 → A、B 跟随 | AC-06 / AC-07 |
| 6 | L1 一笔反手 → A、B 先平后开，收敛到新目标 | AC-08 |
| 7 | L1 全平 SOL → A、B 无残仓 | AC-09 |
| 8 | 手动平掉 A 一半 ETH，等一次对账 → 日志出现 `source=reconcile` 的修复单 | AC-12 |
| 9 | A 比例改 0.2 → 立即出现调仓日志 | AC-13 |
| 10 | `docker compose stop/start` → 运行中的任务自动恢复；停止 B 勾选平仓 | AC-14 / AC-15 |
| 11 | 日志页按任务/币种/结果筛选，展开一条失败原文 | AC-19 |

## 排障

- **容器起不来**：先看 `docker compose logs app`；缺少 `HL_SECRET_ENC_KEY` 会打印生成命令。
- **WS 一直 disconnected**：若本机走 SOCKS 代理，已内置 `python-socks`；否则检查
  `wss://api.hyperliquid.xyz/ws` 是否可达。
- **钱包显示「需重新导入」**：`HL_SECRET_ENC_KEY` 换过或密文损坏；重新导入该钱包私钥即可。
- **任务自动停止**：日志里 `agent_unauthorized` 表示 API wallet 失效，去 HL 页面重新生成。

## 文档

- [docs/PLAN.md](docs/PLAN.md) — 权威设计：决策表、数据模型、取舍、AC 映射
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 施工视图与下单流图
- [docs/AC-CHECK.md](docs/AC-CHECK.md) — AC-01…AC-20 逐条状态与证据
