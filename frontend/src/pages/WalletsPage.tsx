import { useCallback, useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { Link } from "react-router-dom";

import {
  createWallet,
  deleteWallet,
  describeError,
  fetchWallets,
  updateWallet,
  type Wallet,
  type WalletPayload,
} from "../api";

type FormState = {
  name: string;
  address: string;
  apiWalletAddress: string;
  privateKey: string;
};

const EMPTY: FormState = { name: "", address: "", apiWalletAddress: "", privateKey: "" };

export default function WalletsPage() {
  const [wallets, setWallets] = useState<Wallet[]>([]);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setWallets(await fetchWallets());
    } catch (err) {
      setError(describeError(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const update = (field: keyof FormState) => (event: ChangeEvent<HTMLInputElement>) => {
    setForm((current) => ({ ...current, [field]: event.target.value }));
  };

  const reset = () => {
    setForm(EMPTY);
    setEditingId(null);
    setError(null);
  };

  const startEdit = (wallet: Wallet) => {
    setEditingId(wallet.id);
    setError(null);
    setNotice(null);
    setForm({
      name: wallet.name,
      address: wallet.address,
      apiWalletAddress: wallet.api_wallet_address,
      privateKey: "",
    });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setNotice(null);

    if (!form.name.trim()) {
      setError("请填写钱包名称");
      return;
    }
    if (editingId === null && !form.privateKey.trim()) {
      setError("导入钱包时必须填写 API wallet 私钥");
      return;
    }

    const payload: WalletPayload = {
      name: form.name.trim(),
      address: form.address.trim(),
      api_wallet_address: form.apiWalletAddress.trim(),
    };
    if (form.privateKey.trim()) {
      payload.private_key = form.privateKey.trim();
    }

    setBusy(true);
    try {
      if (editingId === null) {
        await createWallet(payload);
        setNotice("钱包已导入");
      } else {
        await updateWallet(editingId, payload);
        setNotice("已保存");
      }
      setForm(EMPTY);
      setEditingId(null);
      await load();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (wallet: Wallet) => {
    if (!window.confirm(`删除钱包「${wallet.name}」？此操作不可撤销。`)) {
      return;
    }
    setError(null);
    setNotice(null);
    try {
      await deleteWallet(wallet.id);
      setNotice("已删除");
      await load();
    } catch (err) {
      setError(describeError(err));
    }
  };

  return (
    <>
      <header>
        <h1>钱包</h1>
        <p className="sub">
          一个 follower 钱包 = 一个主账户 + 它的一把 API wallet 私钥。主账户地址重复会被拒绝。
        </p>
      </header>

      {error && <p className="error">{error}</p>}
      {notice && <p className="notice">{notice}</p>}

      <form className="panel" onSubmit={submit}>
        <div className="panel-head">
          <h2>{editingId === null ? "导入钱包" : "编辑钱包"}</h2>
          {editingId !== null && (
            <button type="button" className="link" onClick={reset}>
              取消编辑
            </button>
          )}
        </div>

        <div className="fields">
          <label>
            <span>名称</span>
            <input value={form.name} onChange={update("name")} maxLength={64} autoComplete="off" />
          </label>
          <label>
            <span>主账户地址（查询用）</span>
            <input
              value={form.address}
              onChange={update("address")}
              placeholder="0x…"
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          <label>
            <span>API wallet 地址（签名归属）</span>
            <input
              value={form.apiWalletAddress}
              onChange={update("apiWalletAddress")}
              placeholder="0x…"
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          <label>
            <span>API wallet 私钥{editingId !== null ? "（留空则不修改）" : ""}</span>
            <input
              type="password"
              value={form.privateKey}
              onChange={update("privateKey")}
              placeholder={editingId !== null ? "留空保持原私钥" : "只显示一次，请从 HL 页面复制"}
              autoComplete="off"
              spellCheck={false}
            />
          </label>
        </div>

        <div className="panel-foot">
          <button type="submit" disabled={busy}>
            {busy ? "提交中…" : editingId === null ? "导入" : "保存"}
          </button>
          <span className="hint">私钥经 Fernet 加密后入库，接口与页面永不回显。</span>
        </div>
      </form>

      <section className="panel">
        <div className="panel-head">
          <h2>已导入的钱包</h2>
          <span className="hint">{wallets.length} 个</span>
        </div>

        {wallets.length === 0 ? (
          <p className="empty">还没有钱包。</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>名称</th>
                  <th>主账户</th>
                  <th>API wallet</th>
                  <th>状态</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {wallets.map((wallet) => (
                  <tr key={wallet.id}>
                    <td>
                      <Link to={`/wallets/${wallet.id}`}>{wallet.name}</Link>
                    </td>
                    <td className="mono" title={wallet.address}>
                      {wallet.address}
                    </td>
                    <td className="mono" title={wallet.api_wallet_address}>
                      {wallet.api_wallet_address}
                    </td>
                    <td>
                      <span className={wallet.key_state === "ok" ? "badge ok" : "badge warn"}>
                        {wallet.key_state === "ok" ? "正常" : "需重新导入"}
                      </span>
                    </td>
                    <td className="actions">
                      <Link className="link" to={`/wallets/${wallet.id}`}>
                        详情
                      </Link>
                      <button type="button" className="link" onClick={() => startEdit(wallet)}>
                        编辑
                      </button>
                      <button type="button" className="link danger" onClick={() => remove(wallet)}>
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
