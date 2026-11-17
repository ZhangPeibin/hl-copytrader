# HL Copytrader

个人版 Hyperliquid 跟单网站：用多个自有钱包按比例跟随 HL 上任意地址的永续合约交易，
`docker compose` 一键启动。

> 当前阶段：设计稿，尚未开始实现。完整方案见 [docs/PLAN.md](docs/PLAN.md)。

## 技术栈

- 后端：Python 3.12 + FastAPI + `hyperliquid-python-sdk` + SQLite(WAL)
- 跟单引擎：单线程同步串行循环（天然满足同币种下单互斥）
- 事件：WebSocket `userFills` + 定时对账兜底
- 前端：React + Vite + TypeScript（构建产物由 FastAPI 静态挂载）

## 规划中的启动方式

```bash
cp .env.example .env      # 填入 HL_SECRET_ENC_KEY 与钱包信息
docker compose up -d --build
open http://127.0.0.1:8080
```

## 环境变量

见 `.env.example`。

| 变量 | 说明 |
| --- | --- |
| `HL_NETWORK` | `mainnet`（默认）/ `testnet` |
| `HL_SECRET_ENC_KEY` | 私钥加密密钥（Fernet），`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `APP_PASSWORD` | 可选访问密码；未设置时端口只绑 127.0.0.1 |
| `DATA_DIR` | SQLite 与运行数据目录，默认 `/data` |

## 每个 follower 钱包需要准备什么

完整说明见 [docs/PLAN.md §8](docs/PLAN.md)。

1. 官方前端连接主账户 → **More → API → Generate API wallet**，主账户签名确认。
2. 记录页面显示的 **private key**（只显示一次）和 **address**。
3. 在网站里新增钱包：名称 + **主账户地址**（查询用）+ **API wallet 地址** + **API wallet 私钥**（签名用）。
4. 导入时系统会用私钥反推地址与第 3 步填写的地址比对，不一致直接拒绝。

注意：`API wallet`（协议里叫 `agent`）只能交易、不能提现。主账户私钥**不需要也不要导入**。
私钥经 Fernet 加密后存库，接口与页面永不回显。

需要多钱包跟单时，**每个 follower 钱包必须是不同的主账户**（资金和仓位都在主账户上，
同一主账户的多把钥匙会互相覆盖仓位）。同一主账户地址重复导入会被拒绝。


## 文档

- [docs/PLAN.md](docs/PLAN.md) — 架构、跟单规则实现、数据模型、AC 映射、时间预算与待讨论项
