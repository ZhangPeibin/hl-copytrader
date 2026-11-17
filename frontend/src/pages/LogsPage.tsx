import { useCallback, useEffect, useState } from "react";

import { REASON_LABELS, describeError, fetchLogs, fetchTasks, type ExecLog, type Task } from "../api";

const PAGE_SIZE = 50;

const SOURCE_LABELS: Record<string, string> = {
  fill: "成交",
  reconcile: "对账",
  param: "改参数",
  start: "启动",
  resume: "恢复",
  stop: "停止",
};

const RESULT_LABELS: Record<string, string> = {
  filled: "成交",
  partial: "部分",
  failed: "失败",
  skipped: "跳过",
};

export default function LogsPage() {
  const [items, setItems] = useState<ExecLog[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [taskId, setTaskId] = useState("");
  const [coin, setCoin] = useState("");
  const [result, setResult] = useState("");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchTasks()
      .then(setTasks)
      .catch(() => setTasks([]));
  }, []);

  const load = useCallback(async () => {
    try {
      const page = await fetchLogs({
        task_id: taskId ? Number(taskId) : undefined,
        coin: coin.trim() ? coin.trim().toUpperCase() : undefined,
        result: result || undefined,
        limit: PAGE_SIZE,
        offset,
      });
      setItems(page.items);
      setTotal(page.total);
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }, [taskId, coin, result, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const current = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <>
      <header>
        <h1>执行日志</h1>
        <p className="sub">每笔下单、跳过与失败都落库；5 秒刷新一次。</p>
      </header>

      {error && <p className="error">{error}</p>}

      <section className="panel">
        <div className="fields">
          <label>
            <span>任务</span>
            <select value={taskId} onChange={(event) => { setTaskId(event.target.value); setOffset(0); }}>
              <option value="">全部任务</option>
              {tasks.map((task) => (
                <option key={task.id} value={task.id}>
                  #{task.id} · {task.leader.slice(0, 10)}…
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>币种</span>
            <input
              value={coin}
              onChange={(event) => { setCoin(event.target.value); setOffset(0); }}
              placeholder="BTC"
              autoComplete="off"
            />
          </label>
          <label>
            <span>结果</span>
            <select value={result} onChange={(event) => { setResult(event.target.value); setOffset(0); }}>
              <option value="">全部结果</option>
              <option value="filled">成交</option>
              <option value="partial">部分</option>
              <option value="failed">失败</option>
              <option value="skipped">跳过</option>
            </select>
          </label>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>日志</h2>
          <span className="hint">{total} 条</span>
        </div>

        {items.length === 0 ? (
          <p className="empty">没有匹配的日志。</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>任务</th>
                  <th>来源</th>
                  <th>币种</th>
                  <th>方向</th>
                  <th>数量</th>
                  <th>价格</th>
                  <th>结果</th>
                  <th>原因</th>
                  <th>延迟</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id}>
                    <td className="mono">{new Date(item.ts).toLocaleTimeString()}</td>
                    <td>{item.task_id ?? "—"}</td>
                    <td>{SOURCE_LABELS[item.source] ?? item.source}</td>
                    <td>{item.coin === "*" ? "全部" : item.coin}</td>
                    <td className={item.side === "buy" ? "long" : item.side === "sell" ? "short" : ""}>
                      {item.side === "buy" ? "买" : item.side === "sell" ? "卖" : "—"}
                    </td>
                    <td className="num">{item.qty ?? "—"}</td>
                    <td className="num">{item.price ?? "—"}</td>
                    <td>
                      <span
                        className={
                          item.result === "filled"
                            ? "badge ok"
                            : item.result === "failed"
                              ? "badge warn"
                              : "badge neutral"
                        }
                      >
                        {RESULT_LABELS[item.result] ?? item.result}
                      </span>
                    </td>
                    <td>{item.reason ? (REASON_LABELS[item.reason] ?? item.reason) : "—"}</td>
                    <td className="num">{item.latency_ms != null ? `${item.latency_ms}ms` : "—"}</td>
                    <td>
                      {item.raw ? (
                        <details>
                          <summary>详情</summary>
                          <p className="raw">{item.raw}</p>
                        </details>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="pager">
          <button
            type="button"
            className="link"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            上一页
          </button>
          <span className="hint">
            {current} / {pages}
          </span>
          <button
            type="button"
            className="link"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            下一页
          </button>
        </div>
      </section>
    </>
  );
}
