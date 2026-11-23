const TOKEN_KEY = "cdt-token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* 忽略 */ }
}

export type ApiError = Error & {
  status?: number;
  code?: string;
  retryable?: boolean;
};

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string> | undefined),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  let r: Response;
  try {
    r = await fetch(path, {
      ...init,
      headers,
      credentials: "same-origin",
    });
  } catch (e) {
    // E-03/E-05：网络层失败（后端没起、断网）与 401 未登录分开：
    // 网络失败不带 status，调用方显示“服务不可达”，不跳登录页。
    const err = new Error(String((e as Error).message || "网络请求失败")) as ApiError;
    err.retryable = true;
    throw err;
  }
  if (r.status === 401) {
    // 会话失效：通知 App 回到登录页
    window.dispatchEvent(new Event("cdt-unauthorized"));
  }
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    let code: string | undefined;
    try {
      const d = await r.json();
      if (d?.detail) msg = String(d.detail);
      if (typeof d?.code === "string") code = d.code;
    } catch { /* 忽略 */ }
    const e = new Error(msg) as ApiError;
    e.status = r.status;
    if (code) e.code = code;
    // 可重试：限流 / 上游失败 / 网关错误；校验与未登录不可重试。
    e.retryable = [429, 502, 503, 504].includes(r.status);
    throw e;
  }
  return r.json() as Promise<T>;
}

export const get = <T = any>(p: string) => api<T>(p);
export const post = <T = any>(p: string, body?: unknown) =>
  api<T>(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
export const put = <T = any>(p: string, body?: unknown) =>
  api<T>(p, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
export const del = <T = any>(p: string) => api<T>(p, { method: "DELETE" });
