import { useEffect, useState } from "react";

type Health = {
  status: string;
  network: string;
  version: string;
  engine: { running: boolean; last_tick_ms: number | null; queue_depth: number };
  ws: { connected: boolean; subscriptions: number };
  db: { ok: boolean };
};

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const response = await fetch("/api/health");
        if (!response.ok) {
          throw new Error(`health ${response.status}`);
        }
        const body = (await response.json()) as Health;
        if (!cancelled) {
          setHealth(body);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "unreachable");
        }
      }
    };

    load();
    const timer = window.setInterval(load, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <main>
      <header>
        <h1>HL Copytrader</h1>
        <p className="sub">个人版 Hyperliquid 跟单 · 引擎骨架</p>
      </header>

      {error && <p className="error">无法连接后端：{error}</p>}

      <section className="grid">
        <Card title="状态" value={health ? health.status : "…"} tone={health?.status === "ok" ? "ok" : "warn"} />
        <Card title="网络" value={health ? health.network : "…"} />
        <Card title="版本" value={health ? health.version : "…"} />
        <Card title="数据库" value={health ? (health.db.ok ? "ok" : "error") : "…"} tone={health?.db.ok ? "ok" : "warn"} />
        <Card title="引擎" value={health ? (health.engine.running ? "running" : "idle") : "…"} />
        <Card title="队列积压" value={health ? String(health.engine.queue_depth) : "…"} />
        <Card title="WS" value={health ? (health.ws.connected ? "connected" : "disconnected") : "…"} />
        <Card title="WS 订阅" value={health ? String(health.ws.subscriptions) : "…"} />
      </section>

      <footer>
        <span>钱包、任务与跟单逻辑在后续 ticket 中接入。</span>
        <a href="/api/health">/api/health</a>
      </footer>
    </main>
  );
}

function Card({ title, value, tone }: { title: string; value: string; tone?: "ok" | "warn" }) {
  return (
    <div className="card">
      <span className="card-title">{title}</span>
      <span className={tone ? `card-value ${tone}` : "card-value"}>{value}</span>
    </div>
  );
}
