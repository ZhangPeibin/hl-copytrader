import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  describeError,
  fetchAccount,
  fetchMeta,
  fetchWallet,
  type Account,
  type Meta,
  type Wallet,
} from "../api";

const POLL_MS = 3000;

export default function WalletDetailPage() {
  const { id } = useParams();
  const walletId = Number(id);

  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadAccount = useCallback(async () => {
    if (!Number.isFinite(walletId)) {
      setError("钱包 id 无效");
      setLoading(false);
      return;
    }
    try {
      setAccount(await fetchAccount(walletId));
      setError(null);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setLoading(false);
    }
  }, [walletId]);

  useEffect(() => {
    if (!Number.isFinite(walletId)) {
      setError("钱包 id 无效");
      setLoading(false);
      return;
    }

    void fetchWallet(walletId)
      .then(setWallet)
      .catch((err) => setError(describeError(err)));
    void fetchMeta()
      .then(setMeta)
      .catch(() => setMeta(null));

    void loadAccount();
    const timer = window.setInterval(() => void loadAccount(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [walletId, loadAccount]);

  return (
    <>
      <header>
        <p className="back">
          <Link to="/wallets">← 返回钱包列表</Link>
        </p>
        <h1>{wallet ? wallet.name : "钱包详情"}</h1>
        <p className="sub">
          {wallet ? (
            <>
              主账户 <span className="mono inline">{wallet.address}</span>
              {meta ? ` · 市场 ${meta.coins.length} 个币种` : ""}
            </>
          ) : (
            "读取中…"
          )}
        </p>
      </header>

      {error && (
        <p className="error">
          {error}
          <button type="button" className="link inline" onClick={() => void loadAccount()}>
            重试
          </button>
        </p>
      )}

      {loading && !account && !error && <p className="empty">加载中…</p>}

      {account && (
        <>
          <section className="stats">
            <Stat label="账户价值" value={account.account_value} />
            <Stat label="可提现" value={account.withdrawable} />
            <Stat label="已用保证金" value={account.margin_used} />
            <Stat label="持仓 / 挂单" value={`${account.positions.length} / ${account.open_orders.length}`} />
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>持仓</h2>
              <span className="hint">更新于 {new Date(account.fetched_at_ms).toLocaleTimeString()}</span>
            </div>
            {account.positions.length === 0 ? (
              <p className="empty">当前没有持仓。</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>币种</th>
                      <th>方向</th>
                      <th>数量</th>
                      <th>开仓价</th>
                      <th>仓位价值</th>
                      <th>未实现盈亏</th>
                      <th>杠杆</th>
                      <th>强平价</th>
                    </tr>
                  </thead>
                  <tbody>
                    {account.positions.map((position) => (
                      <tr key={position.coin}>
                        <td>{position.coin}</td>
                        <td className={Number(position.size) >= 0 ? "long" : "short"}>
                          {Number(position.size) >= 0 ? "多" : "空"}
                        </td>
                        <td className="num">{position.size}</td>
                        <td className="num">{position.entry_px ?? "—"}</td>
                        <td className="num">{position.position_value}</td>
                        <td className={Number(position.unrealized_pnl) >= 0 ? "num long" : "num short"}>
                          {position.unrealized_pnl}
                        </td>
                        <td className="num">
                          {position.leverage}x {position.is_cross ? "全仓" : "逐仓"}
                        </td>
                        <td className="num">{position.liquidation_px ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>挂单</h2>
              <span className="hint">挂单不触发跟单动作</span>
            </div>
            {account.open_orders.length === 0 ? (
              <p className="empty">当前没有挂单。</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>币种</th>
                      <th>方向</th>
                      <th>价格</th>
                      <th>数量</th>
                      <th>仅减仓</th>
                    </tr>
                  </thead>
                  <tbody>
                    {account.open_orders.map((order) => (
                      <tr key={order.oid}>
                        <td>{order.coin}</td>
                        <td className={order.side === "B" ? "long" : "short"}>
                          {order.side === "B" ? "买" : "卖"}
                        </td>
                        <td className="num">{order.price}</td>
                        <td className="num">{order.size}</td>
                        <td>{order.reduce_only ? "是" : "否"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card">
      <span className="card-title">{label}</span>
      <span className="card-value">{value}</span>
    </div>
  );
}
