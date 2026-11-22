export async function api(path: string, opts: RequestInit = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const text = await r.text();
  let data: unknown = text;
  try { data = JSON.parse(text); } catch { /* plain text */ }
  if (!r.ok) throw new Error((data as { detail?: string })?.detail || `请求失败 ${r.status}`);
  return data;
}
export const get = (p: string) => api(p);
export const post = (p: string, body: unknown = {}) => api(p, { method: "POST", body: JSON.stringify(body) });
export const put = (p: string, body: unknown = {}) => api(p, { method: "PUT", body: JSON.stringify(body) });
export const del = (p: string) => api(p, { method: "DELETE" });
