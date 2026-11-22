import { useEffect, useRef, useState } from "react";
import { get, post, put, del, getToken, setToken } from "./api";
import { getLang, translate, trStatus, useI18n } from "./i18n";

type Tab = "dash" | "accounts" | "groups" | "config" | "logs";
type SubTab = "settings" | "domains" | "notify";
type Any = Record<string, any>;

const TABS: [Tab, string][] = [
  ["dash", "运行概览"], ["accounts", "账户管理"],
  ["groups", "分组管理"], ["config", "设置"], ["logs", "日志"],
];

// 设置页内的子标签（从左到右）：全局设置、域名管理、通知设置
const SUB_TABS: [SubTab, string][] = [
  ["settings", "全局设置"], ["domains", "域名管理"], ["notify", "通知设置"],
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

// 阈值动作三选一：全局设置页与实例配置表单共用同一组选项与文案。
// 内部值沿用 exclusion_action：none=踢出DDNS（默认，不停机）/
// stop_charging=节省停机 / stop=停机。
const EXCL_OPTIONS = [
  { value: "none", title: "踢出DDNS（默认，不停机）", sub: "只跳出 DDNS，继续跑" },
  { value: "stop_charging", title: "节省停机", sub: "切完 DNS 后节省停机" },
  { value: "stop", title: "停机", sub: "切完 DNS 后停机" },
];

const exclLabel = (v: string | undefined | null) =>
  translate(getLang(), (EXCL_OPTIONS.find((o) => o.value === (v || "none"))?.title as string) || "踢出DDNS（默认，不停机）");

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

// 按键式选择（全站统一）：选中蓝框、未选灰框，见 styles.css 的 .seg。
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

// 实例配置表单：服务器名排在最上面；分组不出现（新建默认进默认分组，
// 编辑保持原分组）；必填项标签加粗（label.req）。站点/阈值动作/恢复方式/
// 日报参与都用按键式选择。
function InstanceForm({ account, settings, count, onDone, onCancel, onAutoSaved }: {
  account: Any | null; settings: Any; count: number;
  onDone: () => void; onCancel: () => void; onAutoSaved?: () => void;
}) {
  const { t } = useI18n();
  const isNew = !account;
  const [name, setName] = useState((account?.name as string) || "");
  const [akId, setAkId] = useState((account?.access_key_id as string) || "");
  const [akSecret, setAkSecret] = useState("");
  const [instanceId, setInstanceId] = useState((account?.instance_id as string) || "");
  const [site, setSite] = useState((account?.site as string) || "global");
  const [region, setRegion] = useState((account?.region as string) || "cn-hongkong");
  const [regionCustom, setRegionCustom] = useState(false);
  const [regionCustomVal, setRegionCustomVal] = useState("");
  const [quota, setQuota] = useState(String(account?.quota_gb ?? 200));
  const [schedEnabled, setSchedEnabled] = useState(Boolean(account?.daily_schedule_enabled));
  const [onTime, setOnTime] = useState((account?.daily_on_time as string) || "08:00");
  const [offTime, setOffTime] = useState((account?.daily_off_time as string) || "20:00");
  const [report, setReport] = useState((account?.daily_report as string) || "follow");
  const [excl, setExcl] = useState((account?.exclusion_action as string) || "");
  const [recovery, setRecovery] = useState((account?.recovery_action as string) || "");
  const [regions, setRegions] = useState<{ region_id: string; local_name: string }[]>(BUILTIN_REGIONS);
  const [ownedIds, setOwnedIds] = useState<Set<string>>(new Set());
  const [regionsNote, setRegionsNote] = useState(t("内置常用地域"));
  const [formMsg, setFormMsg] = useState("");
  const [formBusy, setFormBusy] = useState(false);

  const loadRegions = async (withAk: boolean) => {
    // 优先调发现接口（已有实例所在地域分组 + 全部可用地域）；失败回退 GET 列表，再失败用内置列表
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
      setRegionsNote(t("地域来自阿里云 DescribeRegions；“已有实例”组为该账号下已有实例所在地域"));
    } catch {
      try {
        const q = account?.id
          ? `/api/aliyun/regions?account_id=${account.id}&site=${site}`
          : (akId && akSecret
            ? `/api/aliyun/regions?site=${site}&access_key_id=${encodeURIComponent(akId)}&access_key_secret=${encodeURIComponent(akSecret)}`
            : "");
        if (!q) throw new Error("no-credentials");
        const r = await get(q) as Any;
        setRegions(((r.regions as Any[]) || BUILTIN_REGIONS) as { region_id: string; local_name: string }[]);
        setOwnedIds(new Set());
        setRegionsNote(t("地域来自阿里云 DescribeRegions"));
      } catch {
        setRegions(BUILTIN_REGIONS);
        setOwnedIds(new Set());
        setRegionsNote(t("接口未取到，已用内置列表"));
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
      group_id: account?.group_id ?? 1,
      daily_schedule_enabled: schedEnabled ? 1 : 0, daily_on_time: onTime,
      daily_off_time: offTime, daily_report: report,
      exclusion_action: excl || "", recovery_action: recovery || "",
    };
    if (akSecret) payload.access_key_secret = akSecret;  // 留空保持不变，绝不清空
    try {
      if (isNew) { payload.priority = count; await post("/api/accounts", payload); onDone(); }
      else { await put(`/api/accounts/${account.id}`, payload); onAutoSaved?.(); }
    } catch { /* 自动保存失败不打扰，等用户改完再说 */ }
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
        <div><label className="req">{t("AccessKey Secret *")}</label><input type="password" value={akSecret} onChange={(e) => setAkSecret(e.target.value)} onBlur={saveAuto} placeholder={account?.has_secret ? t("已配置（留空保持不变）") : t("未配置，请填写")} /></div>
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
          <p className="muted">{regionsNote}</p>
          <div className="row">
            <button className="btn ghost" onClick={autoDiscover} disabled={formBusy}>{t("自动发现")}</button>
            <button className="btn ghost" onClick={() => loadRegions(true)} disabled={formBusy}>{t("重新拉取地域列表")}</button>
          </div>
          <p className="muted">{t("“自动发现”按实例 ID 查出该实例真实所在地域并填入；查不到会报错，不会静默填错。")}</p></div>
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
        <p className="muted">{t("开启后每天在设定时间开机/关机；错过时间窗口（重启/延迟）时在 1 小时内补偿一次。阈值踢出优先于定时开机：被踢出的实例到点也不拉起；关机窗口内恢复拉起也不执行。")}</p>
      </div>
      <div style={{ marginTop: 12 }} onBlur={saveAuto}>
        <label className="lbl">{t("参与日报推送")}</label>
        <Seg value={report} options={[
          { value: "follow", label: t("跟随全局") },
          { value: "on", label: t("单独开启") },
          { value: "off", label: t("单独关闭") },
        ]} onChange={setReport} />
        <p className="muted">{t("跟随全局：按全局每日财报开关；单独开启：即使全局关了也推这台；单独关闭：即使全局开了也不推这台。")}</p>
      </div>
      <div style={{ marginTop: 12 }} onBlur={saveAuto}>
        <label className="lbl">{t("阈值动作")}</label>
        <Seg value={excl} options={[
          { value: "", label: t("跟随全局") },
          { value: "none", label: t("踢出DDNS") },
          { value: "stop_charging", label: t("节省停机") },
          { value: "stop", label: t("停机") },
        ]} onChange={setExcl} />
        <p className="muted">{t("跟随全局：按全局设置（当前：{label}）。踢出DDNS 默认不停机，只跳出 DDNS 继续跑；停机/节省停机都在切完 DNS 之后。", { label: exclLabel(settings.exclusion_action) })}</p>
      </div>
      <div style={{ marginTop: 12 }} onBlur={saveAuto}>
        <label className="lbl">{t("阈值恢复")}</label>
        <Seg value={recovery} options={[
          { value: "", label: t("跟随全局") },
          { value: "start", label: t("恢复时拉起") },
          { value: "keep_stopped", label: t("恢复入池但保持停机") },
        ]} onChange={setRecovery} />
        <p className="muted">{t("跟随全局：按全局设置（当前：{label}）。", { label: settings.threshold_recovery_action === "keep_stopped" ? t("恢复入池但保持停机") : t("恢复时拉起") })}</p>
      </div>
      {formMsg && <p style={{ color: "#f87171", marginTop: 12 }}>{formMsg}</p>}
      <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
        <button className="btn ghost" onClick={onCancel}>{t("取消")}</button>
      </div>
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
        <p className="muted">{t("首次登录，请填写管理员信息。之后可用密码、指纹、Google 或 GitHub 登录。")}</p>
        <label className="req">{t("管理员名称 *")}</label>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("如 管理员")} />
        <label className="req">{t("登录密码 *")}</label>
        <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder={t("至少 6 位")} />
        <label className="req">{t("确认密码 *")}</label>
        <input type="password" value={pw2} onChange={(e) => setPw2(e.target.value)} placeholder={t("再输一次")} />
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
        <p className="muted">{info.admin_name ? t("管理员：{name}", { name: info.admin_name as string }) : t("请输入管理员密码登录。")}</p>
        <label className="req">{t("登录密码 *")}</label>
        <input type="password" value={pw} onChange={(e) => setPw(e.target.value)}
          placeholder={t("管理员密码")} onKeyDown={(e) => { if (e.key === "Enter") loginPw(); }} />
        {msg && <p style={{ color: "#f87171", marginTop: 12 }}>{msg}</p>}
        <button className="btn" style={{ width: "100%", marginTop: 16 }} onClick={loginPw} disabled={busy}>{t("登录")}</button>
        {info.webauthn && (
          <button className="oauth-btn" onClick={loginWebauthn} disabled={busy || !webauthnSupported}>
            {t("指纹登录")}{webauthnSupported ? "" : t("（当前浏览器不支持）")}
          </button>
        )}
        <button className="oauth-btn" disabled={!info.oauth?.google}
          onClick={() => { window.location.href = "/api/auth/oauth/google/login"; }}>
          {t("使用 Google 登录")}{info.oauth?.google ? "" : t("（未配置）")}
        </button>
        <button className="oauth-btn" disabled={!info.oauth?.github}
          onClick={() => { window.location.href = "/api/auth/oauth/github/login"; }}>
          {t("使用 GitHub 登录")}{info.oauth?.github ? "" : t("（未配置）")}
        </button>
        <p className="muted" style={{ marginTop: 12 }}>
          {t("Google / GitHub 登录需先在全局设置“登录方式”里填入对应 OAuth 应用的 Client ID / Secret。")}
        </p>
      </div>
    </div>
  );
}

// ---------------- 分组卡片：模式按键 + 参数表单（无 prompt） ----------------
function GroupCard({ g, settings, accounts, domains, dotEl, run }: {
  g: Any; settings: Any; accounts: Any[]; domains: Any[];
  dotEl: (dot: string | undefined, title?: string) => JSX.Element;
  run: (fn: () => Promise<unknown>, ok: string) => Promise<void>;
}) {
  const { lang, t } = useI18n();
  const [interval, setInterval] = useState(String(g.interval_hours ?? settings.interval_hours ?? 6));
  const [diffMode, setDiffMode] = useState((g.switch_diff_mode ?? settings.switch_diff_mode ?? "percent") as string);
  const [diffPct, setDiffPct] = useState(String(g.switch_diff_pct ?? settings.switch_diff_pct ?? 10));
  const [diffGb, setDiffGb] = useState(String(g.switch_diff_gb ?? settings.switch_diff_gb ?? 10));
  const [fixed, setFixed] = useState((g.fixed_switch_time ?? settings.fixed_switch_time ?? "") as string);
  const [nStart, setNStart] = useState((g.night_window_start ?? settings.night_window_start ?? "00:00") as string);
  const [nEnd, setNEnd] = useState((g.night_window_end ?? settings.night_window_end ?? "04:00") as string);
  // 分组设置区是否展开（卡片右上角 ⚙ 开关，默认展开）
  const [showSettings, setShowSettings] = useState(true);

  const members = accounts.filter((a) => (a.group_id || 1) === g.id);
  const dom = domains.find((d) => (d.group_id || 1) === g.id);
  const cur = dom ? accounts.find((a) => a.id === dom.current_account_id) : null;

  // 分组参数自动保存：改动即存，不用点保存
  const savePatch = (patch: Any) => run(() => put(`/api/groups/${g.id}`, patch), t("分组参数已保存"));

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row"><b>{g.name as string}</b>
        <span className="pill">{g.switch_mode === "time" ? t("定时轮巡") : t("负载均衡")}</span>
        <span className="spacer" />
        <button className="btn ghost" title={t("分组设置")} onClick={() => setShowSettings(!showSettings)}>⚙️</button>
        <button className="icon-btn danger" title={t("删除")} onClick={() => { if (confirm(t("删除分组 {name}？须先移走其账户/域名。", { name: g.name }))) run(() => del(`/api/groups/${g.id}`), t("已删除")); }}>
          <Ico label={t("删除")}><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /><line x1="10" y1="11" x2="10" y2="17" /><line x1="14" y1="11" x2="14" y2="17" /></Ico>
        </button>
      </div>
      {showSettings && (<>
      <div style={{ marginTop: 12 }}>
        <label className="lbl">{t("切换模式")}</label>
        <Seg value={g.switch_mode as string} options={[
          { value: "time", label: t("定时轮巡") },
          { value: "load_balance", label: t("负载均衡") },
        ]} onChange={(m) => run(() => put(`/api/groups/${g.id}`, { switch_mode: m }), t("已切换模式"))} />
      </div>
      {g.switch_mode === "time" ? (
        <div style={{ marginTop: 12 }}>
          <div className="form2">
            <div><label className="lbl">{t("间隔切换周期")}</label>
              <Seg value={interval} options={[
                { value: "1", label: t("1 小时") }, { value: "2", label: t("2 小时") },
                { value: "3", label: t("3 小时") }, { value: "4", label: t("4 小时") },
                { value: "6", label: t("6 小时") }, { value: "12", label: t("12 小时") },
                { value: "24", label: t("24 小时") },
              ]} onChange={(v) => { setInterval(v); savePatch({ interval_hours: Number(v) || 6 }); }} /></div>
            <div><label className="lbl">{t("固定每日切换时刻")}</label><input type="time" value={fixed} onChange={(e) => setFixed(e.target.value)} onBlur={() => savePatch({ fixed_switch_time: fixed || "" })} /></div>
            <div><label className="lbl">{t("夜间窗口开始")}</label><input type="time" value={nStart} onChange={(e) => setNStart(e.target.value)} onBlur={() => savePatch({ night_window_start: nStart || "00:00" })} /></div>
            <div><label className="lbl">{t("夜间窗口结束")}</label><input type="time" value={nEnd} onChange={(e) => setNEnd(e.target.value)} onBlur={() => savePatch({ night_window_end: nEnd || "04:00" })} /></div>
          </div>
          <p className="muted">{t("未填的项继承全局设置。夜间窗口内每晚切换一次至使用率更低的候选；间隔按上次切换锚点每 N 小时切一次。")}</p>
        </div>
      ) : (
        <div style={{ marginTop: 12 }}>
          <label className="lbl">{t("切换阀值")}</label>
          <Seg value={diffMode} options={[
            { value: "percent", label: t("百分比（%）") },
            { value: "gb", label: t("固定流量（GB）") },
          ]} onChange={(m) => { setDiffMode(m); savePatch({ switch_diff_mode: m }); }} />
          <div className="form2">
            {diffMode === "percent" ? (
              <div><label className="lbl">{t("切换阀值（%）")}</label><input type="number" min={1} value={diffPct} onChange={(e) => setDiffPct(e.target.value)} onBlur={() => savePatch({ switch_diff_pct: Number(diffPct) || 10 })} /></div>
            ) : (
              <div><label className="lbl">{t("切换阀值（GB）")}</label><input type="number" min={1} value={diffGb} onChange={(e) => setDiffGb(e.target.value)} onBlur={() => savePatch({ switch_diff_gb: Number(diffGb) || 10 })} /></div>
            )}
          </div>
          <p className="muted">{t("负载均衡：% 模式差值 ≥ 阈值切，GB 模式差值 ≥ 阈值 GB 切。未填的项继承全局设置。")}</p>
        </div>
      )}
      </>)}
      <h3 style={{ marginTop: 12 }}>{t("成员实例（参与 DDNS，可多选）")}</h3>
      <p className="muted">{t("点一下选中 / 取消，被选中的实例参与 DDNS 域名分配；名称前的点是当前状态。")}</p>
      {members.length === 0 && <p className="muted">{t("暂无成员，去「账户管理」添加并选本分组。")}</p>}
      <div className="seg">
        {members.map((a) => {
          const on = a.ddns_enabled == null ? true : Boolean(a.ddns_enabled);
          return (
            <button key={a.id as number} className={on ? "seg-on" : ""}
              title={`${a.status_label ? trStatus(a.status_label, lang) : (a.instance_status || t("未知"))}${a.public_ip ? " · " + (a.public_ip as string) : ""}${a.usage_rate != null ? " · " + a.usage_rate + "%" : ""}`}
              onClick={() => run(() => put(`/api/accounts/${a.id}`, { ddns_enabled: on ? 0 : 1 }), on ? t("已取消 {name} 参与 DDNS", { name: a.name }) : t("{name} 已参与 DDNS", { name: a.name }))}>
              {dotEl(a.status_dot, a.status_detail || a.status_label || "")}
              {a.name as string}
            </button>
          );
        })}
      </div>
      <h3 style={{ marginTop: 12 }}>{t("绑定域名（最多 1 个）")}</h3>
      {dom ? (
        <div className="row"><b>{dom.name as string}</b><span className="muted">{t("当前指向")}</span>
          <span>{(dom.current_ip as string) || t("未分配")}</span>
          <span className="muted">{cur ? cur.name as string : (dom.fallback_active ? t("备用地址生效中") : "")}</span></div>
      ) : <p className="muted">{t("未绑定域名，去「域名管理」添加并选本分组。")}</p>}
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
      return saveSettings({
        email_smtp_host: (document.getElementById("em-host") as HTMLInputElement)?.value ?? "",
        email_smtp_port: (document.getElementById("em-port") as HTMLInputElement)?.value ?? "465",
        email_user: (document.getElementById("em-user") as HTMLInputElement)?.value ?? "",
        email_to: (document.getElementById("em-to") as HTMLInputElement)?.value ?? "",
        ...(((document.getElementById("em-pass") as HTMLInputElement)?.value || "") ? { email_password: (document.getElementById("em-pass") as HTMLInputElement).value } : {}),
      });
    } else {
      return saveSettings({
        telegram_chat_id: (document.getElementById("tg-chat") as HTMLInputElement)?.value ?? "",
        ...(((document.getElementById("tg-token") as HTMLInputElement)?.value || "") ? { telegram_token: (document.getElementById("tg-token") as HTMLInputElement).value } : {}),
      });
    }
  };
  // 通知渠道自动保存：配置改完失焦即存，财报开关/时间改完即存
  const saveAll = async () => {
    await saveConfig();
    await saveSettings({ daily_report_enabled: drEnabled ? "1" : "0", daily_report_time: drTime || "00:00" });
  };
  return (
    <div className="card">
      <div className="row"><h3 style={{ margin: 0 }}>{title}</h3><span className="spacer" />
        <Toggle checked={enabled} label={t("{title}启用", { title })}
          onChange={(v) => saveSettings(isEmail ? { email_enabled: v ? "1" : "0" } : { telegram_enabled: v ? "1" : "0" })} />
      </div>
      {isEmail ? (
        <div style={{ marginTop: 12 }}>
          <label>{t("SMTP 服务器")}</label><input id="em-host" defaultValue={String(settings.email_smtp_host ?? "")} placeholder="smtp.example.com" onBlur={saveAll} />
          <label>{t("SMTP 端口")}</label><input id="em-port" defaultValue={String(settings.email_smtp_port ?? 465)} onBlur={saveAll} />
          <label>{t("发件邮箱")}</label><input id="em-user" defaultValue={String(settings.email_user ?? "")} placeholder="sender@example.com" onBlur={saveAll} />
          <label>{t("SMTP 密码")}</label><input type="password" id="em-pass" placeholder={t("留空保持不变")} onBlur={saveAll} />
          <label>{t("收件邮箱")}</label><input id="em-to" defaultValue={String(settings.email_to ?? "")} placeholder="receiver@example.com" onBlur={saveAll} />
        </div>
      ) : (
        <div style={{ marginTop: 12 }}>
          <label>Bot Token</label><input type="password" id="tg-token" placeholder={t("留空保持不变")} onBlur={saveAll} />
          <label>Chat ID</label><input id="tg-chat" defaultValue={String(settings.telegram_chat_id ?? "")} placeholder={t("如 123456789")} onBlur={saveAll} />
          <p className="muted">{t("Token 留空保存时保持不变，只有填了新值才更新。")}</p>
        </div>
      )}
      <div style={{ marginTop: 12 }}>
        <label>{t("每日财报推送")}</label>
        <div className="row"><span>{t("启用每日财报推送")}</span><span className="spacer" />
          <Toggle checked={drEnabled} label={t("启用每日财报推送")} onChange={(v) => { setDrEnabled(v); saveSettings({ daily_report_enabled: v ? "1" : "0", daily_report_time: drTime || "00:00" }); }} /></div>
        <label>{t("推送时间")}</label><input type="time" value={drTime} onChange={(e) => setDrTime(e.target.value)} onBlur={saveAll} />
        <p className="muted">{t("每天这个时间推送一次，只推已启用的渠道。改时间后自动保存；若改到的时间当天已过，等明天再推，不会当天补推。")}</p>
      </div>
      <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
        <button className="btn ghost" onClick={() => run(() => post("/api/notify/test", { event: "error", channel: kind }), t("测试通知已发送到{title}", { title }))}>{t("发送测试通知")}</button>
        <button className="btn ghost" onClick={() => run(() => post("/api/notify/test-daily", { channel: kind }), t("测试财报已发送到{title}", { title }))}>{t("发送测试财报")}</button>
        <button className="btn" onClick={() => run(() => post("/api/notify/send-daily", { channel: kind }), t("日报已手动推送到{title}", { title }))}>{t("立即推送日报")}</button>
      </div>
    </div>
  );
}

// ---------------- 登录与安全卡片（全局设置页）：改密码 / 注册指纹 / OAuth 配置 ----------------
function LoginSecurity({ adminName, saveSettings, settings, setMsg }: {
  adminName: string; settings: Any;
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
    if (gsec) patch.google_oauth_client_secret = gsec;
    saveSettings(patch);
    setMsg(t("Google 登录已保存"));
  };

  const saveGithubOAuth = () => {
    const patch: Any = {
      github_oauth_client_id: (document.getElementById("oauth-github-id") as HTMLInputElement)?.value ?? "",
    };
    const hsec = (document.getElementById("oauth-github-secret") as HTMLInputElement)?.value || "";
    if (hsec) patch.github_oauth_client_secret = hsec;
    saveSettings(patch);
    setMsg(t("GitHub 登录已保存"));
  };

  return (
    <div className="card"><h2>{t("登录与安全")}</h2>
      <p className="muted">{t("管理员：{name}。登录方式：密码、指纹、Google、GitHub。", { name: adminName || t("管理员") })}</p>
      <h3 style={{ marginTop: 12 }}>{t("修改密码")}</h3>
      <div className="form2">
        <div><label>{t("当前密码")}</label><input type="password" value={curPw} onChange={(e) => setCurPw(e.target.value)} placeholder={t("首次设置留空")} /></div>
        <div><label>{t("新密码")}</label><input type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} placeholder={t("至少 6 位")} /></div>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn" onClick={changePw} disabled={busy}>{t("修改密码")}</button>
      </div>
      <h3 style={{ marginTop: 20 }}>{t("指纹登录")}</h3>
      <p className="muted">{t("在本设备注册指纹（或安全密钥），下次登录页可直接用指纹登录。需 HTTPS 或 localhost 访问。")}</p>
      <div className="row">
        <button className="btn" onClick={registerFingerprint} disabled={busy}>{t("注册指纹")}</button>
      </div>
      {creds.map((c) => (
        <div key={c.credential_id as string} className="row" style={{ marginTop: 8 }}>
          <span>{c.label as string || t("指纹")}</span>
          <span className="muted">{new Date((c.created_at as number) * 1000).toLocaleDateString(lang === "zh" ? "zh-CN" : "en-US")}</span>
          <span className="spacer" />
          <button className="icon-btn danger" title={t("删除")} onClick={async () => { if (!confirm(t("删除指纹 {label}？", { label: (c.label as string) || t("指纹") }))) return; await del(`/api/auth/webauthn/${c.credential_id}`); await loadCreds(); }}>
            <Ico label={t("删除")}><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /><line x1="10" y1="11" x2="10" y2="17" /><line x1="14" y1="11" x2="14" y2="17" /></Ico>
          </button>
        </div>
      ))}
      <h3 style={{ marginTop: 20 }}>{t("Google 登录")}</h3>
      <p className="muted">{t("在 Google Cloud Console 创建 OAuth 应用，回调地址填 {origin}/api/auth/oauth/google/callback。Secret 留空保持不变。", { origin: window.location.origin })}</p>
      <div className="form2">
        <div><label>Google Client ID</label><input id="oauth-google-id" defaultValue={String(settings.google_oauth_client_id ?? "")} placeholder={t("未配置")} onBlur={saveGoogleOAuth} /></div>
        <div><label>Google Client Secret</label><input type="password" id="oauth-google-secret" placeholder={settings.google_oauth_configured ? t("已配置，留空保持不变") : t("未配置")} onBlur={saveGoogleOAuth} /></div>
      </div>
      <h3 style={{ marginTop: 20 }}>{t("GitHub 登录")}</h3>
      <p className="muted">{t("在 GitHub Settings 创建 OAuth 应用，回调地址填 {origin}/api/auth/oauth/github/callback。Secret 留空保持不变。", { origin: window.location.origin })}</p>
      <div className="form2">
        <div><label>GitHub Client ID</label><input id="oauth-github-id" defaultValue={String(settings.github_oauth_client_id ?? "")} placeholder={t("未配置")} onBlur={saveGithubOAuth} /></div>
        <div><label>GitHub Client Secret</label><input type="password" id="oauth-github-secret" placeholder={settings.github_oauth_configured ? t("已配置，留空保持不变") : t("未配置")} onBlur={saveGithubOAuth} /></div>
      </div>
      {secMsg && <p style={{ color: "#f87171", marginTop: 12 }}>{secMsg}</p>}
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
      <p className="muted">{t("下载备份：导出全部数据为一个 JSON 文件（账户、域名、分组、设置、通知配置、指纹凭证、最近事件与流量记录，以及加密主密钥，恢复后 AK Secret 仍可用）。")}<b>{t("备份文件等同于全部凭据，请妥善保管，不要外发。")}</b></p>
      <p className="muted">{t("恢复备份：整体替换当前数据，管理员密码也会回到备份时的那一个；当前登录会话不受影响，恢复后建议检查一遍再调度。")}</p>
      <div className="row">
        <button className="btn" onClick={download} disabled={busy}>{t("下载备份")}</button>
        <input type="file" id="backup-file" accept=".json,application/json"
          style={{ flex: 1 }} onChange={(e) => setFileName(e.target.files?.[0]?.name || "")} />
        <button className="btn" onClick={restore} disabled={busy || !fileName}>{t("恢复备份")}</button>
      </div>
      {fileName && <p className="muted">{t("已选：{name}", { name: fileName })}</p>}
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
  const [langMenuOpen, setLangMenuOpen] = useState(false);
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
  useEffect(() => { if (tab === "logs" && authState === "authed") loadLogs(); }, [tab, authState]);

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true); setMsg("");
    try { await fn(); setMsg(ok); await load(); }
    catch (e) { setMsg(String((e as Error).message)); }
    finally { setBusy(false); }
  };

  const saveSettings = async (patch: Any) => {
    await run(async () => put("/api/settings", patch), t("已保存"));
  };

  // DDNS 设置（Cloudflare Token + 时区）：Token 留空保持不变，只有填了
  // 新值才提交；改动自动保存。
  const ddnsPatch = () => {
    const patch: Any = { timezone: (document.getElementById("s-timezone") as HTMLSelectElement)?.value || "Asia/Shanghai" };
    const tok = (document.getElementById("cf-token") as HTMLInputElement)?.value || "";
    if (tok) patch.cloudflare_token = tok;
    return patch;
  };

  // 切换模式按分组设置：全局设置页只保留“新建分组的默认值”，
  // 时间/差值两组控件都显示，不再按全局模式变灰。
  // 阈值踢出/保活/备用地址不受切换模式影响。
  const groupName = (gid: number | null | undefined) =>
    (groups.find((g) => g.id === (gid || 1))?.name as string) || t("未分组");
  const groupMode = (gid: number | null | undefined) =>
    (groups.find((g) => g.id === (gid || 1))?.switch_mode as string) || "load_balance";

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
      <InstanceForm account={a} settings={settings} count={accounts.length}
        onDone={() => { setCardSettingsId(null); load(); setMsg(t("已保存")); }}
        onCancel={() => setCardSettingsId(null)}
        onAutoSaved={() => { load(); setMsg(t("已保存")); }} />
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
    setSRefresh(["5", "10", "15", "30", "60"].includes(r) ? r : "custom");
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
      api_refresh_minutes: (() => {
        if (sRefresh === "custom") return (document.getElementById("s-refresh-custom") as HTMLInputElement)?.value || "5";
        return sRefresh;
      })(),
      settlement_currency: sCurrency,
      exchange_rate_usd_cny: (document.getElementById("s-fx-rate") as HTMLInputElement).value || String(settings.exchange_rate_usd_cny ?? "6.6719"),
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
          <h1 style={{ margin: 0 }}>{tab === "dash" ? t("运行概览") : tab === "accounts" ? t("账户管理") : tab === "groups" ? t("分组管理") : tab === "logs" ? t("日志") : subTab === "settings" ? t("全局设置") : subTab === "domains" ? t("域名管理") : t("通知设置")}</h1>
          <div className="row">
            <div className="theme-menu">
              <button className="btn ghost" aria-label={t("语言")} onClick={() => { setLangMenuOpen(!langMenuOpen); setThemeMenuOpen(false); }}>
                {lang === "zh" ? t("简体中文") : "English"}
              </button>
              {langMenuOpen && (
                <>
                  <div className="menu-backdrop" onClick={() => setLangMenuOpen(false)} />
                  <div className="menu">
                    {(["zh", "en"] as const).map((v) => (
                      <button key={v} className={lang === v ? "sel" : ""} onClick={() => { setLang(v); setLangMenuOpen(false); }}>
                        <span>{v === "zh" ? t("简体中文") : "English"}</span>
                        <span className="menu-check">{lang === v ? "✓" : ""}</span>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
            <div className="theme-menu">
              <button className="btn ghost theme-btn" aria-label={t("主题")} onClick={() => { setThemeMenuOpen(!themeMenuOpen); setLangMenuOpen(false); }}>
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
          </div>
        </div>
        <p>{tab === "dash" ? t("掌握账户流量、VPS 状态和域名分配。") : tab === "accounts" ? t("管理中国站与国际站账户，按需覆盖全局调度参数。") : tab === "groups" ? t("一个分组只选一种切换模式，1 个分组最多绑定 1 个域名。") : tab === "logs" ? t("最近的事件记录，最新的在前面。") : subTab === "settings" ? t("设置调度规则和登录方式。") : subTab === "domains" ? t("管理 Cloudflare 域名，保留现有分配并按需切换账户。") : t("仅事件通知。可同时启用 E-Mail 和 Telegram。")}</p>
      </header>
      <nav>{TABS.map(([k, label]) => (
        <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{t(label)}</button>
      ))}</nav>
      {tab === "config" && (
        <div className="subtabs">{SUB_TABS.map(([k, label]) => (
          <button key={k} className={subTab === k ? "active" : ""} onClick={() => setSubTab(k)}>{t(label)}</button>
        ))}</div>
      )}
      {msg && <div className="notice">{msg}</div>}

      {tab === "dash" && (
        <>
          <div className="notice">{t("调度运行时会按当前配置执行 VPS 和 DNS 操作。")}</div>
          <div className="grid3">
            <div className="card"><h3>{t("实例状态")}</h3>
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
              <div className="muted" style={{ marginTop: 8 }}>{t("调度")}{(status?.scheduler as Any)?.paused ? t("已暂停") : t("运行中")} · {t("API 刷新")} {(status?.scheduler as Any)?.api_refresh_minutes ?? (status?.scheduler as Any)?.check_interval_minutes ?? 5} {t("分钟")} · {t("DNS 已确认")} {status?.dns_confirmed ?? 0} / {status?.dns_total ?? 0}</div>
            </div>
            <div className="card"><h3>{t("本月流量")}</h3>
              <div className="big">{fmtGb(status?.total_used_gb ?? 0)} GiB / {fmtNum(totalQuota)} GiB{totalUsedPct != null ? ` (${totalUsedPct.toFixed(2)}%)` : ""}</div>
              <div className="muted">{t("已取得 {a} / {b} 个账户用量", { a: status?.accounts_known ?? 0, b: status?.accounts_total ?? 0 })}</div>
            </div>
            <div className="card"><h3>{t("当前费用")}{costAsOf ? <span className="muted" style={{ float: "right" }}>{t("截至 {date}", { date: costAsOf })}</span> : null}</h3>
              <div className="big">{costConverted ? `${costConverted.total.toFixed(2)} ${costConverted.cur}` : (Object.entries((status?.total_cost as Any) || {}).map(([c, v]) => `${v} ${c}`).join(" / ") || "—")}</div>
              <div className="muted">{t("本月费用账期 {month}", { month: status?.month ?? "" })}<br />{costConverted ? t("已查询 {a} / {b} 个账户账单，按汇率 {rate} 换算成 {currency}", { a: status?.accounts_known ?? 0, b: status?.accounts_total ?? 0, rate: costConverted.rate, currency: costConverted.cur }) : t("已查询 {a} / {b} 个账户账单，按币种汇总", { a: status?.accounts_known ?? 0, b: status?.accounts_total ?? 0 })}</div>
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
            {accounts.length === 0 && <p className="muted">{t("暂无账户，先去「账户管理」添加。")}</p>}
            {viewMode === "card" ? (
              <div className="grid3" style={{ marginTop: 12 }}>
                {accounts.map((a) => (
                  <div className="card" key={a.id as number} style={{ marginBottom: 0 }}>
                    <div>{dotEl(a.status_dot, a.status_detail || a.status_label || "")}<b style={{ fontSize: 16 }}>{a.name as string}</b> <span className="pill">{a.status_label ? trStatus(a.status_label, lang) : (a.instance_status || t("未知"))}</span>{a.evicted ? <span className="pill gray" style={{ marginLeft: 6 }}>{t("已跳出")}</span> : null}</div>
                    <div className="bar" style={{ marginTop: 10 }}><i style={{ width: `${a.usage_rate ?? 0}%`, background: usageColor(a.usage_rate, (a.protection_threshold_pct ?? settings.protection_threshold_pct ?? 95) as number) }} /></div>
                    <div className="stats">
                      <div className="stat"><span>{t("地域")}</span><b>{a.region as string}</b></div>
                      <div className="stat"><span>IP</span><b>{maskIp(a.public_ip)}</b></div>
                      <div className="stat"><span>{t("流量")}</span><b>{fmtGb(a.traffic_used_gb)} / {a.quota_gb ?? 200} GiB{trafficPct(a)}</b></div>
                      <div className="stat"><span>{t("费用")}</span><b>{a.billing_month_cost ?? "—"} {a.billing_currency || ""}</b></div>
                      <div className="stat"><span>{t("站点")}</span><b>{a.site === "cn" ? t("中国站") : t("国际站")}</b></div>
                    </div>
                    {a.status_detail ? <div className="muted" style={{ marginTop: 6 }}>{a.status_detail as string}</div> : null}
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
                    <td>{a.billing_month_cost ?? "—"} {a.billing_currency || ""}</td>
                  </tr>
                ))}</tbody></table>
            )}
          </div>
        </>
      )}

      {tab === "accounts" && (
        <div className="card">
          {editingId === "new" && (
            <InstanceForm account={null} settings={settings} count={accounts.length}
              onDone={() => { setEditingId(null); load(); setMsg(t("账户已添加")); }}
              onCancel={() => setEditingId(null)}
              onAutoSaved={() => { load(); setMsg(t("已保存")); }} />
          )}
          {accounts.map((a) => (
            <div className="card" key={a.id as number} style={{ marginTop: 12 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>{dotEl(a.status_dot, a.status_detail || a.status_label || "")}<b style={{ fontSize: 16 }}>{a.name as string}</b> <span className="pill">{a.status_label ? trStatus(a.status_label, lang) : (a.instance_status || t("未知"))}</span>{a.evicted ? <span className="pill gray" style={{ marginLeft: 6 }}>{t("已跳出")}</span> : null}<br />
                <span className="muted">{a.site === "cn" ? t("中国站 cn") : t("国际站 global")} · {a.region as string} · {groupName(a.group_id)}（{groupMode(a.group_id) === "time" ? t("定时轮巡") : t("负载均衡")}）</span></div>
                <button className="btn ghost" title={t("卡片设置")} onClick={() => setCardSettingsId(cardSettingsId === a.id ? null : (a.id as number))}>⚙️</button>
              </div>
              <div className="bar" style={{ marginTop: 10 }}><i style={{ width: `${a.usage_rate ?? 0}%`, background: usageColor(a.usage_rate, (a.protection_threshold_pct ?? settings.protection_threshold_pct ?? 95) as number) }} /></div>
              <div className="stats">
                <div className="stat"><span>{t("实例")}</span><b>{a.instance_id as string}</b></div>
                <div className="stat"><span>IP</span><b>{maskIp(a.public_ip)}</b></div>
                <div className="stat"><span>{t("流量")}</span><b>{fmtGb(a.traffic_used_gb)} / {a.quota_gb ?? 200} GiB{trafficPct(a)}</b></div>
                <div className="stat"><span>{t("费用")}</span><b>{a.billing_month_cost ?? "—"} {a.billing_currency || ""}</b></div>
                <div className="stat"><span>{t("阈值")}</span><b>{a.protection_threshold_pct ?? settings.protection_threshold_pct ?? 95}%</b></div>
              </div>
              <div className="muted" style={{ marginTop: 6 }}>
                {t("阈值动作")} {exclLabel(a.exclusion_action_effective || a.exclusion_action)} · {t("恢复")} {a.recovery_action_effective === "keep_stopped" ? t("入池但保持停机") : t("恢复时拉起")} · {t("每日定时")} {a.daily_schedule_enabled ? `${a.daily_on_time}–${a.daily_off_time}` : t("关")} · {t("日报")} {a.daily_report === "on" ? t("单独开启") : a.daily_report === "off" ? t("单独关闭") : t("跟随全局")}{a.has_secret ? "" : ` · ${t("未配置凭据")}`}
              </div>
              {a.status_detail ? <div className="muted" style={{ marginTop: 4 }}>{a.status_detail as string}</div> : null}
              <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}><ActionButtons a={a} /></div>
              {cardSettingsId === a.id && <CardSettings a={a} />}
            </div>
          ))}
          <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
            <button className="btn" onClick={() => setEditingId("new")}>{t("+ 添加实例")}</button>
          </div>
        </div>
      )}

      {tab === "config" && subTab === "domains" && (
        <>
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <h2 style={{ margin: 0 }}>{t("受管理域名 {count}", { count: domains.length })}</h2>
            <button className="btn ghost" title={t("DDNS 设置")} onClick={() => setShowDdns(!showDdns)}>⚙️</button>
          </div>
          <div className="notice">{t("1 个分组最多绑定 1 个域名。排在前面的域名优先使用流量使用率较低的账户。Zone ID 与记录 ID 自动发现。")}</div>
          <table><thead><tr><th>{t("优先顺序")}</th><th>{t("域名")}</th><th>{t("分组")}</th><th>Zone</th><th>{t("当前记录快照")}</th><th>{t("记录设置")}</th><th>{t("操作")}</th></tr></thead>
            <tbody>{domains.map((d, i) => {
              const cur = accounts.find((a) => a.id === d.current_account_id);
              const editing = editDomId === d.id;
              // 域名行内编辑自动保存：改完即存，不退出编辑态（点叉号退出）；没改过不发请求
              const saveDomEdit = (nm0: string, grp0: string) => {
                const nm = nm0.trim();
                if (!nm) { setMsg(t("请填写域名")); return; }
                if (nm === d.name && Number(grp0) === (d.group_id || 1)) return;
                const payload: Any = { name: nm, zone: nm.split(".").slice(-2).join("."), group_id: Number(grp0) };
                if (nm !== d.name) { payload.current_account_id = null; payload.current_ip = ""; payload.fallback_active = 0; }
                run(() => put(`/api/domains/${d.id}`, payload), t("域名已更新"));
              };
              return (
              <tr key={d.id as number}><td>{String(i + 1).padStart(2, "0")}<br />
                <button className="btn ghost" disabled={i === 0 || editing} onClick={() => run(async () => { await post("/api/domains/reorder", { order: domains.map((x, j) => (j === i ? domains[i - 1] : j === i - 1 ? domains[i] : x).id) }); }, t("已调整顺序"))}>↑</button>
                <button className="btn ghost" disabled={i === domains.length - 1 || editing} onClick={() => run(async () => { await post("/api/domains/reorder", { order: domains.map((x, j) => (j === i ? domains[i + 1] : j === i + 1 ? domains[i] : x).id) }); }, t("已调整顺序"))}>↓</button></td>
                <td>{editing ? <input value={editDomName} onChange={(e) => setEditDomName(e.target.value)} onBlur={() => saveDomEdit(editDomName, editDomGroup)} style={{ width: "100%" }} /> : <b>{d.name as string}</b>}</td>
                <td>{editing ? (
                  <select value={editDomGroup} onChange={(e) => { const gv = e.target.value; setEditDomGroup(gv); saveDomEdit(editDomName, gv); }}>
                    {groups.map((g) => {
                      const bound = domains.some((x) => x.id !== d.id && (x.group_id || 1) === g.id);
                      return <option key={g.id as number} value={g.id as number} disabled={bound}>{g.name as string}{bound ? t("（已绑定域名，不可选）") : ""}</option>;
                    })}
                  </select>
                ) : (<>{groupName(d.group_id)}<br /><span className="muted">{groupMode(d.group_id) === "time" ? t("定时轮巡") : t("负载均衡")}</span></>)}</td>
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
                    <button className="icon-btn" title={t("编辑")} onClick={() => { setEditDomId(d.id as number); setEditDomName(d.name as string); setEditDomGroup(String(d.group_id || 1)); }}>
                      <Ico label={t("编辑")}><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" /></Ico>
                    </button>
                    <button className="icon-btn danger" title={t("删除")} onClick={() => { if (confirm(t("删除域名 {name}？", { name: d.name }))) run(() => del(`/api/domains/${d.id}`), t("已删除")); }}>
                      <Ico label={t("删除")}><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /><line x1="10" y1="11" x2="10" y2="17" /><line x1="14" y1="11" x2="14" y2="17" /></Ico>
                    </button>
                  </div>
                )}</td></tr>
              ); })}</tbody></table>
          <p className="muted">{t("同名存在多条 A / AAAA / CNAME 记录时报告冲突，由用户整理。")}</p>
          <div className="row">
            <input id="dom-name" placeholder={t("域名，如 www.example.xyz")} style={{ flex: 1 }} />
            <select id="dom-group" defaultValue="">
              <option value="">{t("请选择分组")}</option>
              {groups.map((g) => {
                const bound = domains.some((d) => (d.group_id || 1) === g.id);
                return <option key={g.id as number} value={g.id as number} disabled={bound}>{g.name as string}{bound ? t("（已绑定域名，不可选）") : ""}</option>;
              })}
            </select>
            <button className="btn" onClick={() => {
              const name = (document.getElementById("dom-name") as HTMLInputElement).value.trim();
              if (!name) { setMsg(t("请填写域名")); return; }
              const gv = (document.getElementById("dom-group") as HTMLSelectElement).value;
              if (!gv) { setMsg(t("请选择分组")); return; }
              run(() => post("/api/domains", { name, zone: name.split(".").slice(-2).join("."), priority: domains.length, group_id: Number(gv) }), t("域名已添加"));
            }}>{t("+ 添加域名")}</button>
          </div>
        </div>
        {showDdns && (
        <div className="card"><h2>{t("DDNS 设置")}</h2>
          <p className="muted">{t("Cloudflare Token 用于 DDNS 解析（Zone / 记录 ID 自动发现，IP 不变不改）。")}</p>
          <div className="form2">
            <div><label>Cloudflare API Token</label>
              <input type="password" id="cf-token" placeholder={settings.cloudflare_configured ? t("已配置，留空保持不变") : t("未配置，填写 Cloudflare API Token")} onBlur={() => saveSettings(ddnsPatch())} />
              <p className="muted">{settings.cloudflare_configured ? t("已配置。留空保存时保持不变，只有填了新值才更新。") : t("未配置。填写后才能执行 DDNS 更新。")}</p></div>
            <div><label className="req">{t("时区 *")}</label>
              <select defaultValue={String(settings.timezone ?? "Asia/Shanghai")} id="s-timezone" onChange={() => saveSettings(ddnsPatch())}>
                {(timezones.length ? timezones : TZ_FALLBACK).map((tz) => (
                  <option key={tz.value} value={tz.value}>{tz.label}</option>
                ))}
              </select>
              <p className="muted">{t("当前：{tz}。夜间窗口、固定时刻、间隔锚点、每日开关机、日报推送时间都按此时区执行；保存后立即生效，下次调度按新时区走，不用重启。容器 TZ 环境变量只作后备默认值。", { tz: String(settings.timezone ?? "Asia/Shanghai") })}</p></div>
          </div>
        </div>
        )}
        </>
      )}

      {tab === "groups" && (
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2 style={{ margin: 0 }}>{t("分组 {count}", { count: groups.length })}</h2>
            <button className="btn" onClick={() => setShowNewGroup(!showNewGroup)}>{t("+ 新建分组")}</button>
          </div>
          <div className="notice">{t("一个分组只选一种切换模式：定时轮巡或负载均衡，不同分组可以选不同模式。1 个分组最多绑定 1 个域名。阈值踢出、保活、备用地址不受切换模式影响。")}</div>
          {showNewGroup && (
            <div className="instance-form" style={{ marginBottom: 12 }}>
              <label className="req">{t("分组名 *")}</label>
              <input value={newGroupName} onChange={(e) => setNewGroupName(e.target.value)} placeholder={t("如 分组C")} />
              <label className="lbl" style={{ marginTop: 12 }}>{t("切换模式")}</label>
              <Seg value={newGroupMode} options={[
                { value: "time", label: t("定时轮巡") },
                { value: "load_balance", label: t("负载均衡") },
              ]} onChange={setNewGroupMode} />
              <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
                <button className="btn ghost" onClick={() => { setShowNewGroup(false); setNewGroupName(""); setNewGroupMode(""); }}>{t("取消")}</button>
                <button className="btn" onClick={() => {
                  if (!newGroupName.trim()) { setMsg(t("请填写分组名")); return; }
                  if (!newGroupMode) { setMsg(t("请选择切换模式")); return; }
                  run(() => post("/api/groups", { name: newGroupName.trim(), switch_mode: newGroupMode }), t("分组已创建"));
                  setShowNewGroup(false); setNewGroupName(""); setNewGroupMode("");
                }}>{t("创建分组")}</button>
              </div>
            </div>
          )}
          {groups.map((g) => (
            <GroupCard key={`${g.id}-${g.interval_hours}-${g.switch_diff_pct}-${g.switch_diff_gb}-${g.fixed_switch_time}-${g.night_window_start}-${g.night_window_end}`}
              g={g} settings={settings} accounts={accounts} domains={domains} dotEl={dotEl} run={run} />
          ))}
        </div>
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
              <div><label className="req">{t("切换阀值 *")}</label>
                <Seg value={sDiffMode} options={[
                  { value: "percent", label: t("百分比（%）") },
                  { value: "gb", label: t("固定流量（GB）") },
                ]} onChange={(v) => { setSDiffMode(v); saveParamSettings({ switch_diff_mode: v }); }} /></div>
              {sDiffMode === "percent" ? (
                <div><label className="req">{t("切换阀值 *（%）")}</label><input defaultValue={String(settings.switch_diff_pct ?? 10)} id="s-diff" onBlur={() => saveParamSettings()} /></div>
              ) : (
                <div><label className="lbl">{t("切换阀值（GB）")}</label><input defaultValue={String(settings.switch_diff_gb ?? 10)} id="s-diffgb" onBlur={() => saveParamSettings()} /></div>
              )}
              <div><p className="muted" style={{ marginTop: 28 }}>{t("负载均衡：% 模式差值 ≥ 10% 切，GB 模式差值 ≥ 10 GB 切。")}</p></div>
              <div><label className="req">{t("API 刷新时间 *（分钟）")}</label>
                <Seg value={sRefresh} options={[
                  { value: "5", label: t("5 分钟") }, { value: "10", label: t("10 分钟") },
                  { value: "15", label: t("15 分钟") }, { value: "30", label: t("30 分钟") },
                  { value: "60", label: t("60 分钟") },
                  { value: "custom", label: t("自定义") },
                ]} onChange={(v) => { setSRefresh(v); saveParamSettings({ api_refresh_minutes: v === "custom" ? ((document.getElementById("s-refresh-custom") as HTMLInputElement)?.value || "5") : v }); }} />
                {sRefresh === "custom" && (
                <input placeholder={t("自定义分钟数")} defaultValue={["5", "10", "15", "30", "60"].includes(String(settings.api_refresh_minutes ?? 5)) ? "" : String(settings.api_refresh_minutes ?? 5)} id="s-refresh-custom" style={{ marginTop: 6 }} onBlur={() => saveParamSettings()} />)}
                <p className="muted">{t("多久查一次阿里云 CDT/ECS/BSS 状态并重新评估。保存后下次调度按新间隔走（与旧的检查间隔是同一项，已合并）。")}</p></div>
            </div>
            <h3 style={{ marginTop: 20 }}>{t("阈值")}</h3>
            <p className="muted">{t("到达阈值默认不停机，只是跳出 DDNS：实例踢出池子、不再参与 DDNS 解析，域名切到下一台用量最低的。恢复线 = 阈值 − 2%（如阈值 95%，回落到 93% 以下才恢复）；被踢出超 24 小时且用量低于阈值时兜底恢复；流量一更新就重新评估，恢复后同一轮重新参与分配。")}</p>
            <div>
              <label className="lbl">{t("阈值动作")}</label>
              <Seg value={sExcl} options={[
                { value: "none", label: t("踢出DDNS") },
                { value: "stop_charging", label: t("节省停机") },
                { value: "stop", label: t("停机") },
              ]} onChange={(v) => { setSExcl(v); saveParamSettings({ exclusion_action: v }); }} />
              <p className="muted">{t("默认不停机，只跳出 DDNS；如需省流量费再选停机/节省停机。停机永远在切完 DNS 之后，同一台只停一次。")}</p>
            </div>
            <div style={{ marginTop: 12 }}>
              <label className="lbl">{t("阈值恢复")}</label>
              <Seg value={sRecovery} options={[
                { value: "start", label: t("恢复时拉起（默认）") },
                { value: "keep_stopped", label: t("恢复入池但保持停机") },
              ]} onChange={(v) => { setSRecovery(v); saveParamSettings({ threshold_recovery_action: v }); }} />
              <p className="muted">{t("每日定时关机窗口内恢复拉起不执行（定时关机优先）；定时开机窗口内正常执行。")}</p>
            </div>
            <h3 style={{ marginTop: 20 }}>{t("定时轮巡")}</h3>
            <div>
            <div className="form2">
              <div><label className="lbl">{t("轮巡周期")}</label>
                <Seg value={sInterval} options={[
                  { value: "1", label: t("1 小时") }, { value: "2", label: t("2 小时") },
                  { value: "3", label: t("3 小时") }, { value: "4", label: t("4 小时") },
                  { value: "6", label: t("6 小时") }, { value: "12", label: t("12 小时") },
                  { value: "24", label: t("24 小时") }, { value: "0", label: t("自定义") },
                ]} onChange={(v) => { setSInterval(v); saveParamSettings({ interval_hours: v }); }} /></div>
              {sInterval === "0" && (
              <div onBlur={() => saveParamSettings()}><label className="lbl">{t("自定义间隔")}</label>
                <div className="row" style={{ alignItems: "center" }}>
                  <input type="number" min={0} value={sCustVal} onChange={(e) => setSCustVal(e.target.value)} style={{ width: 120 }} />
                  <Seg value={sCustUnit} options={[
                    { value: "minute", label: t("分钟") },
                    { value: "hour", label: t("小时") },
                    { value: "day", label: t("天") },
                  ]} onChange={changeCustUnit} />
                </div></div>)}
            </div>
            </div>
            <div style={{ marginTop: 12 }}>
              <label className="lbl">{t("货币结算")}</label>
              <Seg value={sCurrency} options={[
                { value: "USD", label: "USD" },
                { value: "CNY", label: "CNY" },
              ]} onChange={(v) => { setSCurrency(v); saveParamSettings({ settlement_currency: v }); }} />
              <div style={{ marginTop: 8 }}>
                <label className="lbl">{t("汇率")}</label>
                <div className="row" style={{ alignItems: "center" }}><span>1 USD =&nbsp;</span><input id="s-fx-rate" type="number" step="0.0001" min={0} defaultValue={String(settings.exchange_rate_usd_cny ?? "6.6719")} style={{ width: 120 }} onBlur={() => saveParamSettings()} /><span>&nbsp;CNY</span></div>
              </div>
              <p className="muted">{t("当前费用卡片按所选货币与汇率换算显示；日报仍按各账户币种。")}</p>
            </div>
          </div>
          <LoginSecurity adminName={String(authInfo.admin_name || "")} settings={settings} saveSettings={saveSettings} setMsg={setMsg} />
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
                <div className="row" key={k} style={{ padding: "6px 0" }}>
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

      {tab === "logs" && (
        <div className="card"><h2>{t("日志")}</h2>
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
    </div>
  );
}
