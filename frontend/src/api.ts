export type Wallet = {
  id: number;
  name: string;
  address: string;
  api_wallet_address: string;
  key_state: "ok" | "unreadable";
  created_at: number;
  updated_at: number;
};

export type Health = {
  status: string;
  network: string;
  version: string;
  engine: { running: boolean; last_tick_ms: number | null; queue_depth: number };
  ws: { connected: boolean; subscriptions: number };
  db: { ok: boolean };
};

export const ERROR_MESSAGES: Record<string, string> = {
  invalid_name: "名称需为 1-64 个字符",
  invalid_address: "地址格式错误",
  invalid_address_checksum: "地址校验和不正确，请原样粘贴",
  invalid_private_key: "私钥格式错误（需 64 位 hex，可带 0x）",
  private_key_address_mismatch: "私钥与填写的 API wallet 地址不一致",
  address_already_registered: "该主账户已存在，请为其授权新的 API wallet 或复用已有配置",
  name_already_registered: "该名称已被使用",
  api_wallet_requires_private_key: "修改 API wallet 地址时必须同时提供其私钥",
  wallet_in_use: "该钱包仍有任务，请先删除任务",
  wallet_not_found: "钱包不存在",
  task_not_found: "任务不存在",
  invalid_ratio: "比例需在 (0, 1] 之间",
  invalid_max_notional_usd: "最大名义需为正数",
  invalid_max_slippage: "滑点需在 0 到 0.1 之间",
  invalid_max_leverage: "杠杆需在 1 到 100 之间",
  invalid_leader: "leader 地址格式错误",
  invalid_transition: "当前状态不允许该操作",
};

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return ERROR_MESSAGES[error.code] ?? error.message;
  }
  return error instanceof Error ? error.message : "请求失败";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    const detail = body?.detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      throw new ApiError(response.status, String(detail.code ?? "error"), String(detail.message ?? ""));
    }
    const message = typeof detail === "string" ? detail : `HTTP ${response.status}`;
    throw new ApiError(response.status, "error", message);
  }

  return body as T;
}

export function fetchHealth(): Promise<Health> {
  return request<Health>("/api/health");
}

export function fetchWallets(): Promise<Wallet[]> {
  return request<Wallet[]>("/api/wallets");
}

export type WalletPayload = {
  name: string;
  address: string;
  api_wallet_address: string;
  private_key?: string;
};

export function createWallet(payload: WalletPayload): Promise<Wallet> {
  return request<Wallet>("/api/wallets", { method: "POST", body: JSON.stringify(payload) });
}

export function updateWallet(id: number, payload: Partial<WalletPayload>): Promise<Wallet> {
  return request<Wallet>(`/api/wallets/${id}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function deleteWallet(id: number): Promise<void> {
  return request<void>(`/api/wallets/${id}`, { method: "DELETE" });
}

export type CoinMeta = {
  name: string;
  sz_decimals: number;
  max_leverage: number;
};

export type Meta = {
  coins: CoinMeta[];
  fetched_at_ms: number;
};

export type Position = {
  coin: string;
  size: string;
  entry_px: string | null;
  position_value: string;
  unrealized_pnl: string;
  leverage: number;
  is_cross: boolean;
  liquidation_px: string | null;
};

export type OpenOrder = {
  coin: string;
  oid: number;
  side: string;
  price: string;
  size: string;
  reduce_only: boolean;
  ts: number;
};

export type Account = {
  address: string;
  account_value: string;
  withdrawable: string;
  margin_used: string;
  positions: Position[];
  open_orders: OpenOrder[];
  fetched_at_ms: number;
};

export function fetchWallet(id: number): Promise<Wallet> {
  return request<Wallet>(`/api/wallets/${id}`);
}

export function fetchAccount(id: number): Promise<Account> {
  return request<Account>(`/api/wallets/${id}/account`);
}

export function fetchMeta(): Promise<Meta> {
  return request<Meta>("/api/meta");
}

export type Task = {
  id: number;
  wallet_id: number;
  leader: string;
  mode: "sync" | "new_only";
  ratio: string;
  max_notional_usd: string;
  max_leverage: string;
  max_slippage: string;
  status: "stopped" | "running" | "paused";
  close_on_stop: boolean;
  created_at: number;
  updated_at: number;
};

export type TaskPayload = {
  wallet_id: number;
  leader: string;
  mode: "sync" | "new_only";
  ratio: string;
  max_notional_usd: string;
  max_leverage: number;
  max_slippage: string;
  close_on_stop: boolean;
};

export type CompareRow = {
  coin: string;
  leader_size: string;
  follower_size: string;
  target: string;
  delta: string;
  mark_px: string | null;
  locked: boolean;
  truncated: boolean;
  reason: string;
};

export type Compare = {
  task_id: number;
  mode: string;
  leader: string;
  address: string;
  fetched_at_ms: number;
  rows: CompareRow[];
};

export const REASON_LABELS: Record<string, string> = {
  task_paused: "任务已暂停",
  task_stopped: "任务已停止",
  leader_state_unavailable: "leader 状态不可用",
  mark_unavailable: "缺少标记价",
  backoff: "退避中",
  locked: "锁定",
  unlocked: "解锁（下一轮建仓）",
  no_position: "无持仓",
  close_all: "全平",
  below_min_notional: "低于最小名义",
  reverse: "反手（先平后开）",
  set_leverage: "首次建仓（先设杠杆）",
  place: "下单",
};

export function fetchTasks(): Promise<Task[]> {
  return request<Task[]>("/api/tasks");
}

export function createTask(payload: TaskPayload): Promise<Task> {
  return request<Task>("/api/tasks", { method: "POST", body: JSON.stringify(payload) });
}

export function updateTask(id: number, payload: Partial<TaskPayload>): Promise<Task> {
  return request<Task>(`/api/tasks/${id}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function deleteTask(id: number): Promise<void> {
  return request<void>(`/api/tasks/${id}`, { method: "DELETE" });
}

export function fetchCompare(id: number): Promise<Compare> {
  return request<Compare>(`/api/tasks/${id}/compare`);
}

export type ExecLog = {
  id: number;
  task_id: number | null;
  wallet_id: number | null;
  ts: number;
  source: string;
  coin: string;
  leader_pos: string | null;
  pos_before: string | null;
  target: string | null;
  side: string | null;
  qty: string | null;
  price: string | null;
  result: string;
  reason: string | null;
  latency_ms: number | null;
  oid: string | null;
  cloid: string | null;
  raw: string | null;
};

export type LogsPage = {
  items: ExecLog[];
  total: number;
  limit: number;
  offset: number;
};

export type LogFilters = {
  task_id?: number;
  coin?: string;
  result?: string;
  limit?: number;
  offset?: number;
};

export function fetchLogs(filters: LogFilters): Promise<LogsPage> {
  const params = new URLSearchParams();
  if (filters.task_id) params.set("task_id", String(filters.task_id));
  if (filters.coin) params.set("coin", filters.coin);
  if (filters.result) params.set("result", filters.result);
  params.set("limit", String(filters.limit ?? 50));
  params.set("offset", String(filters.offset ?? 0));
  return request<LogsPage>(`/api/logs?${params.toString()}`);
}

export function startTask(id: number): Promise<Task> {
  return request<Task>(`/api/tasks/${id}/start`, { method: "POST" });
}

export function pauseTask(id: number): Promise<Task> {
  return request<Task>(`/api/tasks/${id}/pause`, { method: "POST" });
}

export function resumeTask(id: number): Promise<Task> {
  return request<Task>(`/api/tasks/${id}/resume`, { method: "POST" });
}

export function stopTask(id: number, closePositions: boolean): Promise<Task> {
  return request<Task>(`/api/tasks/${id}/stop`, {
    method: "POST",
    body: JSON.stringify({ close_positions: closePositions }),
  });
}
