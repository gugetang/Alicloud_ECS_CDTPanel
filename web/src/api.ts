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

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string> | undefined),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const r = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (r.status === 401) {
    // 会话失效：通知 App 回到登录页
    window.dispatchEvent(new Event("cdt-unauthorized"));
  }
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try {
      const d = await r.json();
      if (d?.detail) msg = String(d.detail);
    } catch { /* 忽略 */ }
    const e = new Error(msg) as Error & { status?: number };
    e.status = r.status;
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
