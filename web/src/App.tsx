import { useEffect, useRef, useState, type DragEvent as ReactDragEvent } from "react";
import { get, post, put, del, getToken, setToken } from "./api";
import { getLang, translate, trStatus, useI18n } from "./i18n";

type Tab = "dash" | "accounts" | "groups" | "domains" | "config";
type SubTab = "settings" | "account" | "notify" | "other" | "logs";
type Any = Record<string, any>;

// 已配置的密钥一律显示 * 掩码（密码框里显示为圆点），不显示明文；
// 掩码本身不是新密钥，保存时跳过。点进框清空以便重填，失焦留空则恢复掩码。
const SECRET_MASK = "****************";
const secretFocus = (e: { target: HTMLInputElement }) => { if (e.target.value === SECRET_MASK) e.target.value = ""; };
const secretBlurRestore = (e: { target: HTMLInputElement }, configured: boolean) => { if (!e.target.value && configured) e.target.value = SECRET_MASK; };

const TABS: [Tab, string][] = [
  ["dash", "运行概览"], ["accounts", "实例管理"],
  ["groups", "分组管理"], ["domains", "域名管理"], ["config", "设置"],
];

// 设置页内的子标签（从左到右）：全局设置、账号设置、通知设置、其它设置、日志统计
const SUB_TABS: [SubTab, string][] = [
  ["settings", "全局设置"], ["account", "账号设置"], ["notify", "通知设置"], ["other", "其它设置"], ["logs", "日志统计"],
];

const BUILTIN_REGIONS: { region_id: string; local_name: string }[] = [
  { region_id: "cn-hongkong", local_name: "中国香港" },
  { region_id: "ap-southeast-1", local_name: "新加坡" },
  { region_id: "ap-northeast-1", local_name: "东京" },
  { region_id: "us-west-1", local_name: "硅谷" },
  { region_id: "eu-central-1", local_name: "法兰克福" },
  { region_id: "cn-beijing", local_name: "北京" },
  { region_id: "cn-shanghai", local_name: "上海" },
  { region_id: "cn-shenzhen", local_name: "深圳" },
  { region_id: "cn-hangzhou", local_name: "杭州" },
];

// 时区下拉内置回退列表（与后端 GET /api/timezones 同款；接口加载失败时用它）。
const TZ_FALLBACK = [
  { value: "Asia/Shanghai", label: "上海 Asia/Shanghai" },
  { value: "Asia/Hong_Kong", label: "香港 Asia/Hong_Kong" },
  { value: "Asia/Taipei", label: "台北 Asia/Taipei" },
  { value: "Asia/Singapore", label: "新加坡 Asia/Singapore" },
  { value: "Asia/Tokyo", label: "东京 Asia/Tokyo" },
  { value: "Asia/Seoul", label: "首尔 Asia/Seoul" },
  { value: "UTC", label: "UTC 协调世界时" },
  { value: "Europe/London", label: "伦敦 Europe/London" },
  { value: "Europe/Berlin", label: "柏林 Europe/Berlin" },
  { value: "Europe/Paris", label: "巴黎 Europe/Paris" },
  { value: "America/New_York", label: "纽约 America/New_York" },
  { value: "America/Chicago", label: "芝加哥 America/Chicago" },
  { value: "America/Denver", label: "丹佛 America/Denver" },
  { value: "America/Los_Angeles", label: "洛杉矶 America/Los_Angeles" },
  { value: "Australia/Sydney", label: "悉尼 Australia/Sydney" },
];

// 按键式选择（全站统一）：选中主题色边框+浅底深色字、未选灰框，见 styles.css 的 .seg。
// value 为 "" 时表示“未选/跟随全局”，所有按钮都是灰框（分组新建时无默认选中）。
function Seg({ value, options, onChange }: {
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button key={o.value} type="button" className={value === o.value ? "seg-on" : ""} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

// 单台操作图标（内联 SVG）：刷新 / 启动 / 重启 / 停机。
function Ico({ label, children }: { label: string; children: any }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" role="img" aria-label={label}>{children}</svg>
  );
}

// 主题图标：亮色=太阳，暗色=月亮，跟随系统=显示器。
function ThemeIcon({ theme }: { theme: string }) {
  if (theme === "dark") {
    return (<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" /></svg>);
  }
  if (theme === "system") {
    return (<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="2" y="3" width="20" height="14" rx="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" /></svg>);
  }
  return (<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="4" /><line x1="12" y1="1" x2="12" y2="3" /><line x1="12" y1="21" x2="12" y2="23" /><line x1="4.22" y1="4.22" x2="5.64" y2="5.64" /><line x1="18.36" y1="18.36" x2="19.78" y2="19.78" /><line x1="1" y1="12" x2="3" y2="12" /><line x1="21" y1="12" x2="23" y2="12" /><line x1="4.22" y1="19.78" x2="5.64" y2="18.36" /><line x1="18.36" y1="5.64" x2="19.78" y2="4.22" /></svg>);
}

// 流量用量条颜色：绿 <80%、黄 ≥80%、红 ≥保护阈值，未知为灰。
const usageColor = (rate: number | null | undefined, threshold: number) =>
  rate == null ? "#9AA0A6" : rate >= threshold ? "#EA4335" : rate >= 80 ? "#FBBC04" : "#34A853";

// 流量显示保留 2 位小数；IP 只显示 A.B 段，C.D 段用 * 代替。
const fmtGb = (v: number | null | undefined) =>
  v == null ? "—" : (Math.round(v * 100) / 100).toFixed(2);
// 总额显示：整数不带小数（如 1000），非整数保留 2 位小数。
const fmtNum = (v: number) => Number.isInteger(v) ? String(v) : v.toFixed(2);
// 费用显示保留 2 位小数；未知/空值显示 —。
const fmtCost = (v: number | string | null | undefined) => {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(2) : String(v);
};
// 流量“已用 / 额度 GiB”后面加的百分比（用后端已算好的 usage_rate，2 位小数；
// 流量未知、usage_rate 为 null 时不加括号）。
const trafficPct = (a: Any) => a.usage_rate != null ? ` (${Number(a.usage_rate).toFixed(2)}%)` : "";
const maskIp = (ip: string | null | undefined) => {
  if (!ip) return "—";
  const parts = String(ip).split(".");
  return parts.length === 4 ? `${parts[0]}.${parts[1]}.*.*` : String(ip);
};

// 滑动开关（通知设置等开关类按钮统一用滑动形式）：开=蓝底白圆点，关=灰底。
function Toggle({ checked, onChange, label }: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label}
      className={checked ? "switch on" : "switch"}
      onClick={() => onChange(!checked)}>
      <span className="switch-knob" />
    </button>
  );
}

// WebAuthn（指纹登录）用的 base64url / ArrayBuffer 互转。
const b64urlToBuf = (s: string): ArrayBuffer => {
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
};
const bufToB64url = (buf: ArrayBuffer): string => {
  const bytes = new Uint8Array(buf);
  let bin = "";
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};
const credentialToJSON = (cred: any) => {
  const resp: Any = {
    clientDataJSON: bufToB64url(cred.response.clientDataJSON),
  };
  if (cred.response.attestationObject) resp.attestationObject = bufToB64url(cred.response.attestationObject);
  if (cred.response.authenticatorData) resp.authenticatorData = bufToB64url(cred.response.authenticatorData);
  if (cred.response.signature) resp.signature = bufToB64url(cred.response.signature);
  if (cred.response.userHandle) resp.userHandle = bufToB64url(cred.response.userHandle);
  if (cred.response.getTransports) resp.transports = cred.response.getTransports();
  return {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    response: resp,
    clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
  };
};

// 实例配置表单：服务器名排在最上面；分组不出现（新建默认进第一个已建分组，
// 编辑保持原分组；不要再把 1 硬编码为默认分组——库里未必有 id=1 的分组，
// 新建发 group_id=1 会直接 400“分组 1 不存在”）；必填项标签加粗（label.req）。
// 站点/阈值动作/恢复方式/日报参与都用按键式选择。
function InstanceForm({ account, settings, count, groups, onDone, onCancel, onAutoSaved }: {
  account: Any | null; settings: Any; count: number; groups: Any[];
  onDone: () => void; onCancel: () => void; onAutoSaved?: () => void;
}) {
  const { t } = useI18n();
  const isNew = !account;
  const [name, setName] = useState((account?.name as string) || "");
  const [akId, setAkId] = useState((account?.access_key_id as string) || "");
  const [akSecret, setAkSecret] = useState(account?.has_secret ? SECRET_MASK : "");
  const [instanceId, setInstanceId] = useState((account?.instance_id as string) || "");
  const [site, setSite] = useState((account?.site as string) || "global");
  const [region, setRegion] = useState((account?.region as string) || "cn-hongkong");
  const [regionCustom, setRegionCustom] = useState(false);
  const [regionCustomVal, setRegionCustomVal] = useState("");
  const [quota, setQuota] = useState(String(account?.quota_gb ?? 200));
  const [schedEnabled, setSchedEnabled] = useState(Boolean(account?.daily_schedule_enabled));
  const [onTime, setOnTime] = useState((account?.daily_on_time as string) || "08:00");
  const [offTime, setOffTime] = useState((account?.daily_off_time as string) || "20:00");
  const [regions, setRegions] = useState<{ region_id: string; local_name: string }[]>(BUILTIN_REGIONS);
  const [ownedIds, setOwnedIds] = useState<Set<string>>(new Set());
  const [formMsg, setFormMsg] = useState("");
  const [formBusy, setFormBusy] = useState(false);

  const loadRegions = async (withAk: boolean) => {
    // 优先调发现接口（已有实例所在地域分组 + 全部可用地域）；失败回退 POST 列表，再失败用内置列表
    try {
      const body: Any = { site };
      if (account?.id) body.account_id = account.id;
      else if (withAk && akId && akSecret) { body.access_key_id = akId; body.access_key_secret = akSecret; }
      else throw new Error("no-credentials");
      const r = await post("/api/aliyun/discover-regions", body) as Any;
      const all = (r.all as Any[]) || [];
      const owned = (r.owned as Any[]) || [];
      setRegions((all.length ? all : BUILTIN_REGIONS) as { region_id: string; local_name: string }[]);
      setOwnedIds(new Set(owned.map((x: Any) => x.region_id)));
    } catch {
      try {
        // 回退接口同样走 POST + JSON body：临时 AK 不再放 URL 查询参数里（会进访问日志）
        const body: Any = { site };
        if (account?.id) body.account_id = account.id;
        else if (akId && akSecret) { body.access_key_id = akId; body.access_key_secret = akSecret; }
        else throw new Error("no-credentials");
        const r = await post("/api/aliyun/regions", body) as Any;
        setRegions(((r.regions as Any[]) || BUILTIN_REGIONS) as { region_id: string; local_name: string }[]);
        setOwnedIds(new Set());
      } catch {
        setRegions(BUILTIN_REGIONS);
        setOwnedIds(new Set());
      }
    }
  };

  useEffect(() => { loadRegions(false); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [site, account?.id]);

  const autoDiscover = async () => {
    if (!instanceId.trim()) { setFormMsg(t("请先填写实例 ID")); return; }
    setFormBusy(true); setFormMsg("");
    try {
      const body: Any = { instance_id: instanceId.trim(), site };
      if (account?.id) body.account_id = account.id;
      else if (akId && akSecret) { body.access_key_id = akId; body.access_key_secret = akSecret; }
      else throw new Error(t("请先填写 AccessKey ID 与 Secret，或先保存账户"));
      const r = await post("/api/aliyun/find-instance-region", body) as Any;
      setRegion(r.region_id as string);
      setRegionCustom(false);
      setFormMsg(t("已发现实例所在地域：{local}（{region}）", { local: t(r.local_name as string), region: r.region_id }));
    } catch (e) { setFormMsg(String((e as Error).message)); }
    finally { setFormBusy(false); }
  };

  // 实例表单自动保存：改完失焦即存（300ms 防抖），不用点保存。没填完不存、
  // 不报错；编辑已有账户存完不关闭表单；新建账户填完必填项后失焦即创建并关闭。
  const autoTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const savingRef = useRef(false);
  useEffect(() => () => { if (autoTimer.current) clearTimeout(autoTimer.current); }, []);
  const saveAutoNow = async () => {
    if (savingRef.current) { autoTimer.current = setTimeout(() => { void saveAutoNow(); }, 300); return; }
    if (!name.trim() || !akId.trim() || !instanceId.trim()) return;
    const q = Number(quota);
    if (!quota || !(q > 0)) return;
    if (schedEnabled && onTime === offTime) return;
    const finalRegion = regionCustom ? regionCustomVal.trim() : region;
    if (!finalRegion) return;
    savingRef.current = true;
    const payload: Any = {
      name: name.trim(), access_key_id: akId.trim(), instance_id: instanceId.trim(),
      site, region: finalRegion, quota_gb: q,
      // 新建默认进第一个已建分组（不是硬编码 1）；编辑保持原分组。
      // 后端 create_account 对不存在的分组也会再兜底一次。
      group_id: account?.group_id ?? (groups[0]?.id ?? 1),
      daily_schedule_enabled: schedEnabled ? 1 : 0, daily_on_time: onTime,
      daily_off_time: offTime,
      // 阈值动作/恢复已移到分组卡片：实例级不再保存这两个字段。
      // 参与日报推送已取消：实例级不再保存，全部跟随全局开关。
      // 实例级刷新时间已取消：强制跟随全局，存 null 清掉旧的自定义值。
      api_refresh_minutes: null,
    };
    if (akSecret && akSecret !== SECRET_MASK) payload.access_key_secret = akSecret;  // 掩码不是新密钥，留空/掩码都保持不变，绝不清空
    try {
      if (isNew) { payload.priority = count; await post("/api/accounts", payload); onDone(); }
      else { await put(`/api/accounts/${account.id}`, payload); onAutoSaved?.(); }
    } catch (e) {
      // 自动保存失败要让用户看见（之前静默吞掉，400“分组不存在”这类错
      // 表单看着像没反应，用户无从得知）。后端 detail 已是中文提示。
      setFormMsg(String((e as Error).message || t("保存失败")));
    }
    finally { savingRef.current = false; }
  };
  const saveAuto = () => {
    if (autoTimer.current) clearTimeout(autoTimer.current);
    autoTimer.current = setTimeout(() => { void saveAutoNow(); }, 300);
  };

  const regionKnown = regions.some((r) => r.region_id === region);
  const ownedOptions = regions.filter((r) => ownedIds.has(r.region_id));
  const allOptions = regions.filter((r) => !ownedIds.has(r.region_id));

  return (
    <div className="instance-form">
      <div className="form2">
        <div><label className="req">{t("服务器名 *")}</label><input value={name} onChange={(e) => setName(e.target.value)} onBlur={saveAuto} placeholder={t("如 AliCloud-HK-1")} /></div>
        <div><label className="req">{t("实例 ID *")}</label><input value={instanceId} onChange={(e) => setInstanceId(e.target.value)} onBlur={saveAuto} placeholder="i-..." /></div>
        <div><label className="req">{t("AccessKey ID *")}</label><input value={akId} onChange={(e) => setAkId(e.target.value)} onBlur={saveAuto} placeholder="LTAI..." /></div>
        <div><label className="req">{t("AccessKey Secret *")}</label><input type="password" value={akSecret} onChange={(e) => setAkSecret(e.target.value)} onFocus={() => { if (akSecret === SECRET_MASK) setAkSecret(""); }} onBlur={() => { if (!akSecret && account?.has_secret) setAkSecret(SECRET_MASK); saveAuto(); }} placeholder={account?.has_secret ? t("已配置（留空保持不变）") : t("未配置，请填写")} /></div>
        <div onBlur={saveAuto}><label className="req">{t("站点类型 *")}</label>
          <Seg value={site} options={[
            { value: "global", label: t("国际站（USD）") },
            { value: "cn", label: t("国内站（CNY）") },
          ]} onChange={setSite} /></div>
        <div><label className="req">{t("地域 *")}</label>
          <select value={regionCustom ? "__custom" : region} onBlur={saveAuto} onChange={(e) => {
            if (e.target.value === "__custom") { setRegionCustom(true); setRegionCustomVal(region); }
            else { setRegionCustom(false); setRegion(e.target.value); }
          }}>
            {ownedOptions.length > 0 && (
              <optgroup label={t("已有实例所在地域")}>
                {ownedOptions.map((r) => <option key={r.region_id} value={r.region_id}>{t(r.local_name)}（{r.region_id}）</option>)}
              </optgroup>
            )}
            <optgroup label={t("全部可用地域")}>
              {allOptions.map((r) => <option key={r.region_id} value={r.region_id}>{t(r.local_name)}（{r.region_id}）</option>)}
            </optgroup>
            {!regionKnown && !regionCustom && <option value={region}>{region}{t("（当前值）")}</option>}
            <option value="__custom">{t("自定义…")}</option>
          </select>
          {regionCustom && <input value={regionCustomVal} onChange={(e) => setRegionCustomVal(e.target.value)} onBlur={saveAuto} placeholder={t("地域 ID，如 cn-hongkong")} style={{ marginTop: 6 }} />}
          <div className="row">
            <button className="btn ghost" onClick={autoDiscover} disabled={formBusy}>{t("自动发现")}</button>
            <button className="btn ghost" onClick={() => loadRegions(true)} disabled={formBusy}>{t("重新拉取地域列表")}</button>
          </div></div>
        <div><label className="req">{t("月度流量额度 *（GiB）")}</label><input type="number" min={1} value={quota} onChange={(e) => setQuota(e.target.value)} onBlur={saveAuto} /></div>
      </div>
      <div style={{ marginTop: 12 }}>
        <label className="check" onBlur={saveAuto}><input type="checkbox" checked={schedEnabled} onChange={(e) => setSchedEnabled(e.target.checked)} />{t("每日定时开关机")}</label>
        {schedEnabled && (
          <div className="form2">
            <div><label>{t("每日开机时间")}</label><input type="time" value={onTime} onChange={(e) => setOnTime(e.target.value)} onBlur={saveAuto} /></div>
            <div><label>{t("每日关机时间")}</label><input type="time" value={offTime} onChange={(e) => setOffTime(e.target.value)} onBlur={saveAuto} /></div>
          </div>
        )}
      </div>
      {formMsg && <p style={{ color: "#f87171", marginTop: 12 }}>{formMsg}</p>}
      <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
        <button className="btn ghost" onClick={onCancel}>{t("取消")}</button>
      </div>
    </div>
  );
}

// ---------------- 两行标签：第一行父项，第二行子项缩进 2 字符 ----------------
function TwoLineLabel({ parent, child, req }: { parent: string; child: string; req?: boolean }) {
  const { t } = useI18n();
  return (
    <label className={req ? "req" : "lbl"}><span style={{ fontSize: 16 }}>{t(parent)}</span><br /><span style={{ paddingLeft: "2em" }}>{t(child)}</span></label>
  );
}

// ---------------- 密码输入框：带显示/隐藏切换（眼睛图标） ----------------
function PasswordField({ value, onChange, placeholder, onKeyDown }: { value: string; onChange: (v: string) => void; placeholder?: string; onKeyDown?: (e: React.KeyboardEvent) => void }) {
  const { t } = useI18n();
  const [show, setShow] = useState(false);
  return (
    <div style={{ position: "relative" }}>
      <input type={show ? "text" : "password"} value={value} onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder} onKeyDown={onKeyDown} style={{ paddingRight: 40 }} />
      <button type="button" aria-label={show ? t("隐藏密码") : t("显示密码")} title={show ? t("隐藏密码") : t("显示密码")}
        onClick={() => setShow(!show)}
        style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", background: "transparent", border: "none", cursor: "pointer", color: "var(--muted)", padding: 4, display: "flex", alignItems: "center" }}>
        {show ? (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" /><line x1="1" y1="1" x2="23" y2="23" /></svg>
        ) : (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>
        )}
      </button>
    </div>
  );
}

// ---------------- 首次登录：填写管理员信息 ----------------
function SetupScreen({ onDone, onSkip }: { onDone: () => void; onSkip: () => void }) {
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [msg, setMsg] = useState("");
  const submit = async () => {
    if (pw.length < 6) { setMsg(t("密码至少 6 位")); return; }
    if (pw !== pw2) { setMsg(t("两次密码不一致")); return; }
    try {
      const r = await post("/api/auth/setup", { admin_name: name.trim(), password: pw }) as Any;
      setToken(r.token as string);
      onDone();
    } catch (e) { setMsg(String((e as Error).message)); }
  };
  return (
    <div className="auth-wrap">
      <div className="card">
        <div className="auth-title">{t("欢迎使用 CDT 面板")}</div>
        <label className="req">{t("管理员名称 *")}</label>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("如 管理员")} />
        <label className="req">{t("登录密码 *")}</label>
        <PasswordField value={pw} onChange={setPw} placeholder={t("至少 6 位")} />
        <label className="req">{t("确认密码 *")}</label>
        <PasswordField value={pw2} onChange={setPw2} placeholder={t("再输一次")} />
        {msg && <p style={{ color: "#f87171", marginTop: 12 }}>{msg}</p>}
        <button className="btn" style={{ width: "100%", marginTop: 16 }} onClick={submit}>{t("创建管理员并进入")}</button>
        <button className="btn ghost" style={{ width: "100%", marginTop: 8 }} onClick={onSkip}>{t("稍后设置，直接进入")}</button>
      </div>
    </div>
  );
}

// ---------------- 登录页：密码 / 指纹 / Google / GitHub ----------------
function LoginScreen({ info, oauthMsg, onDone }: { info: Any; oauthMsg: string; onDone: () => void }) {
  const { t } = useI18n();
  const [pw, setPw] = useState("");
  const [msg, setMsg] = useState(oauthMsg || "");
  const [busy, setBusy] = useState(false);
  const webauthnSupported = typeof window !== "undefined" && !!window.PublicKeyCredential;

  const loginPw = async () => {
    if (!pw) { setMsg(t("请输入密码")); return; }
    setBusy(true); setMsg("");
    try {
      const r = await post("/api/auth/login", { password: pw }) as Any;
      setToken(r.token as string);
      onDone();
    } catch (e) { setMsg(String((e as Error).message)); }
    finally { setBusy(false); }
  };

  const loginWebauthn = async () => {
    if (!webauthnSupported) { setMsg(t("当前浏览器不支持指纹登录")); return; }
    setBusy(true); setMsg("");
    try {
      const opts = await post("/api/auth/webauthn/authenticate/begin", {}) as Any;
      const pk: Any = {
        ...opts,
        challenge: b64urlToBuf(opts.challenge),
        allowCredentials: (opts.allowCredentials || []).map((c: Any) => ({ ...c, id: b64urlToBuf(c.id) })),
      };
      const cred = await navigator.credentials.get({ publicKey: pk as PublicKeyCredentialRequestOptions }) as any;
      if (!cred) throw new Error(t("未完成指纹验证"));
      const r = await post("/api/auth/webauthn/authenticate/finish", { credential: credentialToJSON(cred) }) as Any;
      setToken(r.token as string);
      onDone();
    } catch (e) { setMsg(String((e as Error).message)); }
    finally { setBusy(false); }
  };

  return (
    <div className="auth-wrap">
      <div className="card">
        <div className="auth-title">{t("登录 CDT 面板")}</div>
        <label className="req">{t("登录密码 *")}</label>
        <PasswordField value={pw} onChange={setPw} placeholder={t("管理员密码")}
          onKeyDown={(e) => { if (e.key === "Enter") loginPw(); }} />
        {msg && <p style={{ color: "#f87171", marginTop: 12 }}>{msg}</p>}
        <button className="btn" style={{ width: "100%", marginTop: 16 }} onClick={loginPw} disabled={busy}>{t("登录")}</button>
        {info.webauthn && (
          <button className="oauth-btn" onClick={loginWebauthn} disabled={busy || !webauthnSupported}>
            {t("指纹登录")}{webauthnSupported ? "" : t("（当前浏览器不支持）")}
          </button>
        )}
        <button className="oauth-btn" disabled={!info.oauth?.google}
          onClick={() => { window.location.href = "/api/auth/oauth/google/login"; }}>
          <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.611 20.083H42V20H24v8h11.303C33.654 32.657 29.223 36 24 36c-6.627 0-12-5.373-12-12s5.373-12 12-12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 12.955 4 4 12.955 4 24s8.955 20 20 20 20-8.955 20-20c0-1.341-.138-2.65-.389-3.917z"/><path fill="#FF3D00" d="M6.306 14.691l6.571 4.819C14.655 15.108 18.961 12 24 12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 16.318 4 9.656 8.337 6.306 14.691z"/><path fill="#4CAF50" d="M24 44c5.166 0 9.86-1.977 13.409-5.192l-6.19-5.238C29.211 35.091 26.715 36 24 36c-5.202 0-9.619-3.317-11.283-7.946l-6.522 5.025C9.505 39.556 16.227 44 24 44z"/><path fill="#1976D2" d="M43.611 20.083H42V20H24v8h11.303c-.792 2.237-2.231 4.166-4.087 5.571.001-.001.002-.001.003-.002l6.19 5.238C36.971 39.205 44 34 44 24c0-1.341-.138-2.65-.389-3.917z"/></svg>
          {t("使用 Google 登录")}
        </button>
        <button className="oauth-btn" disabled={!info.oauth?.github}
          onClick={() => { window.location.href = "/api/auth/oauth/github/login"; }}>
          <svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>
          {t("使用 GitHub 登录")}
        </button>
      </div>
    </div>
  );
}

// ---------------- 分组卡片：模式按键 + 参数表单（无 prompt） ----------------
function GroupCard({ g, settings, accounts, domains, dotEl, run, setMsg, dragProps: gDragProps, dragOver, dragging }: {
  g: Any; settings: Any; accounts: Any[]; domains: Any[];
  dotEl: (dot: string | undefined, title?: string) => JSX.Element;
  run: (fn: () => Promise<unknown>, ok: string) => Promise<void>;
  setMsg: (s: string) => void;
  dragProps?: Any; dragOver?: boolean; dragging?: boolean;
}) {
  const { lang, t } = useI18n();
  const [gName, setGName] = useState(g.name as string);
  // 自定义间隔：后端存的一律是分钟；自定义分钟数 >0 时间隔选“自定义”（值 0）
  const _gcm = Number(g.custom_interval_minutes ?? 0) || 0;
  const _gcu = _gcm > 0 && _gcm % 1440 === 0 ? "day" : _gcm > 0 && _gcm % 60 === 0 ? "hour" : "minute";
  const [interval, setInterval] = useState(_gcm > 0 ? "0" : String(g.interval_hours ?? settings.interval_hours ?? 6));
  const [custUnit, setCustUnit] = useState(_gcu);
  const [custVal, setCustVal] = useState(String(_gcu === "day" ? _gcm / 1440 : _gcu === "hour" ? _gcm / 60 : _gcm));
  const [startTime, setStartTime] = useState((g.interval_start_time as string) || "00:00:00");
  const [diffMode, setDiffMode] = useState((g.switch_diff_mode ?? settings.switch_diff_mode ?? "percent") as string);
  const [diffPct, setDiffPct] = useState(String(g.switch_diff_pct ?? settings.switch_diff_pct ?? 10));
  const [diffGb, setDiffGb] = useState(String(g.switch_diff_gb ?? settings.switch_diff_gb ?? 10));
  // 动态切换间隔（分钟）：后端存分钟；30/60/90/120 为预设，0=自定义；未填默认 30
  const _gdm = Number(g.dynamic_interval_minutes ?? 0) || 0;
  const [dynInterval, setDynInterval] = useState(_gdm > 0 && [30, 60, 90, 120].includes(_gdm) ? String(_gdm) : _gdm > 0 ? "0" : "30");
  const [dynCustVal, setDynCustVal] = useState(String(_gdm > 0 && ![30, 60, 90, 120].includes(_gdm) ? _gdm : 30));
  // 分组级阈值策略：空=跟随全局
  const [gExcl, setGExcl] = useState((g.exclusion_action as string) ?? "");
  const [gRecovery, setGRecovery] = useState((g.recovery_action as string) ?? "");
  // 分组设置区是否展开（卡片右上角 ⚙ 开关，默认展开）
  const [showSettings, setShowSettings] = useState(false);

  const dom = domains.find((d) => d.group_id === g.id);

  // 分组参数自动保存：改动即存，不用点保存
  const savePatch = (patch: Any, okMsg?: string) => run(() => put(`/api/groups/${g.id}`, patch), okMsg || t("已保存成功"));
  // 自定义间隔单位换算：后端存的一律是分钟；分钟 1 / 小时 60 / 天 1440
  const custMult = (u: string) => (u === "day" ? 1440 : u === "hour" ? 60 : 1);
  const fmtCust = (v: number) => String(Number.isInteger(v) ? v : Math.round(v * 100) / 100);
  const changeCustUnit = (u: string) => {
    const mins = (parseFloat(custVal) || 0) * custMult(custUnit);
    setCustUnit(u);
    setCustVal(fmtCust(mins / custMult(u)));
  };

  return (
    <div className="card" style={{ marginBottom: 12, cursor: showSettings ? "default" : "grab", ...(dragOver ? { outline: "2px solid var(--accent)", outlineOffset: 2 } : {}), ...(dragging ? { opacity: 0.5 } : {}) }} {...(showSettings ? {} : (gDragProps || {}))}>
      <div className="row"><b>{g.name as string}</b>
        <span className="pill">{g.switch_mode === "time" ? t("定时轮巡") : g.switch_mode === "dynamic" ? t("动态切换") : t("负载均衡")}</span>
        <span className="spacer" />
        <button className="btn ghost" title={t("分组设置")} onClick={() => setShowSettings(!showSettings)}>⚙️</button>
        <button className="icon-btn danger" title={t("删除")} onClick={() => { if (confirm(t("删除分组 {name}？须先移走其账户/域名。", { name: g.name }))) run(() => del(`/api/groups/${g.id}`), t("已删除")); }}>
          <Ico label={t("删除")}><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /><line x1="10" y1="11" x2="10" y2="17" /><line x1="14" y1="11" x2="14" y2="17" /></Ico>
        </button>
      </div>
      {showSettings && (<>
      <div style={{ marginTop: 12 }}>
        <label className="req">{t("分组名称 *")}</label>
        <input value={gName} onChange={(e) => setGName(e.target.value)} onBlur={() => {
          const nm = gName.trim();
          if (!nm) { setGName(g.name as string); setMsg(t("请填写分组名")); return; }
          if (nm === g.name) return;
          savePatch({ name: nm }, t("已保存成功"));
        }} />
      </div>
      <div style={{ marginTop: 12 }}>
        <label className="lbl">{t("切换模式")}</label>
        <Seg value={g.switch_mode as string} options={[
          { value: "time", label: t("定时轮巡") },
          { value: "load_balance", label: t("负载均衡") },
          { value: "dynamic", label: t("动态切换") },
        ]} onChange={(m) => run(() => put(`/api/groups/${g.id}`, { switch_mode: m }), t("已切换模式"))} />
      </div>
      {g.switch_mode === "dynamic" ? (
        <div style={{ marginTop: 12 }}>
          <div className="form2">
            <div><label className="lbl" style={{ paddingLeft: "2em" }}>{t("动态切换间隔")}</label>
              <div style={{ paddingLeft: "2em" }}><Seg value={dynInterval} options={[
                { value: "30", label: t("30 分钟") }, { value: "60", label: t("60 分钟") },
                { value: "90", label: t("90 分钟") }, { value: "120", label: t("120 分钟") },
                { value: "0", label: t("自定义") },
              ]} onChange={(v) => { setDynInterval(v); savePatch({ dynamic_interval_minutes: v === "0" ? Math.max(30, Number(dynCustVal) || 30) : Number(v) }); }} /></div></div>
          </div>
          {dynInterval === "0" && (
            <div style={{ marginTop: 12 }} onBlur={() => savePatch({ dynamic_interval_minutes: Math.max(30, Number(dynCustVal) || 30) })}><label className="lbl" style={{ paddingLeft: "2em" }}>{t("自定义间隔（分钟，最少 30）")}</label>
              <div style={{ paddingLeft: "2em" }}><input type="number" min={30} value={dynCustVal} onChange={(e) => setDynCustVal(e.target.value)} style={{ width: 120 }} /></div></div>)}
          <div style={{ color: "var(--accent)", fontSize: 12, marginTop: 6, paddingLeft: "2em" }}>{t("每到间隔切到剩余流量最多的服务器，不看流量差值。")}</div>
        </div>
      ) : g.switch_mode === "time" ? (
        <div style={{ marginTop: 12 }}>
          <div className="form2">
            <div><label className="lbl" style={{ paddingLeft: "2em" }}>{t("间隔切换周期")}</label>
              <div style={{ paddingLeft: "2em" }}><Seg value={interval} options={[
                { value: "1", label: t("1 小时") }, { value: "2", label: t("2 小时") },
                { value: "3", label: t("3 小时") }, { value: "4", label: t("4 小时") },
                { value: "6", label: t("6 小时") }, { value: "12", label: t("12 小时") },
                { value: "24", label: t("24 小时") }, { value: "0", label: t("自定义") },
              ]} onChange={(v) => { setInterval(v); savePatch(v === "0" ? { interval_hours: 6, custom_interval_minutes: Math.round((parseFloat(custVal) || 0) * custMult(custUnit)) } : { interval_hours: Number(v) || 6, custom_interval_minutes: 0 }); }} /></div></div>
          </div>
          {interval === "0" && (
            <div style={{ marginTop: 12 }} onBlur={() => savePatch({ custom_interval_minutes: Math.round((parseFloat(custVal) || 0) * custMult(custUnit)) })}><label className="lbl" style={{ paddingLeft: "2em" }}>{t("自定义间隔")}</label>
              <div style={{ paddingLeft: "2em" }}><div className="row" style={{ alignItems: "center" }}>
                <input type="number" min={0} value={custVal} onChange={(e) => setCustVal(e.target.value)} style={{ width: 120 }} />
                <Seg value={custUnit} options={[
                  { value: "minute", label: t("分钟") },
                  { value: "hour", label: t("小时") },
                  { value: "day", label: t("天") },
                ]} onChange={changeCustUnit} />
              </div></div></div>)}
          <div style={{ marginTop: 12 }}>
            <label className="lbl" style={{ paddingLeft: "2em" }}>{t("开始时间")}</label>
            <div style={{ paddingLeft: "2em" }}><input type="time" step="1" value={startTime} onChange={(e) => setStartTime(e.target.value)} onBlur={() => savePatch({ interval_start_time: startTime || "00:00:00" })} /></div>
          </div>
        </div>
      ) : (
        <div style={{ marginTop: 12 }}>
          <TwoLineLabel parent="负载均衡" child="切换阀值" />
          <div style={{ paddingLeft: "2em" }}><Seg value={diffMode} options={[
            { value: "percent", label: t("百分比（%）") },
            { value: "gb", label: t("固定流量（GB）") },
          ]} onChange={(m) => { setDiffMode(m); savePatch({ switch_diff_mode: m }); }} /></div>
          <div className="form2">
            {diffMode === "percent" ? (
              <div><TwoLineLabel parent="负载均衡" child="切换阀值（%）" /><div style={{ paddingLeft: "2em" }}><input type="number" min={1} value={diffPct} onChange={(e) => setDiffPct(e.target.value)} onBlur={() => savePatch({ switch_diff_pct: Number(diffPct) || 10 })} /></div></div>
            ) : (
              <div><TwoLineLabel parent="负载均衡" child="切换阀值（GB）" /><div style={{ paddingLeft: "2em" }}><input type="number" min={1} value={diffGb} onChange={(e) => setDiffGb(e.target.value)} onBlur={() => savePatch({ switch_diff_gb: Number(diffGb) || 10 })} /></div></div>
            )}
          </div>
          <div style={{ color: "var(--accent)", fontSize: 12, marginTop: 6, paddingLeft: "2em" }}>{t("不适用定时轮巡。")}</div>
        </div>
      )}
      <div style={{ marginTop: 12 }}>
        <label className="lbl">{t("阈值动作")}</label>
        <Seg value={gExcl} options={[
          { value: "", label: t("跟随全局") },
          { value: "none", label: t("踢出DDNS") },
          { value: "stop_charging", label: t("节省停机") },
          { value: "stop", label: t("停机") },
        ]} onChange={(v) => { setGExcl(v); savePatch({ exclusion_action: v }); }} />
      </div>
      <div style={{ marginTop: 12 }}>
        <label className="lbl">{t("阈值恢复")}</label>
        <Seg value={gRecovery} options={[
          { value: "", label: t("跟随全局") },
          { value: "start", label: t("恢复时拉起") },
          { value: "keep_stopped", label: t("恢复入池但保持停机") },
        ]} onChange={(v) => { setGRecovery(v); savePatch({ recovery_action: v }); }} />
      </div>
      </>)}
      <h3 style={{ marginTop: 12 }}>{t("选择成员实例")}</h3>
      <div className="seg" style={{ paddingLeft: "2em" }}>
        {accounts.map((a) => {
          // 多对多：同一台实例可同时选进多个分组；选中=属于本分组，参与本组 DDNS
          const gids = ((a.group_ids as number[] | undefined) ?? ([a.group_id] as number[])).map(Number);
          const on = gids.includes(g.id as number);
          const next = on ? gids.filter((x) => x !== g.id) : [...gids, g.id as number];
          return (
            <button key={a.id as number} className={(on ? "seg-on " : "") + "member-btn"}
              title={`${a.status_label ? trStatus(a.status_label, lang) : (a.instance_status || t("未知"))}${a.public_ip ? " · " + (a.public_ip as string) : ""}${a.usage_rate != null ? " · " + a.usage_rate + "%" : ""}`}
              onClick={() => run(() => put(`/api/accounts/${a.id}`, { group_ids: next, ...(on ? {} : { ddns_enabled: 1 }) }), on ? t("已从分组移除 {name}", { name: a.name }) : t("{name} 已加入分组", { name: a.name }))}>
              <span className="member-top">{dotEl(a.status_dot, a.status_detail || a.status_label || "")}{a.name as string}</span>
              <span className="member-ip">{maskIp(a.public_ip)}</span>
            </button>
          );
        })}
      </div>
      <h3 style={{ marginTop: 12 }}>{t("域名选择")}</h3>
      <div className="seg" style={{ paddingLeft: "2em" }}>
        {domains.map((d) => {
          const on = dom && (d.id as number) === (dom.id as number);
          return (
            <button key={d.id as number} className={on ? "seg-on " : ""}
              title={(d.current_ip as string) || t("未分配")}
              onClick={() => run(() => post(`/api/groups/${g.id}/bind-domain`, { domain_id: d.id }), t("已选择域名 {name}", { name: d.name }))}>
              {d.name as string}
            </button>
          );
        })}
      </div>
      <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
        <button className={`icon-btn ${g.paused ? "green" : "gray"}`} title={g.paused ? t("启动分组") : t("停止分组")}
          onClick={() => run(() => put(`/api/groups/${g.id}`, { paused: g.paused ? 0 : 1 }), g.paused ? t("分组已启动") : t("分组已停止"))}>
          {g.paused
            ? <Ico label={t("启动")}><polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none" /></Ico>
            : <Ico label={t("停止")}><rect x="6" y="6" width="12" height="12" fill="currentColor" stroke="none" /></Ico>}
        </button>
      </div>
    </div>
  );
}

// ---------------- 通知渠道卡片：Email / Telegram 各一个框体 ----------------
// 左上角依次列排列启用按键（选中蓝、未选灰）；每日财报设置与测试都放在框体内；
// 发送测试按键在框体右下角。
function ChannelCard({ kind, settings, drEnabled, drTime, setDrEnabled, setDrTime, saveSettings, run }: {
  kind: "email" | "telegram";
  settings: Any; drEnabled: boolean; drTime: string;
  setDrEnabled: (v: boolean) => void; setDrTime: (v: string) => void;
  saveSettings: (patch: Any) => Promise<void>;
  run: (fn: () => Promise<unknown>, ok: string) => Promise<void>;
}) {
  const { t } = useI18n();
  const isEmail = kind === "email";
  const enabled = (isEmail ? settings.email_enabled : settings.telegram_enabled) === "1";
  const title = isEmail ? "E-Mail" : "Telegram";
  const saveConfig = () => {
    if (isEmail) {
      const emPass = (document.getElementById("em-pass") as HTMLInputElement)?.value || "";
      return saveSettings({
        email_smtp_host: (document.getElementById("em-host") as HTMLInputElement)?.value ?? "",
        email_smtp_port: (document.getElementById("em-port") as HTMLInputElement)?.value ?? "465",
        email_user: (document.getElementById("em-user") as HTMLInputElement)?.value ?? "",
        email_to: (document.getElementById("em-to") as HTMLInputElement)?.value ?? "",
        ...(emPass && emPass !== SECRET_MASK ? { email_password: emPass } : {}),
      });
    } else {
      const tgTok = (document.getElementById("tg-token") as HTMLInputElement)?.value || "";
      return saveSettings({
        telegram_chat_id: (document.getElementById("tg-chat") as HTMLInputElement)?.value ?? "",
        ...(tgTok && tgTok !== SECRET_MASK ? { telegram_token: tgTok } : {}),
      });
    }
  };
  // 通知渠道自动保存：配置改完失焦即存，财报开关/时间改完即存
  const saveAll = async () => {
    await saveConfig();
    await saveSettings({ daily_report_enabled: drEnabled ? "1" : "0", daily_report_time: drTime || "00:00" });
  };
  // 测试发送：后端返回 sent（实际发出的渠道）与 errors（失败原因）；
  // 该渠道没发出去时抛错，让外层的 run 显示真实原因，而不是“已发送”。
  const testSend = async (url: string, body: Any) => {
    const res: Any = await post(url, body);
    const sent: string[] = res?.sent || [];
    if (!sent.includes(kind)) {
      const err = res?.errors?.[kind];
      throw new Error(err || t("发送失败：该渠道未启用或未配置"));
    }
    return res;
  };
  return (
    <div className="card">
      <div className="row"><h3 style={{ margin: 0 }}>{title}</h3><span className="spacer" />
        <Toggle checked={enabled} label={t("{title}启用", { title })}
          onChange={(v) => saveSettings(isEmail ? { email_enabled: v ? "1" : "0" } : { telegram_enabled: v ? "1" : "0" })} />
      </div>
      {isEmail ? (
        <div style={{ marginTop: 12, paddingLeft: "2em" }}>
          <label>{t("SMTP 服务器")}</label><input id="em-host" defaultValue={String(settings.email_smtp_host ?? "")} placeholder="smtp.example.com" onBlur={saveAll} />
          <label>{t("SMTP 端口")}</label><input id="em-port" defaultValue={String(settings.email_smtp_port ?? 465)} onBlur={saveAll} />
          <label>{t("发件邮箱")}</label><input id="em-user" defaultValue={String(settings.email_user ?? "")} placeholder="sender@example.com" onBlur={saveAll} />
          <label>{t("SMTP 密码")}</label><input type="password" id="em-pass" key={String(settings.email_configured)} defaultValue={settings.email_configured ? SECRET_MASK : ""} placeholder={t("留空保持不变")} onFocus={secretFocus} onBlur={(e) => { secretBlurRestore(e, Boolean(settings.email_configured)); saveAll(); }} />
          <label>{t("收件邮箱")}</label><input id="em-to" defaultValue={String(settings.email_to ?? "")} placeholder="receiver@example.com" onBlur={saveAll} />
        </div>
      ) : (
        <div style={{ marginTop: 12, paddingLeft: "2em" }}>
          <label>Bot Token</label><input type="password" id="tg-token" key={String(settings.telegram_configured)} defaultValue={settings.telegram_configured ? SECRET_MASK : ""} placeholder={t("留空保持不变")} onFocus={secretFocus} onBlur={(e) => { secretBlurRestore(e, Boolean(settings.telegram_configured)); saveAll(); }} />
          <label>Chat ID</label><input id="tg-chat" defaultValue={String(settings.telegram_chat_id ?? "")} placeholder={t("如 123456789")} onBlur={saveAll} />
        </div>
      )}
      <div style={{ marginTop: 12 }}>
        <h3 style={{ margin: 0 }}>{t("每日财报推送")}</h3>
        <div style={{ paddingLeft: "2em" }}>
        <div className="row"><span>{t("启用每日财报推送")}</span><span className="spacer" />
          <Toggle checked={drEnabled} label={t("启用每日财报推送")} onChange={(v) => { setDrEnabled(v); saveSettings({ daily_report_enabled: v ? "1" : "0", daily_report_time: drTime || "00:00" }); }} /></div>
        <label>{t("推送时间")}</label><input type="time" value={drTime} onChange={(e) => setDrTime(e.target.value)} onBlur={saveAll} />
        </div>
      </div>
      <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
        {!isEmail && (
          <button className="btn ghost" onClick={() => run(async () => {
            const res: Any = await get("/api/notify/telegram-check");
            const steps: Any[] = res?.steps || [];
            const bad = steps.find((s: Any) => !s.ok);
            if (bad) throw new Error(`${bad.name}：${bad.detail}`);
            return res;
          }, t("Telegram 连接正常"))}>{t("检测连接")}</button>
        )}
        <button className="btn ghost" onClick={() => run(() => testSend("/api/notify/test", { event: "error", channel: kind }), t("测试通知已发送到{title}", { title }))}>{t("发送测试通知")}</button>
        <button className="btn ghost" onClick={() => run(() => testSend("/api/notify/test-daily", { channel: kind }), t("测试财报已发送到{title}", { title }))}>{t("发送测试财报")}</button>
        <button className="btn" onClick={() => run(() => testSend("/api/notify/send-daily", { channel: kind }), t("日报已手动推送到{title}", { title }))}>{t("立即推送日报")}</button>
      </div>
    </div>
  );
}

// ---------------- 登录与安全卡片（全局设置页）：改密码 / 注册指纹 / OAuth 配置 ----------------
function LoginSecurity({ saveSettings, settings, setMsg }: {
  settings: Any;
  saveSettings: (patch: Any) => Promise<void>;
  setMsg: (s: string) => void;
}) {
  const { lang, t } = useI18n();
  const [curPw, setCurPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [creds, setCreds] = useState<Any[]>([]);
  const [busy, setBusy] = useState(false);
  const [secMsg, setSecMsg] = useState("");
  const loadCreds = async () => {
    try { setCreds(await get("/api/auth/webauthn/list") as Any[]); } catch { setCreds([]); }
  };
  useEffect(() => { loadCreds(); }, []);

  const changePw = async () => {
    if (newPw.length < 6) { setSecMsg(t("新密码至少 6 位")); return; }
    setBusy(true); setSecMsg("");
    try {
      await post("/api/auth/change-password", { current_password: curPw, new_password: newPw });
      setCurPw(""); setNewPw(""); setSecMsg(t("密码已修改"));
    } catch (e) { setSecMsg(String((e as Error).message)); }
    finally { setBusy(false); }
  };

  const registerFingerprint = async () => {
    if (!window.PublicKeyCredential) { setSecMsg(t("当前浏览器不支持指纹登录")); return; }
    setBusy(true); setSecMsg("");
    try {
      const opts = await post("/api/auth/webauthn/register/begin", {}) as Any;
      const pk: Any = {
        ...opts,
        challenge: b64urlToBuf(opts.challenge),
        user: { ...opts.user, id: b64urlToBuf(opts.user.id) },
        excludeCredentials: (opts.excludeCredentials || []).map((c: Any) => ({ ...c, id: b64urlToBuf(c.id) })),
      };
      const cred = await navigator.credentials.create({ publicKey: pk as PublicKeyCredentialCreationOptions }) as any;
      if (!cred) throw new Error(t("未完成指纹注册"));
      await post("/api/auth/webauthn/register/finish", { credential: credentialToJSON(cred), label: t("指纹") });
      await loadCreds();
      setSecMsg(t("指纹已注册，下次登录可用指纹登录"));
    } catch (e) { setSecMsg(String((e as Error).message)); }
    finally { setBusy(false); }
  };

  const saveGoogleOAuth = () => {
    const patch: Any = {
      google_oauth_client_id: (document.getElementById("oauth-google-id") as HTMLInputElement)?.value ?? "",
    };
    const gsec = (document.getElementById("oauth-google-secret") as HTMLInputElement)?.value || "";
    if (gsec && gsec !== SECRET_MASK) patch.google_oauth_client_secret = gsec;
    saveSettings(patch);
    setMsg(t("已保存成功"));
  };

  const saveGithubOAuth = () => {
    const patch: Any = {
      github_oauth_client_id: (document.getElementById("oauth-github-id") as HTMLInputElement)?.value ?? "",
    };
    const hsec = (document.getElementById("oauth-github-secret") as HTMLInputElement)?.value || "";
    if (hsec && hsec !== SECRET_MASK) patch.github_oauth_client_secret = hsec;
    saveSettings(patch);
    setMsg(t("已保存成功"));
  };

  return (
    <div className="card"><h2>{t("登录与安全")}</h2>
      <h3 style={{ marginTop: 12 }}>{t("修改密码")}</h3>
      <div style={{ paddingLeft: "2em" }}><div className="form2">
        <div><label>{t("当前密码")}</label><input type="password" value={curPw} onChange={(e) => setCurPw(e.target.value)} placeholder={t("首次设置留空")} /></div>
        <div><label>{t("新密码")}</label><input type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} placeholder={t("至少 6 位")} /></div>
      </div>
      <div className="row" style={{ marginTop: 8, paddingLeft: "2em" }}>
        <button className="btn" onClick={changePw} disabled={busy}>{t("修改密码")}</button>
      </div></div>
      <h3 style={{ marginTop: 20 }}>{t("指纹登录")}</h3>
      <div className="row" style={{ paddingLeft: "2em" }}>
        <button className="btn" onClick={registerFingerprint} disabled={busy}>{t("注册指纹")}</button>
      </div>
      {creds.map((c) => (
        <div key={c.credential_id as string} className="row" style={{ marginTop: 8, paddingLeft: "2em" }}>
          <span>{c.label as string || t("指纹")}</span>
          <span className="muted">{new Date((c.created_at as number) * 1000).toLocaleDateString(lang === "zh" ? "zh-CN" : "en-US")}</span>
          <span className="spacer" />
          <button className="icon-btn danger" title={t("删除")} onClick={async () => { if (!confirm(t("删除指纹 {label}？", { label: (c.label as string) || t("指纹") }))) return; await del(`/api/auth/webauthn/${c.credential_id}`); await loadCreds(); }}>
            <Ico label={t("删除")}><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /><line x1="10" y1="11" x2="10" y2="17" /><line x1="14" y1="11" x2="14" y2="17" /></Ico>
          </button>
        </div>
      ))}
      <h3 style={{ marginTop: 20 }}>{t("Google 登录")}</h3>
      <div style={{ paddingLeft: "2em" }}><div className="form2">
        <div><label>Google Client ID</label><input id="oauth-google-id" defaultValue={String(settings.google_oauth_client_id ?? "")} placeholder={t("未配置")} onBlur={saveGoogleOAuth} /></div>
        <div><label>Google Client Secret</label><input type="password" id="oauth-google-secret" key={String(settings.google_oauth_configured)} defaultValue={settings.google_oauth_configured ? SECRET_MASK : ""} placeholder={settings.google_oauth_configured ? t("已配置，留空保持不变") : t("未配置")} onFocus={secretFocus} onBlur={(e) => { secretBlurRestore(e, Boolean(settings.google_oauth_configured)); saveGoogleOAuth(); }} /></div>
      </div></div>
      <h3 style={{ marginTop: 20 }}>{t("GitHub 登录")}</h3>
      <div style={{ paddingLeft: "2em" }}><div className="form2">
        <div><label>GitHub Client ID</label><input id="oauth-github-id" defaultValue={String(settings.github_oauth_client_id ?? "")} placeholder={t("未配置")} onBlur={saveGithubOAuth} /></div>
        <div><label>GitHub Client Secret</label><input type="password" id="oauth-github-secret" key={String(settings.github_oauth_configured)} defaultValue={settings.github_oauth_configured ? SECRET_MASK : ""} placeholder={settings.github_oauth_configured ? t("已配置，留空保持不变") : t("未配置")} onFocus={secretFocus} onBlur={(e) => { secretBlurRestore(e, Boolean(settings.github_oauth_configured)); saveGithubOAuth(); }} /></div>
      </div></div>
      {secMsg && <p style={{ color: "#f87171", marginTop: 12, paddingLeft: "2em" }}>{secMsg}</p>}
    </div>
  );
}

// ---------------- 备份与恢复卡片（全局设置页） ----------------
function BackupCard({ setMsg, load }: { setMsg: (s: string) => void; load: () => Promise<void> }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [fileName, setFileName] = useState("");

  const download = async () => {
    setBusy(true);
    try {
      const r = await fetch("/api/backup/export", {
        headers: { Authorization: `Bearer ${getToken() || ""}` },
        credentials: "same-origin",
      });
      if (!r.ok) throw new Error(t("下载失败 {status}", { status: r.status }));
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const disp = r.headers.get("Content-Disposition") || "";
      const m = /filename="([^"]+)"/.exec(disp);
      a.href = url;
      a.download = m ? m[1] : "cdt-backup.json";
      a.click();
      URL.revokeObjectURL(url);
      setMsg(t("备份已下载"));
    } catch (e) { setMsg(String((e as Error).message)); }
    finally { setBusy(false); }
  };

  const restore = async () => {
    const input = document.getElementById("backup-file") as HTMLInputElement;
    const file = input?.files?.[0];
    if (!file) { setMsg(t("请先选择备份文件（.json）")); return; }
    if (!confirm(t("恢复备份会整体替换当前全部数据（账户、域名、分组、设置、管理员密码都会回到备份时的状态），确定继续吗？"))) return;
    setBusy(true);
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const r = await post("/api/backup/restore", data) as Any;
      const counts = r.counts || {};
      setMsg(t("备份已恢复：账户 {accounts} 个、域名 {domains} 个、分组 {groups} 个", { accounts: counts.accounts ?? 0, domains: counts.domains ?? 0, groups: counts.groups ?? 0 }));
      setFileName("");
      input.value = "";
      await load();
    } catch (e) { setMsg(String((e as Error).message)); }
    finally { setBusy(false); }
  };

  return (
    <div className="card"><h2>{t("备份与恢复")}</h2>
      <div className="row" style={{ paddingLeft: "2em" }}>
        <button className="btn" onClick={download} disabled={busy}>{t("下载备份")}</button>
        <input type="file" id="backup-file" accept=".json,application/json"
          style={{ flex: 1 }} onChange={(e) => setFileName(e.target.files?.[0]?.name || "")} />
        <button className="btn" onClick={restore} disabled={busy || !fileName}>{t("恢复备份")}</button>
      </div>
      {fileName && <p className="muted" style={{ paddingLeft: "2em" }}>{t("已选：{name}", { name: fileName })}</p>}
    </div>
  );
}

// ---------------- 站点信息卡片（其它设置页） ----------------
function SiteInfoCard({ settings, setMsg, load }: { settings: Any; setMsg: (s: string) => void; load: () => Promise<void> }) {
  const { t } = useI18n();
  const [intro, setIntro] = useState((settings?.site_intro as string) || "");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const hasLogo = Boolean(settings?.site_logo);

  const saveIntro = async () => {
    await put("/api/settings", { site_intro: intro.trim() });
    setMsg(t("已保存成功"));
    await load();
  };

  const uploadLogo = async (f: File) => {
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch("/api/settings/logo", {
        method: "POST",
        headers: { Authorization: `Bearer ${getToken() || ""}` },
        body: fd,
      });
      if (!r.ok) {
        const e = await r.json().catch(() => ({} as Any));
        setMsg((e as Any).detail || t("上传失败"));
        return;
      }
      setMsg(t("已保存成功"));
      await load();
    } catch { setMsg(t("上传失败")); }
    finally { setBusy(false); }
  };

  const removeLogo = async () => {
    setBusy(true);
    try {
      await fetch("/api/settings/logo", {
        method: "DELETE",
        headers: { Authorization: `Bearer ${getToken() || ""}` },
      });
      setMsg(t("已保存成功"));
      await load();
    } catch { setMsg(t("删除失败")); }
    finally { setBusy(false); }
  };

  return (
    <div className="card"><h2>{t("站点信息")}</h2>
      <div style={{ paddingLeft: "2em" }}>
      <label className="lbl">{t("站点 logo")}</label>
      <div className="row" style={{ alignItems: "center" }}>
        {hasLogo && (
          <img src="/api/settings/logo" alt="logo" style={{ height: 40, maxWidth: 160, objectFit: "contain" }} />
        )}
        <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml,image/x-icon"
          style={{ display: "none" }}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadLogo(f); e.target.value = ""; }} />
        <button className="btn" onClick={() => fileRef.current?.click()} disabled={busy}>{t("上传 logo")}</button>
        {hasLogo && (
          <button className="btn ghost" onClick={() => void removeLogo()} disabled={busy}>{t("删除 logo")}</button>
        )}
      </div>
      <div style={{ marginTop: 12 }}>
        <label className="lbl">{t("站点简介")}</label>
        <input value={intro} onChange={(e) => setIntro(e.target.value)} onBlur={() => void saveIntro()} placeholder={t("页眉显示的简介，留空显示默认副标题")} />
      </div>
      </div>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>("dash");
  const [subTab, setSubTab] = useState<SubTab>("settings");
  const [status, setStatus] = useState<Any | null>(null);
  const [accounts, setAccounts] = useState<Any[]>([]);
  const [domains, setDomains] = useState<Any[]>([]);
  const [groups, setGroups] = useState<Any[]>([]);
  // 拖动排序：实例管理 / 分组管理卡片可拖拽换位
  const [dragId, setDragId] = useState<number | null>(null);
  const [dragKind, setDragKind] = useState<"account" | "group" | null>(null);
  const [dragOverId, setDragOverId] = useState<number | null>(null);
  const [settings, setSettings] = useState<Any>({});
  const [timezones, setTimezones] = useState<Any[]>([]);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [editingId, setEditingId] = useState<number | "new" | null>(null);
  // 卡片设置面板：卡片右上角 ⚙ 按钮打开，面板内最下面是删除实例
  const [cardSettingsId, setCardSettingsId] = useState<number | null>(null);
  // 概览视图：卡片 / 列表
  const [viewMode, setViewMode] = useState<"card" | "list">("card");
  // 域名管理页 DDNS 设置卡片是否展开（右上角 ⚙ 开关）
  const [showDdns, setShowDdns] = useState(true);
  // 域名管理页：正在编辑的行（null=无），行内表单
  const [editDomId, setEditDomId] = useState<number | null>(null);
  const [editDomName, setEditDomName] = useState("");
  const [editDomGroup, setEditDomGroup] = useState("");
  // 添加域名对话框（点“+ 添加域名”弹出，填完确认才添加）
  const [showAddDom, setShowAddDom] = useState(false);
  const [addDomName, setAddDomName] = useState("");
  const [acting, setActing] = useState<Record<number, string>>({});

  // 消息提示 5 秒后自动隐藏；新消息进来重新计时。
  useEffect(() => {
    if (!msg) return;
    const t = setTimeout(() => setMsg(""), 5000);
    return () => clearTimeout(t);
  }, [msg]);

  // ---------- 语言：中英切换，默认英文（localStorage `cdt-lang`） ----------
  const { lang, setLang, t } = useI18n();

  // ---------- 主题：亮色 / 暗色 / 跟随系统（磨砂玻璃，尺寸不变） ----------
  const [theme, setTheme] = useState<string>(() => {
    try { return localStorage.getItem("cdt-theme") || "system"; } catch { return "system"; }
  });
  const [themeMenuOpen, setThemeMenuOpen] = useState(false);
  useEffect(() => {
    const apply = () => {
      const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
      document.documentElement.dataset.theme = dark ? "dark" : "light";
    };
    apply();
    try { localStorage.setItem("cdt-theme", theme); } catch { /* 忽略 */ }
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const h = () => { if (theme === "system") apply(); };
    mq.addEventListener("change", h);
    return () => mq.removeEventListener("change", h);
  }, [theme]);

  // ---------- 主题风格：5 种配色（只改按钮与边框，磨砂底色不变） ----------
  const ACCENTS = [
    { id: "emerald", zh: "森之韵", en: "Emerald", gradient: "linear-gradient(135deg,#a7f3d0,#10b981)" },
    { id: "deepblue", zh: "深海谧", en: "Deep Blue", gradient: "linear-gradient(135deg,#bfdbfe,#3b82f6)" },
    { id: "warmsun", zh: "暖阳橘", en: "Warm Sun", gradient: "linear-gradient(135deg,#fed7aa,#f97316)" },
    { id: "nebula", zh: "紫微星", en: "Nebula", gradient: "linear-gradient(135deg,#ddd6fe,#8b5cf6)" },
    { id: "crimson", zh: "绯红月", en: "Crimson", gradient: "linear-gradient(135deg,#fecaca,#ef4444)" },
  ];
  const [accent, setAccent] = useState<string>(() => {
    try { return localStorage.getItem("cdt-accent") || "deepblue"; } catch { return "deepblue"; }
  });
  useEffect(() => {
    document.documentElement.dataset.accent = accent;
    try { localStorage.setItem("cdt-accent", accent); } catch { /* 忽略 */ }
  }, [accent]);

  // ---------- 登录门：首次设置 / 登录 / 已登录 ----------
  const [authState, setAuthState] = useState<"loading" | "setup" | "login" | "authed">("loading");
  const [authInfo, setAuthInfo] = useState<Any>({});
  const [oauthMsg, setOauthMsg] = useState("");

  const checkAuth = async () => {
    try {
      const s = await get("/api/auth/status") as Any;
      setAuthInfo(s);
      if (!s.admin_configured) { setAuthState("setup"); return; }
      if (getToken()) {
        try { await get("/api/status"); setAuthState("authed"); return; }
        catch { setAuthState("login"); return; }
      }
      setAuthState("login");
    } catch { setAuthState("login"); }
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");
    const oerr = params.get("oauth_error");
    if (token) { setToken(token); window.history.replaceState({}, "", window.location.pathname); }
    if (oerr) {
      setOauthMsg(oerr === "identity_mismatch" ? t("该 Google / GitHub 账号与已绑定的管理员身份不一致")
        : oerr === "invalid_state" ? t("登录验证已过期，请重试")
        : t("第三方登录失败（{err}）", { err: oerr }));
      window.history.replaceState({}, "", window.location.pathname);
    }
    checkAuth();
    const onUnauth = () => { setToken(null); setAuthState("login"); };
    window.addEventListener("cdt-unauthorized", onUnauth);
    return () => window.removeEventListener("cdt-unauthorized", onUnauth);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 状态点（新映射）：green 在线 / yellow 排队中 / gray 已跳出或未知 /
  // red 停机或离线 / green_pulse 启动中（高亮闪烁绿）/ red_pulse 停止中（闪烁红）
  // 状态点颜色：Google 品牌色（green #34A853 / yellow #FBBC04 / gray #9AA0A6 / red #EA4335），
  // 与 styles.css 的 --dot-* 变量一致；尺寸 8px（直径约 2mm），垂直居中于字体高度，见 .dot。
  const dotColor = (dot: string | undefined) =>
    dot === "green" ? "#34A853" : dot === "green_pulse" ? "#34A853" : dot === "yellow" ? "#FBBC04" : dot === "gray" ? "#9AA0A6" : dot === "red_pulse" ? "#EA4335" : "#EA4335";
  const dotClass = (dot: string | undefined) =>
    dot === "green_pulse" ? "dot-pulse" : dot === "red_pulse" ? "dot-pulse-red" : "";
  const dotEl = (dot: string | undefined, title?: string) => (
    <span className={`dot ${dotClass(dot)}`} title={title || ""}
      style={{ width: 8, height: 8, borderRadius: "50%", display: "inline-block", background: dotColor(dot), marginRight: 6, verticalAlign: "middle" }} />
  );
  const load = async () => {
    try {
      const s = await get("/api/status") as Any;
      setStatus(s);
      setAccounts((s.accounts as Any[]) || []);
      setDomains((s.domains as Any[]) || []);
      setGroups((s.groups as Any[]) || []);
    } catch { /* 后端未就绪时先显示空态 */ }
    try { setSettings(await get("/api/settings") as Any); } catch { /* ignore */ }
    try { setTimezones((((await get("/api/timezones") as Any).timezones as Any[]) || [])); } catch { /* 失败时下拉用内置列表 */ }
  };
  useEffect(() => { if (authState === "authed") load(); }, [authState]);

  const [logs, setLogs] = useState<Any[]>([]);
  const loadLogs = async () => {
    try { setLogs(await get("/api/logs?limit=100") as Any[]); }
    catch (e) { setMsg(String((e as Error).message)); }
  };
  useEffect(() => { if (tab === "config" && subTab === "logs" && authState === "authed") loadLogs(); }, [tab, subTab, authState]);

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true); setMsg("");
    try { await fn(); setMsg(ok); await load(); }
    catch (e) { setMsg(String((e as Error).message)); }
    finally { setBusy(false); }
  };

  // 拖动排序：把卡片拖到另一张卡片上松手，即按新顺序保存
  const clearDrag = () => { setDragId(null); setDragKind(null); setDragOverId(null); };
  const moveItem = (kind: "account" | "group", fromId: number, toId: number) => {
    const list = kind === "account" ? accounts : groups;
    const ids = list.map((x: Any) => x.id as number);
    const from = ids.indexOf(fromId); const to = ids.indexOf(toId);
    if (from < 0 || to < 0 || from === to) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    run(() => post(`/api/${kind === "account" ? "accounts" : "groups"}/reorder`, { order: ids }), t("已调整顺序"));
  };
  const dragProps = (kind: "account" | "group", id: number) => ({
    draggable: true,
    onDragStart: (e: ReactDragEvent) => { setDragId(id); setDragKind(kind); e.dataTransfer.effectAllowed = "move"; },
    onDragOver: (e: ReactDragEvent) => { e.preventDefault(); if (dragId != null && dragKind === kind && dragId !== id) setDragOverId(id); },
    onDragLeave: (e: ReactDragEvent) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragOverId(null); },
    onDrop: (e: ReactDragEvent) => { e.preventDefault(); if (dragId != null && dragKind === kind) moveItem(kind, dragId, id); clearDrag(); },
    onDragEnd: () => clearDrag(),
  });

  const saveSettings = async (patch: Any) => {
    await run(async () => put("/api/settings", patch), t("已保存成功"));
  };

  // DDNS 设置（Cloudflare Token + 时区）：Token 留空保持不变，只有填了
  // 新值才提交；改动自动保存。
  const ddnsPatch = () => {
    const patch: Any = { timezone: (document.getElementById("s-timezone") as HTMLSelectElement)?.value || "Asia/Shanghai" };
    const tok = (document.getElementById("cf-token") as HTMLInputElement)?.value || "";
    if (tok && tok !== SECRET_MASK) patch.cloudflare_token = tok;
    // 域名 / 账户邮箱都不是密钥，明文显示、改完即存（留空=清除）
    patch.cloudflare_zone = ((document.getElementById("cf-zone") as HTMLInputElement)?.value || "").trim();
    patch.cloudflare_email = ((document.getElementById("cf-email") as HTMLInputElement)?.value || "").trim();
    return patch;
  };

  // 切换模式按分组设置：全局设置页只保留“新建分组的默认值”，
  // 时间/差值两组控件都显示，不再按全局模式变灰。
  // 阈值踢出/保活/备用地址不受切换模式影响。
  const groupName = (gid: number | null | undefined) =>
    gid ? (groups.find((g) => g.id === gid)?.name as string) || t("未分组") : t("未分组");
  const groupMode = (gid: number | null | undefined) =>
    gid ? (groups.find((g) => g.id === gid)?.switch_mode as string) || "load_balance" : "";

  // ---------- 单台操作：刷新 / 启动 / 重启 / 停机 ----------
  const accountAction = async (a: Any, action: "refresh" | "start" | "restart" | "stop") => {
    const label = action === "refresh" ? t("刷新") : action === "start" ? t("启动") : action === "restart" ? t("重启") : t("停机");
    if (action === "stop" && !confirm(t("停机 {name}？若它仍在被域名指向，保活可能在下次调度把它重新拉起。", { name: a.name }))) return;
    setActing((m) => ({ ...m, [a.id]: action }));
    try {
      await post(`/api/accounts/${a.id}/${action}`, {});
      setMsg(t("已执行{label}：{name}", { label, name: a.name }));
      await load();
    } catch (e) { setMsg(String((e as Error).message)); }
    finally {
      setActing((m) => { const n = { ...m }; delete n[a.id]; return n; });
    }
  };
  // 单台操作图标按钮（放卡片右下角；列表的操作列同样用图标）。
  // 启动按钮随状态变：已停机时绿色三角可点，其余状态灰色方块不可点；
  // 重启蓝色，停机红色，刷新默认色。
  const ActionButtons = ({ a }: { a: Any }) => {
    const busy = !!acting[a.id];
    const busyLabel = { refresh: "刷新中…", start: "启动中…", restart: "重启中…", stop: "停机中…" } as const;
    const stopped = a.instance_status === "Stopped";
    const btn = (action: "refresh" | "start" | "restart" | "stop", label: string,
                 disabled: boolean, icon: JSX.Element, cls = "") => (
      <button key={action} className={cls ? `icon-btn ${cls}` : "icon-btn"} title={acting[a.id] === action ? t(busyLabel[action]) : label}
        aria-label={label} disabled={busy || disabled}
        onClick={() => accountAction(a, action)}>{icon}</button>
    );
    return (
      <div className="row" style={{ gap: 6 }}>
        {btn("refresh", t("刷新"), false,
          <Ico label={t("刷新")}><polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" /></Ico>)}
        {btn("start", t("启动"), !stopped,
          stopped
            ? <Ico label={t("启动")}><polygon points="6 3 20 12 6 21 6 3" fill="currentColor" stroke="none" /></Ico>
            : <Ico label={t("启动")}><rect x="6" y="6" width="12" height="12" fill="currentColor" stroke="none" /></Ico>,
          stopped ? "green" : "gray")}
        {btn("restart", t("重启"), a.instance_status !== "Running",
          <Ico label={t("重启")}><polyline points="1 4 1 10 7 10" /><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" /></Ico>, "blue")}
        {btn("stop", t("停机"), a.instance_status !== "Running",
          <Ico label={t("停机")}><path d="M18.36 6.64a9 9 0 1 1-12.73 0" /><line x1="12" y1="2" x2="12" y2="12" /></Ico>, "danger")}
      </div>
    );
  };

  // ---------- 卡片设置面板：右上角 ⚙ 打开；配置表单 + 复制 + 删除实例（最下面） ----------
  const CardSettings = ({ a }: { a: Any }) => (
    <div style={{ marginTop: 12, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
      <InstanceForm account={a} settings={settings} count={accounts.length} groups={groups}
        onDone={() => { setCardSettingsId(null); load(); setMsg(t("已保存成功")); }}
        onCancel={() => setCardSettingsId(null)}
        onAutoSaved={() => { load(); setMsg(t("已保存成功")); }} />
      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn ghost" title={t("复制配置摘要")} onClick={async () => {
          const text = `${a.name}\n${t("实例 ID")}：${a.instance_id}\n${t("地域")}：${a.region}\nIP：${a.public_ip || "—"}`;
          try { await navigator.clipboard.writeText(text); setMsg(t("配置摘要已复制")); }
          catch { setMsg(text); }
        }}>⧉ {t("复制配置摘要")}</button>
      </div>
      <div style={{ marginTop: 12, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
        <button className="btn" style={{ background: "#EA4335", borderColor: "#EA4335", color: "#fff" }}
          onClick={() => { if (confirm(t("删除实例 {name}？删除后域名将重新分配。", { name: a.name }))) { setCardSettingsId(null); run(() => del(`/api/accounts/${a.id}`), t("已删除")); } }}>
          {t("删除实例")}
        </button>
      </div>
    </div>
  );

  // ---------- 全局设置页的按键式选择：与 settings 同步 ----------
  const [sExcl, setSExcl] = useState("none");
  const [sRecovery, setSRecovery] = useState("start");
  const [sDiffMode, setSDiffMode] = useState("percent");
  const [sInterval, setSInterval] = useState("6");
  const [sCustVal, setSCustVal] = useState("0");
  const [sCustUnit, setSCustUnit] = useState("minute");
  const [sRefresh, setSRefresh] = useState("5");
  const [sCurrency, setSCurrency] = useState("USD");
  useEffect(() => {
    setSExcl(settings.exclusion_action ?? "none");
    setSRecovery(settings.threshold_recovery_action ?? "start");
    setSDiffMode(settings.switch_diff_mode ?? "percent");
    setSInterval(String(settings.interval_hours ?? 6));
    const cm = Number(settings.custom_interval_minutes ?? 0) || 0;
    const cu = cm > 0 && cm % 1440 === 0 ? "day" : cm > 0 && cm % 60 === 0 ? "hour" : "minute";
    setSCustUnit(cu);
    setSCustVal(String(cu === "day" ? cm / 1440 : cu === "hour" ? cm / 60 : cm));
    const r = String(settings.api_refresh_minutes ?? 5);
    setSRefresh(["0", "5", "10", "15", "30", "60"].includes(r) ? r : "5");
    setSCurrency(settings.settlement_currency === "CNY" ? "CNY" : "USD");
  }, [settings]);

  // 自定义间隔单位换算：后端存的一律是分钟；分钟 1 / 小时 60 / 天 1440。
  const custMult = (u: string) => (u === "day" ? 1440 : u === "hour" ? 60 : 1);
  const fmtCust = (v: number) => String(Number.isInteger(v) ? v : Math.round(v * 100) / 100);
  const changeCustUnit = (u: string) => {
    const mins = (parseFloat(sCustVal) || 0) * custMult(sCustUnit);
    setSCustUnit(u);
    setSCustVal(fmtCust(mins / custMult(u)));
  };

  // 参数设置自动保存：改动即存（按键改完即存、输入框失焦即存），不用点保存
  const saveParamSettings = (overrides: Any = {}) => {
    const patch: Any = {
      quota_gb: (document.getElementById("s-quota") as HTMLInputElement).value,
      protection_threshold_pct: (document.getElementById("s-prot") as HTMLInputElement).value,
      switch_diff_mode: sDiffMode,
      switch_diff_pct: (document.getElementById("s-diff") as HTMLInputElement)?.value || String(settings.switch_diff_pct ?? 10),
      switch_diff_gb: (document.getElementById("s-diffgb") as HTMLInputElement)?.value || String(settings.switch_diff_gb ?? 10),
      interval_hours: sInterval,
      custom_interval_minutes: String(Math.round((parseFloat(sCustVal) || 0) * custMult(sCustUnit))),
      exclusion_action: sExcl,
      threshold_recovery_action: sRecovery,
      api_refresh_minutes: sRefresh,
      // 不刷新（0）= 关闭调度；选分钟数 = 开始调度。跟暂停开关同步，
      // 让后端已有的暂停逻辑直接生效。
      scheduler_paused: sRefresh === "0" ? "1" : "0",
      settlement_currency: sCurrency,
      exchange_rate_usd_cny: String(settings.exchange_rate_usd_cny ?? "6.6719"),
      ...ddnsPatch(),
    };
    return saveSettings({ ...patch, ...overrides });
  };

  // ---------- 每日财报（通知设置两个渠道框体共用同一组开关） ----------
  const [drEnabled, setDrEnabled] = useState(true);
  const [drTime, setDrTime] = useState("00:00");
  useEffect(() => {
    setDrEnabled(settings.daily_report_enabled !== "0");
    setDrTime(String(settings.daily_report_time ?? "00:00"));
  }, [settings]);

  // ---------- 分组新建表单 ----------
  const [showNewGroup, setShowNewGroup] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupMode, setNewGroupMode] = useState("");
  // 新建分组时选一个已添加的域名（可选；一个域名只能被选取一次，已被选取的不可选）
  const [newGroupDomain, setNewGroupDomain] = useState("");

  if (authState === "loading") {
    return <div className="app"><p className="muted">{t("加载中…")}</p></div>;
  }
  if (authState === "setup") {
    return (
      <div className="app">
        <SetupScreen onDone={() => { setAuthState("authed"); }} onSkip={() => { setAuthState("authed"); }} />
      </div>
    );
  }
  if (authState === "login") {
    return (
      <div className="app">
        <LoginScreen info={authInfo} oauthMsg={oauthMsg} onDone={() => { setAuthState("authed"); }} />
      </div>
    );
  }

  // 当前费用卡片右上角：最后一次账单读取日期（本地 YYYY-MM-DD）。
  // 取各账户 billing_updated_at（秒级时间戳）的最大值；全为 0/缺失时退而取
  // 最近一次调度的 ran_at 日期；两者都没有就不显示。
  const maxBillingTs = Math.max(0, ...accounts.map((a) => (a.billing_updated_at as number) || 0));
  const costAsOf = (() => {
    if (maxBillingTs > 0) {
      const d = new Date(maxBillingTs * 1000);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }
    return (((status?.scheduler as Any)?.last_run?.ran_at as string) || "").slice(0, 10);
  })();

  // 本月流量卡片：总额度与已用占比（总额度为 0 时不显示百分比）。
  const totalQuota = accounts.reduce((s, a) => s + ((a.quota_gb as number) ?? 200), 0);
  const totalUsedPct = totalQuota > 0 ? ((status?.total_used_gb ?? 0) / totalQuota * 100) : null;

  // 当前费用卡片：按结算货币 + 汇率换算成单一货币显示；
  // 汇率缺失/无效、或账单含 USD/CNY 以外币种时，回退按币种分别显示。
  const costConverted = (() => {
    const tc = (status?.total_cost as Any) || {};
    const keys = Object.keys(tc);
    if (keys.length === 0) return null;
    if (keys.some((k) => k !== "USD" && k !== "CNY")) return null;
    const rate = parseFloat(String(settings.exchange_rate_usd_cny ?? ""));
    if (!(rate > 0)) return null;
    const cur = settings.settlement_currency === "CNY" ? "CNY" : "USD";
    const usd = Number(tc.USD || 0);
    const cny = Number(tc.CNY || 0);
    return { total: cur === "CNY" ? cny + usd * rate : usd + cny / rate, cur, rate: String(settings.exchange_rate_usd_cny) };
  })();

  return (
    <div className="app">
      <header>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div className="row" style={{ alignItems: "center", gap: 10 }}>
            {(settings?.site_logo as string) ? (
              <img src="/api/settings/logo" alt="logo" style={{ height: 36, maxWidth: 140, objectFit: "contain" }} />
            ) : null}
            <h1 style={{ margin: 0 }}>{tab === "dash" ? t("运行概览") : tab === "accounts" ? t("实例管理") : tab === "groups" ? t("分组管理") : tab === "domains" ? t("域名管理") : subTab === "settings" ? t("全局设置") : subTab === "account" ? t("账号设置") : subTab === "notify" ? t("通知设置") : subTab === "logs" ? t("日志统计") : t("其它设置")}</h1>
          </div>
          <div className="row">
            <button className="btn ghost lang-btn" aria-label={t("语言")} title={t("语言")} onClick={() => setLang(lang === "zh" ? "en" : "zh")}>
              <span className={lang === "zh" ? "lang-on" : "lang-off"}>中</span>
              <span className="lang-sep">/</span>
              <span className={lang === "en" ? "lang-on" : "lang-off"}>En</span>
            </button>
            <div className="theme-menu">
              <button className="btn ghost theme-btn" aria-label={t("主题")} onClick={() => { setThemeMenuOpen(!themeMenuOpen); }}>
                <ThemeIcon theme={theme} />
              </button>
              {themeMenuOpen && (
                <>
                  <div className="menu-backdrop" onClick={() => setThemeMenuOpen(false)} />
                  <div className="menu">
                    {(["light", "dark", "system"] as const).map((v) => (
                      <button key={v} className={theme === v ? "sel" : ""} onClick={() => { setTheme(v); setThemeMenuOpen(false); }}>
                        <span className="menu-ico"><ThemeIcon theme={v} /></span>
                        <span>{t(v === "light" ? "亮色" : v === "dark" ? "暗色" : "跟随系统")}</span>
                        <span className="menu-check">{theme === v ? "✓" : ""}</span>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
            <button className="btn ghost theme-btn" aria-label={t("退出登录")} title={t("退出登录")} onClick={() => {
              post("/api/auth/logout", {}).catch(() => {});
              setToken(null);
              setAuthState("login");
            }}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
            </button>
          </div>
        </div>
        <p>{(settings?.site_intro as string)?.trim() ? (settings.site_intro as string) : (tab === "dash" ? t("掌握账户流量、VPS 状态和域名分配。") : tab === "accounts" ? t("管理中国站与国际站账户，按需覆盖全局调度参数。") : tab === "groups" ? t("一个分组只选一种切换模式，1 个分组最多绑定 1 个域名。") : tab === "domains" ? t("管理 Cloudflare 域名，保留现有分配并按需切换账户。") : subTab === "settings" ? t("设置调度规则。") : subTab === "account" ? t("登录与安全、备份与恢复。") : subTab === "notify" ? t("仅事件通知。可同时启用 E-Mail 和 Telegram。") : subTab === "logs" ? t("最近的事件记录，最新的在前面。") : t("货币结算、主题。"))}</p>
      </header>
      <nav>{TABS.map(([k, label]) => (
        <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{t(label)}</button>
      ))}</nav>
      {tab === "config" && (
        <div className="subtabs">{SUB_TABS.map(([k, label]) => (
          <button key={k} className={subTab === k ? "active" : ""} onClick={() => setSubTab(k)}>{t(label)}</button>
        ))}</div>
      )}
      {msg && <div className="toast">{msg}</div>}

      {tab === "dash" && (
        <>
          <div className="grid3">
            <div className="card" style={{ fontSize: 14, display: "flex", flexDirection: "column" }}><h3>{t("实例状态")}</h3>
              <div style={{ marginTop: 6 }}>{t("服务器总数 {count}", { count: accounts.length })}</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 12px", marginTop: 8 }}>
                <div>{dotEl("green")}{t("在线状态 {count}", { count: accounts.filter((a) => a.status_dot === "green").length })}</div>
                <div>{dotEl("yellow")}{t("排队 {count}", { count: accounts.filter((a) => a.status_dot === "yellow").length })}</div>
                <div>{dotEl("gray")}{t("离队 {count}", { count: accounts.filter((a) => a.evicted).length })}</div>
                <div>{dotEl("red")}{t("停机 {count}", { count: accounts.filter((a) => a.status_dot === "red" || a.status_dot === "red_pulse").length })}</div>
                {accounts.some((a) => a.status_label === "未参与") && (
                  <div>{dotEl("gray")}{t("未参与 {count}", { count: accounts.filter((a) => a.status_label === "未参与").length })}</div>
                )}
                {accounts.some((a) => a.status_dot === "green_pulse") && (
                  <div>{dotEl("green_pulse")}{t("启动中 {count}", { count: accounts.filter((a) => a.status_dot === "green_pulse").length })}</div>
                )}
              </div>
              <div style={{ marginTop: "auto", paddingTop: 8, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                <span style={{ color: "var(--accent)" }}>{t("调度")}{(status?.scheduler as Any)?.paused ? t("已暂停") : t("运行中")} · {t("API 刷新")} {Number((status?.scheduler as Any)?.api_refresh_minutes ?? (status?.scheduler as Any)?.check_interval_minutes ?? 5) === 0 ? t("不刷新") : `${(status?.scheduler as Any)?.api_refresh_minutes ?? (status?.scheduler as Any)?.check_interval_minutes ?? 5} ${t("分钟")}`} · {t("DNS 已确认")} {status?.dns_confirmed ?? 0} / {status?.dns_total ?? 0}</span>
              </div>
            </div>
            <div className="card" style={{ display: "flex", flexDirection: "column" }}><h3>{t("本月流量")}</h3>
              <div className="big">{fmtGb(status?.total_used_gb ?? 0)} GiB / {fmtNum(totalQuota)} GiB{totalUsedPct != null ? ` (${totalUsedPct.toFixed(2)}%)` : ""}</div>
              <div style={{ color: "var(--accent)", fontSize: 14, marginTop: "auto", paddingTop: 8 }}>{t("已取得 {a} / {b} 个账户用量", { a: status?.accounts_known ?? 0, b: status?.accounts_total ?? 0 })}</div>
            </div>
            <div className="card" style={{ display: "flex", flexDirection: "column" }}><h3>{t("当前费用")}{costAsOf ? <span className="muted" style={{ float: "right" }}>{t("截至 {date}", { date: costAsOf })}</span> : null}</h3>
              <div className="big">{costConverted ? `${costConverted.total.toFixed(2)} ${costConverted.cur}` : (Object.entries((status?.total_cost as Any) || {}).map(([c, v]) => `${fmtCost(v)} ${c}`).join(" / ") || "—")}</div>
              <div style={{ color: "var(--accent)", fontSize: 14, marginTop: "auto", paddingTop: 8 }}>{costConverted ? t("已查询 {a} / {b} 个账户账单，按汇率 {rate} 换算成 {currency}", { a: status?.accounts_known ?? 0, b: status?.accounts_total ?? 0, rate: costConverted.rate, currency: costConverted.cur }) : t("已查询 {a} / {b} 个账户账单，按币种汇总", { a: status?.accounts_known ?? 0, b: status?.accounts_total ?? 0 })}</div>
            </div>
          </div>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h2 style={{ margin: 0 }}>{t("服务器")}</h2>
              <Seg value={viewMode} options={[
                { value: "card", label: t("卡片") },
                { value: "list", label: t("列表") },
              ]} onChange={(v) => setViewMode(v as "card" | "list")} />
            </div>
            {viewMode === "card" ? (
              <div className="grid3" style={{ marginTop: 12 }}>
                {accounts.map((a) => (
                  <div className="card" key={a.id as number} style={{ marginBottom: 0, display: "flex", flexDirection: "column" }}>
                    <div>{dotEl(a.status_dot, a.status_detail || a.status_label || "")}<b style={{ fontSize: 16 }}>{a.name as string}</b> <span className="pill">{a.status_label ? trStatus(a.status_label, lang) : (a.instance_status || t("未知"))}</span>{a.evicted ? <span className="pill gray" style={{ marginLeft: 6 }}>{t("已跳出")}</span> : null}</div>
                    <div className="bar" style={{ marginTop: 10 }}><i style={{ width: `${a.usage_rate ?? 0}%`, background: usageColor(a.usage_rate, (a.protection_threshold_pct ?? settings.protection_threshold_pct ?? 95) as number) }} /></div>
                    <div className="stats">
                      <div className="stat"><span>{t("地域")}</span><b>{a.region as string}</b></div>
                      <div className="stat"><span>IP</span><b>{maskIp(a.public_ip)}</b></div>
                      <div className="stat"><span>{t("流量")}</span><b>{fmtGb(a.traffic_used_gb)} / {a.quota_gb ?? 200} GiB{trafficPct(a)}</b></div>
                      <div className="stat"><span>{t("费用")}</span><b>{fmtCost(a.billing_month_cost)} {a.billing_currency || ""}</b></div>
                      <div className="stat"><span>{t("站点")}</span><b>{a.site === "cn" ? t("中国站") : t("国际站")}</b></div>
                    </div>
                    {a.status_detail ? <div className="muted" style={{ marginTop: "auto", paddingTop: 6, color: (a.status_dot === "green" || a.status_dot === "yellow") ? "var(--accent)" : undefined }}>{a.status_detail as string}</div> : null}
                  </div>
                ))}
              </div>
            ) : (
              <table style={{ marginTop: 12 }}><thead><tr><th>{t("服务器")}</th><th>{t("站点")}</th><th>{t("地域")}</th><th>IP</th><th>{t("已用 / 月度流量额度")}</th><th>{t("实时费用")}</th></tr></thead>
                <tbody>{accounts.map((a) => (
                  <tr key={a.id as number}>
                    <td>{dotEl(a.status_dot, a.status_detail || a.status_label || "")}<b>{a.name as string}</b></td>
                    <td>{a.site === "cn" ? t("中国站") : t("国际站")}</td>
                    <td>{a.region as string}</td>
                    <td>{maskIp(a.public_ip)}</td>
                    <td><b>{fmtGb(a.traffic_used_gb)}</b> / {a.quota_gb ?? 200} GiB{trafficPct(a)}</td>
                    <td>{fmtCost(a.billing_month_cost)} {a.billing_currency || ""}</td>
                  </tr>
                ))}</tbody></table>
            )}
          </div>
        </>
      )}

      {tab === "accounts" && (
        <>
          {editingId === "new" && (
            <InstanceForm account={null} settings={settings} count={accounts.length} groups={groups}
              onDone={() => { setEditingId(null); load(); setMsg(t("账户已添加")); }}
              onCancel={() => setEditingId(null)}
              onAutoSaved={() => { load(); setMsg(t("已保存成功")); }} />
          )}
          {accounts.map((a) => (
            <div className="card" key={a.id as number}
              style={{ marginTop: 0, display: "flex", flexDirection: "column", cursor: cardSettingsId === a.id ? "default" : "grab", ...(dragOverId === a.id && dragKind === "account" ? { outline: "2px solid var(--accent)", outlineOffset: 2 } : {}), ...(dragId === a.id && dragKind === "account" ? { opacity: 0.5 } : {}) }}
              {...(cardSettingsId === a.id ? {} : dragProps("account", a.id as number))}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>{dotEl(a.status_dot, a.status_detail || a.status_label || "")}<b style={{ fontSize: 16 }}>{a.name as string}</b> <span className="pill">{a.status_label ? trStatus(a.status_label, lang) : (a.instance_status || t("未知"))}</span>{a.evicted ? <span className="pill gray" style={{ marginLeft: 6 }}>{t("已跳出")}</span> : null}<br />
                <span className="muted">{a.site === "cn" ? t("中国站 cn") : t("国际站 global")} · {a.region as string} · {groupName(a.group_id)}（{groupMode(a.group_id) === "time" ? t("定时轮巡") : t("负载均衡")}）</span></div>
                <button className="btn ghost" title={t("卡片设置")} onClick={() => setCardSettingsId(cardSettingsId === a.id ? null : (a.id as number))}>⚙️</button>
              </div>
              <div className="bar" style={{ marginTop: 10 }}><i style={{ width: `${a.usage_rate ?? 0}%`, background: usageColor(a.usage_rate, (a.protection_threshold_pct ?? settings.protection_threshold_pct ?? 95) as number) }} /></div>
              <div className="stats five">
                <div className="stat"><span>{t("实例")}</span><b>{a.instance_id as string}</b></div>
                <div className="stat"><span>IP</span><b>{maskIp(a.public_ip)}</b></div>
                <div className="stat"><span>{t("流量")}</span><b>{fmtGb(a.traffic_used_gb)} / {a.quota_gb ?? 200} GiB{trafficPct(a)}</b></div>
                <div className="stat"><span>{t("费用")}</span><b>{fmtCost(a.billing_month_cost)} {a.billing_currency || ""}</b></div>
                <div className="stat"><span>{t("阈值")}</span><b>{a.protection_threshold_pct ?? settings.protection_threshold_pct ?? 95}%</b></div>
              </div>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginTop: "auto", paddingTop: 12 }}>
                {a.status_detail ? <div className="muted" style={{ color: (a.status_dot === "green" || a.status_dot === "yellow") ? "var(--accent)" : undefined }}>{a.status_detail as string}</div> : <span />}
                <ActionButtons a={a} />
              </div>
              {cardSettingsId === a.id && <CardSettings a={a} />}
            </div>
          ))}
          <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
            <button className="btn" onClick={() => setEditingId("new")}>{t("+ 添加实例")}</button>
          </div>
        </>
      )}

      {tab === "domains" && (
        <>
        <div className="card">
          <div className="row" style={{ justifyContent: "flex-end", alignItems: "center" }}>
            <button className="btn ghost" title={t("DDNS 设置")} onClick={() => setShowDdns(!showDdns)}>⚙️</button>
          </div>
          <table><thead><tr><th>{t("优先顺序")}</th><th>{t("域名")}</th><th>{t("分组")}</th><th>Zone</th><th>{t("当前记录快照")}</th><th>{t("记录设置")}</th><th>{t("操作")}</th></tr></thead>
            <tbody>{domains.map((d, i) => {
              const cur = accounts.find((a) => a.id === d.current_account_id);
              const editing = editDomId === d.id;
              // 域名行内编辑自动保存：改完即存，不退出编辑态（点叉号退出）；没改过不发请求
              const saveDomEdit = (nm0: string, grp0: string) => {
                const nm = nm0.trim();
                if (!nm) { setMsg(t("请填写域名")); return; }
                const newGid = grp0 === "" ? null : Number(grp0);
                if (nm === d.name && newGid === (d.group_id ?? null)) return;
                const payload: Any = { name: nm, zone: (settings.cloudflare_zone as string) || nm.split(".").slice(-2).join("."), group_id: newGid };
                if (nm !== d.name) { payload.current_account_id = null; payload.current_ip = ""; payload.fallback_active = 0; }
                run(() => put(`/api/domains/${d.id}`, payload), t("已保存成功"));
              };
              return (
              <tr key={d.id as number}><td>{String(i + 1).padStart(2, "0")}<br />
                <button className="btn ghost" disabled={i === 0 || editing} onClick={() => run(async () => { await post("/api/domains/reorder", { order: domains.map((x, j) => (j === i ? domains[i - 1] : j === i - 1 ? domains[i] : x).id) }); }, t("已调整顺序"))}>↑</button>
                <button className="btn ghost" disabled={i === domains.length - 1 || editing} onClick={() => run(async () => { await post("/api/domains/reorder", { order: domains.map((x, j) => (j === i ? domains[i + 1] : j === i + 1 ? domains[i] : x).id) }); }, t("已调整顺序"))}>↓</button></td>
                <td>{editing ? <input value={editDomName} onChange={(e) => setEditDomName(e.target.value)} onBlur={() => saveDomEdit(editDomName, editDomGroup)} style={{ width: "100%" }} /> : <b>{d.name as string}</b>}</td>
                <td>{editing ? (
                  <select value={editDomGroup} onChange={(e) => { const gv = e.target.value; setEditDomGroup(gv); saveDomEdit(editDomName, gv); }}>
                    <option value="">{t("未分组")}</option>
                    {groups.map((g) => {
                      const bound = domains.some((x) => x.id !== d.id && x.group_id === g.id);
                      return <option key={g.id as number} value={g.id as number} disabled={bound}>{g.name as string}{bound ? t("（已绑定域名，不可选）") : ""}</option>;
                    })}
                  </select>
                ) : (<>{groupName(d.group_id)}{d.group_id ? (<><br /><span className="muted">{groupMode(d.group_id) === "time" ? t("定时轮巡") : t("负载均衡")}</span></>) : null}</>)}</td>
                <td>{d.zone as string || t("自动发现")}</td>
                <td>{dotEl(cur?.status_dot, cur?.status_detail || cur?.status_label || "")}　A　{(d.current_ip as string) || "—"}<br /><span className="muted">{d.fallback_active ? t("备用地址生效中") : (cur?.name as string) || ""}{cur ? `（${cur.status_label ? trStatus(cur.status_label, lang) : (cur.instance_status || "")}）` : ""}</span></td>
                <td><span className="pill blue">{t("仅 DNS")}</span><br /><span className="muted">{t("TTL 自动")}</span></td>
                <td>{editing ? (
                  <div className="row" style={{ gap: 6 }}>
                    <button className="icon-btn" title={t("取消")} onClick={() => setEditDomId(null)}>
                      <Ico label={t("取消")}><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></Ico>
                    </button>
                  </div>
                ) : (
                  <div className="row" style={{ gap: 6 }}>
                    <button className="icon-btn" title={t("编辑")} onClick={() => { setEditDomId(d.id as number); setEditDomName(d.name as string); setEditDomGroup(d.group_id ? String(d.group_id) : ""); }}>
                      <Ico label={t("编辑")}><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" /></Ico>
                    </button>
                    <button className="icon-btn danger" title={t("删除")} onClick={() => { if (confirm(t("删除域名 {name}？", { name: d.name }))) run(() => del(`/api/domains/${d.id}`), t("已删除")); }}>
                      <Ico label={t("删除")}><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /><line x1="10" y1="11" x2="10" y2="17" /><line x1="14" y1="11" x2="14" y2="17" /></Ico>
                    </button>
                  </div>
                )}</td></tr>
              ); })}</tbody></table>
          <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
            <button className="btn" onClick={() => { setAddDomName(""); setShowAddDom(true); }}>{t("+ 添加域名")}</button>
          </div>
          {showAddDom && (
            <div className="modal-overlay" onClick={() => setShowAddDom(false)}>
              <div className="modal" onClick={(e) => e.stopPropagation()}>
                <h3>{t("+ 添加域名")}</h3>
                <div style={{ marginTop: 12 }}>
                  <label className="req">{t("域名 *")}</label>
                  <input placeholder={t("域名，如 www.example.xyz")} value={addDomName} onChange={(e) => setAddDomName(e.target.value)} style={{ width: "100%" }} />
                </div>
                <div className="row" style={{ justifyContent: "flex-end", marginTop: 16 }}>
                  <button className="btn ghost" onClick={() => setShowAddDom(false)}>{t("取消")}</button>
                  <button className="btn" onClick={() => {
                    const name = addDomName.trim();
                    if (!name) { setMsg(t("请填写域名")); return; }
                    setShowAddDom(false);
                    run(() => post("/api/domains", { name, zone: (settings.cloudflare_zone as string) || name.split(".").slice(-2).join("."), priority: domains.length }), t("域名已添加"));
                  }}>{t("添加")}</button>
                </div>
              </div>
            </div>
          )}
        </div>
        {showDdns && (
        <div className="card"><h2>{t("DDNS 设置")}</h2>
          <div className="form2" style={{ paddingLeft: "2em" }}>
            <div><label className="req">{t("域名 *")}</label>
              <input type="text" id="cf-zone" defaultValue={(settings.cloudflare_zone as string) || ""} placeholder={t("Zone 名，如 domainname.com")} onBlur={() => saveSettings(ddnsPatch())} /></div>
            <div><label>{t("账户邮箱")}</label>
              <input type="text" id="cf-email" defaultValue={(settings.cloudflare_email as string) || ""} placeholder={t("Cloudflare 账户邮箱")} onBlur={() => saveSettings(ddnsPatch())} /></div>
            <div><label className="req">{t("Cloudflare API Token *")}</label>
              <input type="password" id="cf-token" key={String(settings.cloudflare_configured)} defaultValue={settings.cloudflare_configured ? SECRET_MASK : ""} placeholder={settings.cloudflare_configured ? t("已配置，留空保持不变") : t("未配置，填写 Cloudflare API Token")} onFocus={secretFocus} onBlur={(e) => { secretBlurRestore(e, Boolean(settings.cloudflare_configured)); saveSettings(ddnsPatch()); }} />
              <p className="muted">{settings.cloudflare_configured ? t("已配置。留空保存时保持不变，只有填了新值才更新。") : t("未配置。填写后才能执行 DDNS 更新。")}</p></div>
            <div><label className="req">{t("时区 *")}</label>
              <select defaultValue={String(settings.timezone ?? "Asia/Shanghai")} id="s-timezone" onChange={() => saveSettings(ddnsPatch())}>
                {(timezones.length ? timezones : TZ_FALLBACK).map((tz) => (
                  <option key={tz.value} value={tz.value}>{tz.label}</option>
                ))}
              </select></div>
          </div>
        </div>
        )}
        </>
      )}

      {tab === "groups" && (
        <>
          {groups.map((g) => (
            <GroupCard key={`${g.id}-${g.name}-${g.interval_hours}-${g.custom_interval_minutes}-${g.interval_start_time}-${g.switch_diff_pct}-${g.switch_diff_gb}-${g.exclusion_action ?? ""}-${g.recovery_action ?? ""}`}
              g={g} settings={settings} accounts={accounts} domains={domains} dotEl={dotEl} run={run} setMsg={setMsg}
              dragProps={dragProps("group", g.id as number)} dragOver={dragOverId === g.id && dragKind === "group"} dragging={dragId === g.id && dragKind === "group"} />
          ))}
          {showNewGroup && (
            <div className="instance-form" style={{ marginBottom: 12 }}>
              <label className="req">{t("分组名 *")}</label>
              <input value={newGroupName} onChange={(e) => setNewGroupName(e.target.value)} placeholder={t("如 分组C")} />
              <label className="lbl" style={{ marginTop: 12 }}>{t("切换模式")}</label>
              <Seg value={newGroupMode} options={[
                { value: "time", label: t("定时轮巡") },
                { value: "load_balance", label: t("负载均衡") },
                { value: "dynamic", label: t("动态切换") },
              ]} onChange={setNewGroupMode} />
              <label className="lbl" style={{ marginTop: 12 }}>{t("选择域名")}</label>
              <select value={newGroupDomain} onChange={(e) => setNewGroupDomain(e.target.value)} style={{ width: "100%" }}>
                <option value="">{t("暂不选择域名")}</option>
                {domains.map((d) => {
                  const boundGroup = d.group_id ? groups.find((g) => g.id === d.group_id) : null;
                  return <option key={d.id as number} value={d.id as number} disabled={Boolean(boundGroup)}>{d.name as string}{boundGroup ? t("（已被 {group} 选取）", { group: boundGroup.name }) : ""}</option>;
                })}
              </select>
              <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
                <button className="btn ghost" onClick={() => { setShowNewGroup(false); setNewGroupName(""); setNewGroupMode(""); setNewGroupDomain(""); }}>{t("取消")}</button>
                <button className="btn" onClick={() => {
                  if (!newGroupName.trim()) { setMsg(t("请填写分组名")); return; }
                  if (!newGroupMode) { setMsg(t("请选择切换模式")); return; }
                  run(() => post("/api/groups", { name: newGroupName.trim(), switch_mode: newGroupMode, ...(newGroupDomain ? { domain_id: Number(newGroupDomain) } : {}) }), t("分组已创建"));
                  setShowNewGroup(false); setNewGroupName(""); setNewGroupMode(""); setNewGroupDomain("");
                }}>{t("创建分组")}</button>
              </div>
            </div>
          )}
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button className="btn" onClick={() => setShowNewGroup(!showNewGroup)}>{t("+ 新建分组")}</button>
          </div>
        </>
      )}

      {tab === "config" && subTab === "settings" && (
        <>
          <div className="card"><h2>{t("参数设置")}</h2><p className="muted">{t("账户未配置覆盖值时，使用以下全局设置。")}</p>
            <div className="notice" style={{ marginBottom: 0 }}><b>{t("切换模式按分组设置，请到「分组管理」选择。")}</b>
              {t("一个分组只选一种模式（定时轮巡 / 负载均衡），不同分组可以选不同模式。这里的时间/差值参数是")}
              <b>{t("新建分组的默认值")}</b>
              {t("，分组里未填的项继承这里的值。旧的全局切换模式只作默认值与无分组 legacy 回退，不再直接控制分组。")}
              <button className="btn" style={{ marginLeft: 8 }} onClick={() => setTab("groups")}>{t("去分组管理")}</button>
            </div>
            <div className="form2">
              <div><label className="req">{t("月度流量额度 *（GiB）")}</label><input defaultValue={String(settings.quota_gb ?? 200)} id="s-quota" onBlur={() => saveParamSettings()} /></div>
              <div><label className="req">{t("流量保护阈值 *（%）")}</label><input defaultValue={String(settings.protection_threshold_pct ?? 95)} id="s-prot" onBlur={() => saveParamSettings()} /></div>
            </div>
            <div className="form2">
              <div><TwoLineLabel parent="负载均衡" child="切换阀值 *" req />
                <div style={{ paddingLeft: "2em" }}><Seg value={sDiffMode} options={[
                  { value: "percent", label: t("百分比（%）") },
                  { value: "gb", label: t("固定流量（GB）") },
                ]} onChange={(v) => { setSDiffMode(v); saveParamSettings({ switch_diff_mode: v }); }} /></div></div>
              {sDiffMode === "percent" ? (
                <div><TwoLineLabel parent="负载均衡" child="切换阀值 *（%）" req /><div style={{ paddingLeft: "2em" }}><input defaultValue={String(settings.switch_diff_pct ?? 10)} id="s-diff" onBlur={() => saveParamSettings()} /></div></div>
              ) : (
                <div><TwoLineLabel parent="负载均衡" child="切换阀值（GB）" /><div style={{ paddingLeft: "2em" }}><input defaultValue={String(settings.switch_diff_gb ?? 10)} id="s-diffgb" onBlur={() => saveParamSettings()} /></div></div>
              )}
            </div>
            <div style={{ color: "var(--accent)", fontSize: 12, marginTop: 6, paddingLeft: "2em" }}>{t("不适用定时轮巡。")}</div>
            <div style={{ marginTop: 12, paddingLeft: "2em" }}>
              <label className="lbl">{t("阈值动作")}</label>
              <Seg value={sExcl} options={[
                { value: "none", label: t("踢出DDNS") },
                { value: "stop_charging", label: t("节省停机") },
                { value: "stop", label: t("停机") },
              ]} onChange={(v) => { setSExcl(v); saveParamSettings({ exclusion_action: v }); }} />
            </div>
            <div style={{ marginTop: 12, paddingLeft: "2em" }}>
              <label className="lbl">{t("阈值恢复")}</label>
              <Seg value={sRecovery} options={[
                { value: "start", label: t("恢复时拉起（默认）") },
                { value: "keep_stopped", label: t("恢复入池但保持停机") },
              ]} onChange={(v) => { setSRecovery(v); saveParamSettings({ threshold_recovery_action: v }); }} />
            </div>
            <h3 style={{ marginTop: 20 }}>{t("定时轮巡")}</h3>
            <div>
            <div className="form2">
              <div><label className="lbl" style={{ paddingLeft: "2em" }}>{t("轮巡周期")}</label>
                <div style={{ paddingLeft: "2em" }}><Seg value={sInterval} options={[
                  { value: "1", label: t("1 小时") }, { value: "2", label: t("2 小时") },
                  { value: "3", label: t("3 小时") }, { value: "4", label: t("4 小时") },
                  { value: "6", label: t("6 小时") }, { value: "12", label: t("12 小时") },
                  { value: "24", label: t("24 小时") }, { value: "0", label: t("自定义") },
                ]} onChange={(v) => { setSInterval(v); saveParamSettings({ interval_hours: v }); }} /></div></div>
              {sInterval === "0" && (
              <div onBlur={() => saveParamSettings()}><label className="lbl" style={{ paddingLeft: "2em" }}>{t("自定义间隔")}</label>
                <div style={{ paddingLeft: "2em" }}><div className="row" style={{ alignItems: "center" }}>
                  <input type="number" min={0} value={sCustVal} onChange={(e) => setSCustVal(e.target.value)} style={{ width: 120 }} />
                  <Seg value={sCustUnit} options={[
                    { value: "minute", label: t("分钟") },
                    { value: "hour", label: t("小时") },
                    { value: "day", label: t("天") },
                  ]} onChange={changeCustUnit} />
                </div></div></div>)}
            </div>
            <div style={{ marginTop: 12 }}>
              <label className="req" style={{ fontSize: 16 }}>{t("API 刷新时间 *（分钟）")}</label>
              <div style={{ paddingLeft: "2em" }}><Seg value={sRefresh} options={[
                { value: "5", label: t("5 分钟") }, { value: "10", label: t("10 分钟") },
                { value: "15", label: t("15 分钟") }, { value: "30", label: t("30 分钟") },
                { value: "60", label: t("60 分钟") },
                { value: "0", label: t("不刷新") },
              ]} onChange={(v) => { setSRefresh(v); saveParamSettings({ api_refresh_minutes: v, scheduler_paused: v === "0" ? "1" : "0" }); }} /></div>
            </div>
            </div>
          </div>
        </>
      )}

      {tab === "config" && subTab === "other" && (
        <>
          <SiteInfoCard settings={settings} setMsg={setMsg} load={load} />
          <div className="card"><h2>{t("货币结算")}</h2>
            <div style={{ paddingLeft: "2em" }}>
            <label className="lbl">{t("结算货币")}</label>
            <Seg value={sCurrency} options={[
              { value: "USD", label: "USD" },
              { value: "CNY", label: "CNY" },
            ]} onChange={(v) => { setSCurrency(v); saveParamSettings({ settlement_currency: v }); }} />
            <div style={{ marginTop: 8 }}>
              <label className="lbl">{t("汇率")}</label>
              <div>1 USD = {String(settings.exchange_rate_usd_cny ?? "6.6719")} CNY</div>
            </div>
            </div>
          </div>
          <div className="card"><h2>{t("主题设置")}</h2>
            <div style={{ paddingLeft: "2em" }}>
            <Seg value={theme} options={[
              { value: "light", label: t("亮色") },
              { value: "dark", label: t("暗色") },
              { value: "system", label: t("跟随系统") },
            ]} onChange={(v) => setTheme(v)} />
            <h3 style={{ marginTop: 16 }}>🎨 {t("主题风格")}</h3>
            <div className="theme-grid" style={{ paddingLeft: "2em" }}>
              {ACCENTS.map((a) => (
                <button key={a.id} type="button" className={accent === a.id ? "theme-card sel" : "theme-card"} onClick={() => setAccent(a.id)}>
                  <span className="theme-swatch" style={{ background: a.gradient }}>
                    {accent === a.id && <span className="theme-check">✓</span>}
                  </span>
                  <span className="theme-name">{a.zh}</span>
                  <span className="theme-en">{a.en}</span>
                </button>
              ))}
            </div>
            </div>
          </div>
        </>
      )}

      {tab === "config" && subTab === "account" && (
        <>
          <LoginSecurity settings={settings} saveSettings={saveSettings} setMsg={setMsg} />
          <BackupCard setMsg={setMsg} load={load} />
        </>
      )}

      {tab === "config" && subTab === "notify" && (
        <>
          <ChannelCard kind="email" settings={settings} drEnabled={drEnabled} drTime={drTime}
            setDrEnabled={setDrEnabled} setDrTime={setDrTime} saveSettings={saveSettings} run={run} />
          <ChannelCard kind="telegram" settings={settings} drEnabled={drEnabled} drTime={drTime}
            setDrEnabled={setDrEnabled} setDrTime={setDrTime} saveSettings={saveSettings} run={run} />
          <div className="card"><h2>{t("通知事件")}</h2>
            <p className="muted">{t("应用于所有已启用渠道。关闭期间仍跟踪状态，重新开启不补发历史事件。")}</p>
            {(["traffic_protection", "dns_change", "fallback", "exclusion", "error", "recovery"] as const).map((k) => {
              const label = ({ traffic_protection: t("流量保护"), dns_change: t("DNS 指向变化"), fallback: t("备用地址启用及恢复"), exclusion: t("排除操作完成"), error: t("查询及操作异常"), recovery: t("故障恢复") } as Any)[k] as string;
              return (
                <div className="row" key={k} style={{ padding: "6px 0 6px 2em" }}>
                  <span>{label}</span><span className="spacer" />
                  <Toggle checked={settings[`notify_${k}`] === "1"} label={label}
                    onChange={(v) => saveSettings({ [`notify_${k}`]: v ? "1" : "0" })} />
                </div>
              );
            })}
            <p className="muted">{t("持续故障仅在首次发生和恢复时通知。测试使用已保存的配置，调度暂停时也可发送。")}</p>
          </div>
        </>
      )}

      {tab === "config" && subTab === "logs" && (
        <div className="card"><h2>{t("日志统计")}</h2>
          <div className="row" style={{ marginBottom: 12 }}>
            <button className="btn ghost" onClick={() => loadLogs()}>{t("刷新日志")}</button>
          </div>
          {logs.length === 0 && <p className="muted">{t("暂无日志。")}</p>}
          {logs.map((e) => (
            <div key={e.id as number} className="row" style={{ padding: "6px 0" }}>
              <span>•</span><span>{e.title as string}</span><span className="muted">{e.detail as string}</span>
              <span className="spacer" />
              <span className="muted">{e.created_at ? new Date((e.created_at as number) * 1000).toLocaleString(lang === "zh" ? "zh-CN" : "en-US", { hour12: false }) : ""}</span>
            </div>
          ))}
        </div>
      )}
      <footer style={{ marginTop: 24, padding: "12px 0", textAlign: "center" }}>
        <a href="https://github.com/gugetang/Alicloud_ECS_CDTPanel" target="_blank" rel="noreferrer" style={{ color: "var(--muted)", fontSize: 13 }}>GitHub: gugetang/Alicloud_ECS_CDTPanel</a>
      </footer>
    </div>
  );
}
