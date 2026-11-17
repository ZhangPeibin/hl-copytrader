import { useCallback, useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  REASON_LABELS,
  createTask,
  deleteTask,
  describeError,
  fetchCompare,
  fetchTasks,
  fetchWallets,
  pauseTask,
  resumeTask,
  startTask,
  stopTask,
  updateTask,
  type Compare,
  type Task,
  type TaskPayload,
  type Wallet,
} from "../api";

type FormState = {
  walletId: string;
  leader: string;
  mode: "sync" | "new_only";
  ratio: string;
  maxNotional: string;
  maxLeverage: string;
  slippage: string;
  closeOnStop: boolean;
};

const EMPTY: FormState = {
  walletId: "",
  leader: "",
  mode: "sync",
  ratio: "0.1",
  maxNotional: "1000",
  maxLeverage: "10",
  slippage: "0.01",
  closeOnStop: false,
};

const MODE_LABELS: Record<string, string> = {
  sync: "同步",
  new_only: "只跟新单",
};

const STATUS_LABELS: Record<string, string> = {
  running: "运行中",
  paused: "已暂停",
  stopped: "已停止",
};

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [wallets, setWallets] = useState<Wallet[]>([]);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [compare, setCompare] = useState<Compare | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();

  const load = useCallback(async () => {
    try {
      const [nextTasks, nextWallets] = await Promise.all([fetchTasks(), fetchWallets()]);
      setTasks(nextTasks);
      setWallets(nextWallets);
    } catch (err) {
      setError(describeError(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const loadCompare = useCallback(async (taskId: number) => {
    try {
      setCompare(await fetchCompare(taskId));
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }, []);

  useEffect(() => {
    const requested = Number(searchParams.get("task"));
    if (Number.isFinite(requested) && requested > 0) {
      void loadCompare(requested);
    }
  }, []);

  useEffect(() => {
    if (!compare) {
      return;
    }
    const timer = window.setInterval(() => void loadCompare(compare.task_id), 5000);
    return () => window.clearInterval(timer);
  }, [compare, loadCompare]);

  const update = (field: keyof FormState) => (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const value = event.target.value;
    setForm((current) => ({ ...current, [field]: value }));
  };

  const reset = () => {
    setForm(EMPTY);
    setEditingId(null);
    setError(null);
  };

  const startEdit = (task: Task) => {
    setEditingId(task.id);
    setError(null);
    setNotice(null);
    setForm({
      walletId: String(task.wallet_id),
      leader: task.leader,
      mode: task.mode,
      ratio: task.ratio,
      maxNotional: task.max_notional_usd,
      maxLeverage: task.max_leverage,
      slippage: task.max_slippage,
      closeOnStop: task.close_on_stop,
    });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setNotice(null);

    const walletId = Number(form.walletId);
    if (!Number.isFinite(walletId) || walletId <= 0) {
      setError("请选择 follower 钱包");
      return;
    }
    const leverage = Number(form.maxLeverage);
    if (!Number.isInteger(leverage)) {
      setError("杠杆需为整数");
      return;
    }

    const payload: TaskPayload = {
      wallet_id: walletId,
      leader: form.leader.trim(),
      mode: form.mode,
      ratio: form.ratio.trim(),
      max_notional_usd: form.maxNotional.trim(),
      max_leverage: leverage,
      max_slippage: form.slippage.trim(),
      close_on_stop: form.closeOnStop,
    };

    setBusy(true);
    try {
      if (editingId === null) {
        const created = await createTask(payload);
        setNotice("任务已创建");
        setCompare(null);
        setForm(EMPTY);
        await load();
        void loadCompare(created.id);
      } else {
        await updateTask(editingId, payload);
        setNotice("任务已保存");
        setForm(EMPTY);
        setEditingId(null);
        await load();
      }
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (task: Task) => {
    if (!window.confirm(`删除任务 #${task.id}？`)) {
      return;
    }
    try {
      await deleteTask(task.id);
      if (compare?.task_id === task.id) {
        setCompare(null);
      }
      setNotice("任务已删除");
      await load();
    } catch (err) {
      setError(describeError(err));
    }
  };

  const runLifecycle = async (task: Task, action: "start" | "pause" | "resume" | "stop" | "stop-close") => {
    setError(null);
    setNotice(null);
    if (action === "stop-close" && !window.confirm(`停止任务 #${task.id} 并按市价平掉跟随仓位？`)) {
      return;
    }
    try {
      if (action === "start") await startTask(task.id);
      if (action === "pause") await pauseTask(task.id);
      if (action === "resume") await resumeTask(task.id);
      if (action === "stop") await stopTask(task.id, false);
      if (action === "stop-close") await stopTask(task.id, true);
      setNotice("状态已更新");
      await load();
    } catch (err) {
      setError(describeError(err));
    }
  };

  const openCompare = (taskId: number) => {
    setSearchParams({ task: String(taskId) });
    void loadCompare(taskId);
  };

  const compareTask = tasks.find((task) => task.id === compare?.task_id) ?? null;

  return (
    <>
      <header>
        <h1>任务</h1>
        <p className="sub">任务创建后即运行；暂停/停止即时生效，停止并平仓按 reduceOnly 全平。</p>
      </header>

      {error && <p className="error">{error}</p>}
      {notice && <p className="notice">{notice}</p>}

      {wallets.length === 0 && (
        <p className="panel empty">
          还没有钱包，请先到 <Link to="/wallets">钱包</Link> 页面导入。
        </p>
      )}

      <form className="panel" onSubmit={submit}>
        <div className="panel-head">
          <h2>{editingId === null ? "新建任务" : `编辑任务 #${editingId}`}</h2>
          {editingId !== null && (
            <button type="button" className="link" onClick={reset}>
              取消编辑
            </button>
          )}
        </div>

        <div className="fields">
          <label>
            <span>Follower 钱包</span>
            <select value={form.walletId} onChange={update("walletId")}>
              <option value="">选择钱包…</option>
              {wallets.map((wallet) => (
                <option key={wallet.id} value={wallet.id}>
                  {wallet.name} · {wallet.address.slice(0, 8)}…
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Leader 地址</span>
            <input
              value={form.leader}
              onChange={update("leader")}
              placeholder="0x…"
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          <label>
            <span>模式</span>
            <select value={form.mode} onChange={update("mode")}>
              <option value="sync">sync · 同步现有仓位</option>
              <option value="new_only">new_only · 只跟新单</option>
            </select>
          </label>
          <label>
            <span>跟随比例</span>
            <input value={form.ratio} onChange={update("ratio")} inputMode="decimal" />
          </label>
          <label>
            <span>最大名义（USD）</span>
            <input value={form.maxNotional} onChange={update("maxNotional")} inputMode="decimal" />
          </label>
          <label>
            <span>最大杠杆</span>
            <input value={form.maxLeverage} onChange={update("maxLeverage")} inputMode="numeric" />
          </label>
          <label>
            <span>最大滑点</span>
            <input value={form.slippage} onChange={update("slippage")} inputMode="decimal" />
          </label>
          <label className="checkbox">
            <span>停止时默认平仓</span>
            <input
              type="checkbox"
              checked={form.closeOnStop}
              onChange={(event) => setForm((current) => ({ ...current, closeOnStop: event.target.checked }))}
            />
          </label>
        </div>

        <div className="panel-foot">
          <button type="submit" disabled={busy || wallets.length === 0}>
            {busy ? "提交中…" : editingId === null ? "创建" : "保存"}
          </button>
          <span className="hint">数量、价格与比例全程使用 Decimal 字符串，不经过 float。</span>
        </div>
      </form>

      <section className="panel">
        <div className="panel-head">
          <h2>任务列表</h2>
          <span className="hint">{tasks.length} 个</span>
        </div>

        {tasks.length === 0 ? (
          <p className="empty">还没有任务。</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Leader</th>
                  <th>模式</th>
                  <th>比例</th>
                  <th>上限</th>
                  <th>杠杆</th>
                  <th>滑点</th>
                  <th>状态</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {tasks.map((task) => (
                  <tr key={task.id}>
                    <td>{task.id}</td>
                    <td className="mono" title={task.leader}>
                      {task.leader}
                    </td>
                    <td>{MODE_LABELS[task.mode] ?? task.mode}</td>
                    <td className="num">{task.ratio}</td>
                    <td className="num">{task.max_notional_usd}</td>
                    <td className="num">{task.max_leverage}x</td>
                    <td className="num">{task.max_slippage}</td>
                    <td>
                      <span className={task.status === "running" ? "badge ok" : "badge warn"}>
                        {STATUS_LABELS[task.status] ?? task.status}
                      </span>
                    </td>
                    <td className="actions">
                      {task.status === "stopped" && (
                        <button type="button" className="link" onClick={() => void runLifecycle(task, "start")}>
                          启动
                        </button>
                      )}
                      {task.status === "running" && (
                        <button type="button" className="link" onClick={() => void runLifecycle(task, "pause")}>
                          暂停
                        </button>
                      )}
                      {task.status === "paused" && (
                        <button type="button" className="link" onClick={() => void runLifecycle(task, "resume")}>
                          恢复
                        </button>
                      )}
                      {task.status !== "stopped" && (
                        <button type="button" className="link" onClick={() => void runLifecycle(task, "stop")}>
                          停止
                        </button>
                      )}
                      {task.status !== "stopped" && (
                        <button type="button" className="link danger" onClick={() => void runLifecycle(task, "stop-close")}>
                          平仓
                        </button>
                      )}
                      <button type="button" className="link" onClick={() => openCompare(task.id)}>
                        对比
                      </button>
                      <button type="button" className="link" onClick={() => startEdit(task)}>
                        编辑
                      </button>
                      <button type="button" className="link danger" onClick={() => void remove(task)}>
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

      {compare && (
        <section className="panel">
          <div className="panel-head">
            <h2>对比 · 任务 #{compare.task_id}</h2>
            <span className="hint">
              {compareTask ? MODE_LABELS[compareTask.mode] : ""} · 更新于{" "}
              {new Date(compare.fetched_at_ms).toLocaleTimeString()}
            </span>
          </div>

          {compare.rows.length === 0 ? (
            <p className="empty">该任务当前没有需要对比的币种。</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>币种</th>
                    <th>Leader 仓位</th>
                    <th>目标</th>
                    <th>实际</th>
                    <th>偏差</th>
                    <th>标记价</th>
                    <th>锁定</th>
                    <th>判定</th>
                  </tr>
                </thead>
                <tbody>
                  {compare.rows.map((row) => (
                    <tr key={row.coin}>
                      <td>{row.coin}</td>
                      <td className="num">{row.leader_size}</td>
                      <td className="num">{row.target}</td>
                      <td className="num">{row.follower_size}</td>
                      <td className={row.delta === "0" ? "num" : "num strong"}>{row.delta}</td>
                      <td className="num">{row.mark_px ?? "—"}</td>
                      <td>{row.locked ? "是" : "否"}</td>
                      <td>
                        {REASON_LABELS[row.reason] ?? row.reason}
                        {row.truncated && <span className="badge warn small">已截断</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </>
  );
}
