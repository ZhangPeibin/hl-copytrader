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
