import { useEffect, useState } from "react";
import { get, post, put, del } from "./api";

type Tab = "dash" | "accounts" | "domains" | "groups" | "settings" | "notify";
type Any = Record<string, any>;

const TABS: [Tab, string][] = [
  ["dash", "运行概览"], ["accounts", "Ali 账户"], ["domains", "域名管理"],
  ["groups", "分组管理"], ["settings", "全局设置"], ["notify", "通知设置"],
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

// 阈值动作三选一（radio）：全局设置页与实例配置表单共用同一组选项与文案。
// 内部值沿用 exclusion_action：none=踢出DDNS（默认，不停机）/
// stop_charging=节省停机 / stop=停机。
const EXCL_OPTIONS = [
  { value: "none", title: "踢出DDNS（默认，不停机）", sub: "只跳出 DDNS，继续跑" },
  { value: "stop_charging", title: "节省停机", sub: "切完 DNS 后节省停机" },
  { value: "stop", title: "停机", sub: "切完 DNS 后停机" },
];

const exclLabel = (v: string | undefined | null) =>
  (EXCL_OPTIONS.find((o) => o.value === (v || "none"))?.title as string) || "踢出DDNS（默认，不停机）";

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

// 实例配置表单（按截图结构，深色主题）：站点在地域之前（站点决定端点与币种），
// 地域来自阿里云 DescribeRegions + 已有实例发现，失败回退内置列表。
function InstanceForm({ account, groups, settings, count, onDone, onCancel }: {
  account: Any | null; groups: Any[]; settings: Any; count: number;
  onDone: () => void; onCancel: () => void;
}) {
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
  const [groupId, setGroupId] = useState(String(account?.group_id ?? 1));
  const [schedEnabled, setSchedEnabled] = useState(Boolean(account?.daily_schedule_enabled));
  const [onTime, setOnTime] = useState((account?.daily_on_time as string) || "08:00");
  const [offTime, setOffTime] = useState((account?.daily_off_time as string) || "20:00");
  const [report, setReport] = useState((account?.daily_report as string) || "follow");
  const [excl, setExcl] = useState((account?.exclusion_action as string) || "");
  const [recovery, setRecovery] = useState((account?.recovery_action as string) || "");
  const [regions, setRegions] = useState<{ region_id: string; local_name: string }[]>(BUILTIN_REGIONS);
  const [ownedIds, setOwnedIds] = useState<Set<string>>(new Set());
  const [regionsNote, setRegionsNote] = useState("内置常用地域");
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
      setRegionsNote("地域来自阿里云 DescribeRegions；“已有实例”组为该账号下已有实例所在地域");
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
        setRegionsNote("地域来自阿里云 DescribeRegions");
      } catch {
        setRegions(BUILTIN_REGIONS);
        setOwnedIds(new Set());
        setRegionsNote("接口未取到，已用内置列表");
      }
    }
  };

  useEffect(() => { loadRegions(false); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [site, account?.id]);

  const autoDiscover = async () => {
    if (!instanceId.trim()) { setFormMsg("请先填写实例 ID"); return; }
    setFormBusy(true); setFormMsg("");
    try {
      const body: Any = { instance_id: instanceId.trim(), site };
      if (account?.id) body.account_id = account.id;
      else if (akId && akSecret) { body.access_key_id = akId; body.access_key_secret = akSecret; }
      else throw new Error("请先填写 AccessKey ID 与 Secret，或先保存账户");
      const r = await post("/api/aliyun/find-instance-region", body) as Any;
      setRegion(r.region_id as string);
      setRegionCustom(false);
      setFormMsg(`已发现实例所在地域：${r.local_name}（${r.region_id}）`);
    } catch (e) { setFormMsg(String((e as Error).message)); }
    finally { setFormBusy(false); }
  };

  const save = async () => {
    const vName = name.trim();
    if (!vName) { setFormMsg("请填写备注（服务器名）"); return; }
    if (!akId.trim()) { setFormMsg("请填写 AccessKey ID"); return; }
    if (!instanceId.trim()) { setFormMsg("请填写实例 ID"); return; }
    const q = Number(quota);
    if (!quota || !(q > 0)) { setFormMsg("流量额度必须大于 0"); return; }
    if (schedEnabled && onTime === offTime) { setFormMsg("每日开机时间与关机时间不能相同"); return; }
    const finalRegion = regionCustom ? regionCustomVal.trim() : region;
    if (!finalRegion) { setFormMsg("请选择地域或填写自定义地域"); return; }
    setFormBusy(true); setFormMsg("");
    const payload: Any = {
      name: vName, access_key_id: akId.trim(), instance_id: instanceId.trim(),
      site, region: finalRegion, quota_gb: q, group_id: Number(groupId) || 1,
      daily_schedule_enabled: schedEnabled ? 1 : 0, daily_on_time: onTime,
      daily_off_time: offTime, daily_report: report,
      exclusion_action: excl || "", recovery_action: recovery || "",
    };
    if (akSecret) payload.access_key_secret = akSecret;  // 留空保持不变，绝不清空
    try {
      if (isNew) { payload.priority = count; await post("/api/accounts", payload); }
      else await put(`/api/accounts/${account.id}`, payload);
      onDone();
    } catch (e) { setFormMsg(String((e as Error).message)); }
    finally { setFormBusy(false); }
  };

  const regionKnown = regions.some((r) => r.region_id === region);
  const ownedOptions = regions.filter((r) => ownedIds.has(r.region_id));
  const allOptions = regions.filter((r) => !ownedIds.has(r.region_id));

  return (
    <div className="instance-form">
      <div className="form2">
        <div><label>AccessKey ID *</label><input value={akId} onChange={(e) => setAkId(e.target.value)} placeholder="LTAI..." /></div>
        <div><label>AccessKey Secret *</label><input type="password" value={akSecret} onChange={(e) => setAkSecret(e.target.value)} placeholder={account?.has_secret ? "已配置（留空保持不变）" : "未配置，请填写"} /></div>
        <div><label>实例 ID *</label><input value={instanceId} onChange={(e) => setInstanceId(e.target.value)} placeholder="i-..." /></div>
        <div><label>站点类型 *</label><select value={site} onChange={(e) => setSite(e.target.value)}>
          <option value="global">国际站（USD）</option><option value="cn">国内站（CNY）</option></select>
          <p className="muted">站点决定调用哪套阿里云端点与币种；选站点后自动重新拉取该站点的地域列表。</p></div>
        <div><label>地域 *</label>
          <select value={regionCustom ? "__custom" : region} onChange={(e) => {
            if (e.target.value === "__custom") { setRegionCustom(true); setRegionCustomVal(region); }
            else { setRegionCustom(false); setRegion(e.target.value); }
          }}>
            {ownedOptions.length > 0 && (
              <optgroup label="已有实例所在地域">
                {ownedOptions.map((r) => <option key={r.region_id} value={r.region_id}>{r.local_name}（{r.region_id}）</option>)}
              </optgroup>
            )}
            <optgroup label="全部可用地域">
              {allOptions.map((r) => <option key={r.region_id} value={r.region_id}>{r.local_name}（{r.region_id}）</option>)}
            </optgroup>
            {!regionKnown && !regionCustom && <option value={region}>{region}（当前值）</option>}
            <option value="__custom">自定义…</option>
          </select>
          {regionCustom && <input value={regionCustomVal} onChange={(e) => setRegionCustomVal(e.target.value)} placeholder="地域 ID，如 cn-hongkong" style={{ marginTop: 6 }} />}
          <p className="muted">{regionsNote}</p>
          <div className="row">
            <button className="btn ghost" onClick={autoDiscover} disabled={formBusy}>自动发现</button>
            <button className="btn ghost" onClick={() => loadRegions(true)} disabled={formBusy}>重新拉取地域列表</button>
          </div>
          <p className="muted">“自动发现”按实例 ID 查出该实例真实所在地域并填入；查不到会报错，不会静默填错。</p></div>
        <div><label>流量额度 *（GB）</label><input type="number" min={1} value={quota} onChange={(e) => setQuota(e.target.value)} /></div>
        <div><label>备注（服务器名）*</label><input value={name} onChange={(e) => setName(e.target.value)} placeholder="如 AliCloud-HK-1" /></div>
        <div><label>分组</label><select value={groupId} onChange={(e) => setGroupId(e.target.value)}>
          {groups.map((g: Any) => <option key={g.id} value={String(g.id)}>{g.name}（{g.switch_mode === "time" ? "按时间切换" : "按负载均衡"}）</option>)}
        </select></div>
      </div>
      <div style={{ marginTop: 12 }}>
        <label className="check"><input type="checkbox" checked={schedEnabled} onChange={(e) => setSchedEnabled(e.target.checked)} /> 每日定时开关机</label>
        {schedEnabled && (
          <div className="form2">
            <div><label>每日开机时间</label><input type="time" value={onTime} onChange={(e) => setOnTime(e.target.value)} /></div>
            <div><label>每日关机时间</label><input type="time" value={offTime} onChange={(e) => setOffTime(e.target.value)} /></div>
          </div>
        )}
        <p className="muted">开启后每天在设定时间开机/关机；错过时间窗口（重启/延迟）时在 1 小时内补偿一次。阈值踢出优先于定时开机：被踢出的实例到点也不拉起；关机窗口内恢复拉起也不执行。</p>
      </div>
      <div style={{ marginTop: 12 }}>
        <label>参与日报推送</label>
        <div className="seg">
          {(["follow", "on", "off"] as const).map((v) => (
            <button key={v} type="button" className={report === v ? "seg-on" : ""} onClick={() => setReport(v)}>
              {v === "follow" ? "跟随全局" : v === "on" ? "单独开启" : "单独关闭"}
            </button>
          ))}
        </div>
        <p className="muted">跟随全局：按全局每日财报开关；单独开启：即使全局关了也推这台；单独关闭：即使全局开了也不推这台。</p>
      </div>
      <div style={{ marginTop: 12 }}>
        <label>阈值动作（三选一）</label>
        <div className="radio-group">
          <label className="radio"><input type="radio" name={`excl-${account?.id || "new"}`} checked={excl === ""} onChange={() => setExcl("")} /><span><b>跟随全局</b><small>按全局设置（当前：{exclLabel(settings.exclusion_action)}）</small></span></label>
          {EXCL_OPTIONS.map((o) => (
            <label key={o.value} className="radio"><input type="radio" name={`excl-${account?.id || "new"}`} checked={excl === o.value} onChange={() => setExcl(o.value)} /><span><b>{o.title}</b><small>{o.sub}</small></span></label>
          ))}
        </div>
      </div>
      <div style={{ marginTop: 12 }}>
        <label>阈值恢复（用量回落后）</label>
        <div className="radio-group">
          <label className="radio"><input type="radio" name={`rec-${account?.id || "new"}`} checked={recovery === ""} onChange={() => setRecovery("")} /><span><b>跟随全局</b><small>按全局设置（当前：{settings.threshold_recovery_action === "keep_stopped" ? "恢复入池但保持停机" : "恢复时拉起"}）</small></span></label>
          <label className="radio"><input type="radio" name={`rec-${account?.id || "new"}`} checked={recovery === "start"} onChange={() => setRecovery("start")} /><span><b>恢复时拉起</b><small>用量回落到恢复线以下时自动开机再入池</small></span></label>
          <label className="radio"><input type="radio" name={`rec-${account?.id || "new"}`} checked={recovery === "keep_stopped"} onChange={() => setRecovery("keep_stopped")} /><span><b>恢复入池但保持停机</b><small>只入池，等保活/手动再拉起</small></span></label>
        </div>
      </div>
      {formMsg && <p style={{ color: "#f87171", marginTop: 12 }}>{formMsg}</p>}
      <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
        <button className="btn ghost" onClick={onCancel}>取消</button>
        <button className="btn" onClick={save} disabled={formBusy}>保存更改</button>
      </div>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>("dash");
  const [status, setStatus] = useState<Any | null>(null);
  const [accounts, setAccounts] = useState<Any[]>([]);
  const [domains, setDomains] = useState<Any[]>([]);
  const [groups, setGroups] = useState<Any[]>([]);
  const [settings, setSettings] = useState<Any>({});
  const [timezones, setTimezones] = useState<Any[]>([]);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [dry, setDry] = useState<Any | null>(null);
  const [editingId, setEditingId] = useState<number | "new" | null>(null);
  const [preview, setPreview] = useState<Any | null>(null);

  // 状态点（新映射）：green 在线 / yellow 排队中 / gray 已跳出或未知 /
  // red 停机或离线 / green_pulse 启动中（高亮闪烁绿）/ red_pulse 停止中（闪烁红）
  // 状态点颜色：Google 品牌色（green #34A853 / yellow #FBBC04 / gray #9AA0A6 / red #EA4335），
  // 与 styles.css 的 --dot-* 变量一致；尺寸 4px（直径约 1mm），见 .dot。
  const dotColor = (dot: string | undefined) =>
    dot === "green" ? "#34A853" : dot === "green_pulse" ? "#34A853" : dot === "yellow" ? "#FBBC04" : dot === "gray" ? "#9AA0A6" : dot === "red_pulse" ? "#EA4335" : "#EA4335";
  const dotClass = (dot: string | undefined) =>
    dot === "green_pulse" ? "dot-pulse" : dot === "red_pulse" ? "dot-pulse-red" : "";
  const dotEl = (dot: string | undefined, title?: string) => (
    <span className={`dot ${dotClass(dot)}`} title={title || ""}
      style={{ width: 4, height: 4, borderRadius: "50%", display: "inline-block", background: dotColor(dot), marginRight: 6 }} />
  );
  const Legend = () => (
    <p className="legend muted">
      <span><span className="dot" style={{ background: "#34A853" }} />绿=在线</span>
      <span><span className="dot" style={{ background: "#FBBC04" }} />黄=排队中</span>
      <span><span className="dot" style={{ background: "#9AA0A6" }} />灰=已跳出</span>
      <span><span className="dot" style={{ background: "#EA4335" }} />红=停机/离线</span>
      <span><span className="dot dot-pulse" style={{ background: "#34A853" }} />闪烁绿=启动中</span>
      <span><span className="dot dot-pulse-red" style={{ background: "#EA4335" }} />闪烁红=停止中</span>
      <span><span className="dot" style={{ background: "#9AA0A6" }} />灰=未知</span>
    </p>
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
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (tab === "notify") {
      get("/api/notify/daily-preview").then((r) => setPreview(r as Any)).catch(() => setPreview(null));
    }
  }, [tab]);

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true); setMsg("");
    try { const r = await fn(); setMsg(ok); setDry(r as Any); await load(); }
    catch (e) { setMsg(String((e as Error).message)); }
    finally { setBusy(false); }
  };

  const saveSettings = async (patch: Any) => {
    await run(async () => put("/api/settings", patch), "已保存");
  };

  // DDNS 设置（Cloudflare Token + 时区）：Token 留空保持不变，只有填了
  // 新值才提交；时区随“保存调度参数”一起保存，也可单独保存。
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
    (groups.find((g) => g.id === (gid || 1))?.name as string) || "默认分组";
  const groupMode = (gid: number | null | undefined) =>
    (groups.find((g) => g.id === (gid || 1))?.switch_mode as string) || "load_balance";

  return (
    <div className="app">
      <header>
        <h1>{tab === "dash" ? "运行概览" : tab === "accounts" ? "Ali 账户" : tab === "domains" ? "域名管理" : tab === "groups" ? "分组管理" : tab === "settings" ? "全局设置" : "通知设置"}</h1>
        <p>{tab === "dash" ? "掌握账户流量、VPS 状态和域名分配。" : tab === "accounts" ? "管理中国站与国际站账户，按需覆盖全局调度参数。" : tab === "domains" ? "管理 Cloudflare 域名，保留现有分配并按需切换账户。" : tab === "groups" ? "一个分组只选一种切换模式，1 个分组最多绑定 1 个域名。" : tab === "settings" ? "设置调度规则、网页访问和通知渠道。" : "仅事件通知。可同时启用 E-Mail 和 Telegram。"}</p>
      </header>
      <nav>{TABS.map(([k, label]) => (
        <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{label}</button>
      ))}</nav>
      {msg && <div className="notice">{msg}</div>}

      {tab === "dash" && (
        <>
          <div className="notice">调度运行时会按当前配置执行 VPS 和 DNS 操作。</div>
          <div className="row" style={{ marginBottom: 16 }}>
            <button className="btn ghost" onClick={() => run(() => get("/api/logs"), "日志已刷新")}>查看日志</button>
            <button className="btn ghost" onClick={() => run(() => post("/api/cloud/status"), "云端状态已查询")}>查询云端状态</button>
            <button className="btn ghost" disabled={busy} onClick={() => run(() => post("/api/schedule/dry-run"), "调度试算完成（未执行任何 VPS / DNS 操作）")}>调度试算</button>
            <button className="btn" disabled={busy} onClick={() => run(() => post("/api/schedule/run"), "立即调度完成")}>立即调度</button>
            <button className="btn ghost" disabled={busy} onClick={() => run(() => post("/api/schedule/pause", { paused: !(status?.scheduler as Any)?.paused }), "已切换调度状态")}>
              {(status?.scheduler as Any)?.paused ? "恢复调度" : "暂停调度"}
            </button>
          </div>
          <div className="grid3">
            <div className="card"><h3>调度状态</h3>
              <div className="big"><span className="dot" />{(status?.scheduler as Any)?.paused ? "已暂停" : "运行中"}</div>
              <div className="muted">API 刷新 {(status?.scheduler as Any)?.api_refresh_minutes ?? (status?.scheduler as Any)?.check_interval_minutes ?? 5} 分钟<br />DNS 已确认 {status?.dns_confirmed ?? 0} / {status?.dns_total ?? 0}</div>
            </div>
            <div className="card"><h3>本月已用流量</h3>
              <div className="big">{status?.total_used_gb ?? 0} <small>GiB</small></div>
              <div className="muted">已取得 {status?.accounts_known ?? 0} / {status?.accounts_total ?? 0} 个账户用量</div>
            </div>
            <div className="card"><h3>本月费用 <span className="muted" style={{ float: "right" }}>账期 {status?.month}</span></h3>
              <div className="big">{Object.entries((status?.total_cost as Any) || {}).map(([c, v]) => `${v} ${c}`).join(" / ") || "—"}</div>
              <div className="muted">已查询 {status?.accounts_known ?? 0} / {status?.accounts_total ?? 0} 个账户账单，按币种汇总</div>
            </div>
          </div>
          <div className="card"><h2>账户流量与状态</h2>
            <table><thead><tr><th>Ali 账户</th><th>账户站点</th><th>VPS 地域</th><th>已用 / 月度流量额度</th><th>流量使用率</th><th>VPS 状态</th></tr></thead>
              <tbody>{accounts.map((a) => (
                <tr key={a.id as number}><td><b>{a.name as string}</b></td><td>{a.site === "cn" ? "中国站" : "国际站"}</td><td>{a.region as string}</td>
                  <td><b>{a.traffic_used_gb ?? "—"}</b> / {a.quota_gb ?? 200} GiB</td>
                  <td>{a.usage_rate != null ? `${a.usage_rate}%` : "—"}<div className="bar"><i style={{ width: `${a.usage_rate ?? 0}%` }} /></div></td>
                  <td>{dotEl(a.status_dot, a.status_detail || a.status_label || "")}<span className="pill">{a.status_label || a.instance_status || "未知"}</span>{a.evicted ? <span className="pill gray" style={{ marginLeft: 6 }}>已跳出</span> : null}</td></tr>
              ))}</tbody></table>
            <Legend />
            <p className="muted">已跳出：达保护阈值被踢出池子，不再参与 DDNS，用量回落后自动恢复；排队中：在线待命，按使用率排队等接替。</p>
            {accounts.length === 0 && <p className="muted">暂无账户，先去「Ali 账户」添加。</p>}
          </div>
          <div className="card"><h2>当前 DNS 指向</h2>
            {domains.map((d) => (
              <div key={d.id as number} className="row" style={{ padding: "8px 0", borderBottom: "1px solid #1e2d4d" }}>
                <span className="pill blue">{String(d.priority + 1).padStart(2, "0")}</span>
                <b>{d.name as string}</b><span className="muted">A · 仅 DNS</span><span className="spacer" />
                <span>{(d.current_ip as string) || "未分配"}</span>
              </div>
            ))}
            <p className="muted">备用地址：{String(settings.fallback_address || "—")}</p>
          </div>
          <div className="card"><h2>最近活动</h2>
            {((status?.events as Any[]) || []).slice(0, 8).map((e) => (
              <div key={e.id as number} className="row" style={{ padding: "6px 0" }}><span>•</span><span>{e.title as string}</span><span className="muted">{e.detail as string}</span></div>
            ))}
          </div>
          {dry && <div className="card"><h2>调度试算结果</h2><pre>{JSON.stringify(dry, null, 2)}</pre></div>}
        </>
      )}

      {tab === "accounts" && (
        <div className="card"><h2>Ali 账户 {accounts.length}</h2>
          <p className="muted">每台实例一个卡片，点“编辑”进入配置表单。账户默认继承全局参数，可单独覆盖阈值动作、恢复方式、每日定时开关机与日报推送。地域来自阿里云 DescribeRegions，站点决定端点与币种。</p>
          <Legend />
          <div className="row" style={{ marginBottom: 12 }}>
            <button className="btn" onClick={() => setEditingId("new")}>+ 添加实例</button>
            <button className="btn ghost" onClick={() => run(() => post("/api/cloud/status"), "云端状态已查询")}>查询云端状态</button>
          </div>
          {editingId === "new" && (
            <InstanceForm account={null} groups={groups} settings={settings} count={accounts.length}
              onDone={() => { setEditingId(null); load(); setMsg("账户已添加"); }}
              onCancel={() => setEditingId(null)} />
          )}
          {accounts.map((a) => (
            <div className="card" key={a.id as number} style={{ marginTop: 12 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div><b>{a.name as string}</b> {dotEl(a.status_dot, a.status_detail || a.status_label || "")}<span className="pill">{a.status_label || a.instance_status || "未知"}</span>{a.evicted ? <span className="pill gray" style={{ marginLeft: 6 }}>已跳出</span> : null}<br />
                <span className="muted">{a.site === "cn" ? "中国站 cn" : "国际站 global"} · {a.region as string} · {groupName(a.group_id)}（{groupMode(a.group_id) === "time" ? "按时间切换" : "按负载均衡"}）</span></div>
                <div className="row">
                  <button className="btn ghost" title="复制配置摘要" onClick={async () => {
                    const text = `${a.name}\n实例 ID：${a.instance_id}\n地域：${a.region}\nIP：${a.public_ip || "—"}`;
                    try { await navigator.clipboard.writeText(text); setMsg("配置摘要已复制"); }
                    catch { setMsg(text); }
                  }}>⧉ 复制</button>
                  <button className="btn ghost" onClick={() => setEditingId(a.id as number)}>编辑</button>
                  <button className="btn ghost" onClick={() => { if (confirm(`删除账户 ${a.name}？删除后域名将重新分配。`)) run(() => del(`/api/accounts/${a.id}`), "已删除"); }}>删除</button>
                </div>
              </div>
              <div className="muted" style={{ marginTop: 8 }}>
                实例 {a.instance_id as string} · IP {a.public_ip as string || "—"} · 流量 <b>{a.traffic_used_gb ?? "—"}</b> / {a.quota_gb ?? 200} GiB{a.usage_rate != null ? `（${a.usage_rate}%）` : ""} · 本月费用 <b>{a.billing_month_cost ?? "—"}</b> {a.billing_currency || ""}
              </div>
              <div className="bar" style={{ marginTop: 6 }}><i style={{ width: `${a.usage_rate ?? 0}%` }} /></div>
              <div className="muted" style={{ marginTop: 6 }}>
                阈值动作 {exclLabel(a.exclusion_action_effective || a.exclusion_action)} · 阈值 {a.protection_threshold_pct ?? settings.protection_threshold_pct ?? 95}% · 恢复 {a.recovery_action_effective === "keep_stopped" ? "入池但保持停机" : "恢复时拉起"} · 每日定时 {a.daily_schedule_enabled ? `${a.daily_on_time}–${a.daily_off_time}` : "关"} · 日报 {a.daily_report === "on" ? "单独开启" : a.daily_report === "off" ? "单独关闭" : "跟随全局"}{a.has_secret ? "" : " · 未配置凭据"}
              </div>
              {a.status_detail ? <div className="muted" style={{ marginTop: 4 }}>{a.status_detail as string}</div> : null}
              {editingId === a.id && (
                <InstanceForm account={a} groups={groups} settings={settings} count={accounts.length}
                  onDone={() => { setEditingId(null); load(); setMsg("已保存"); }}
                  onCancel={() => setEditingId(null)} />
              )}
            </div>
          ))}
        </div>
      )}

      {tab === "domains" && (
        <div className="card"><h2>受管理域名 {domains.length}</h2>
          <div className="notice">1 个分组最多绑定 1 个域名。排在前面的域名优先使用流量使用率较低的账户。Zone ID 与记录 ID 自动发现。</div>
          <table><thead><tr><th>优先顺序</th><th>域名</th><th>分组</th><th>Zone</th><th>当前记录快照</th><th>记录设置</th><th>操作</th></tr></thead>
            <tbody>{domains.map((d, i) => {
              const cur = accounts.find((a) => a.id === d.current_account_id);
              return (
              <tr key={d.id as number}><td>{String(i + 1).padStart(2, "0")}<br />
                <button className="btn ghost" disabled={i === 0} onClick={() => run(async () => { await post("/api/domains/reorder", { order: domains.map((x, j) => (j === i ? domains[i - 1] : j === i - 1 ? domains[i] : x).id) }); }, "已调整顺序")}>↑</button>
                <button className="btn ghost" disabled={i === domains.length - 1} onClick={() => run(async () => { await post("/api/domains/reorder", { order: domains.map((x, j) => (j === i ? domains[i + 1] : j === i + 1 ? domains[i] : x).id) }); }, "已调整顺序")}>↓</button></td>
                <td><b>{d.name as string}</b></td>
                <td>{groupName(d.group_id)}<br /><span className="muted">{groupMode(d.group_id) === "time" ? "按时间切换" : "按负载均衡"}</span></td>
                <td>{d.zone as string || "自动发现"}</td>
                <td>{dotEl(cur?.status_dot, cur?.status_detail || cur?.status_label || "")}　A　{(d.current_ip as string) || "—"}<br /><span className="muted">{d.fallback_active ? "备用地址生效中" : (cur?.name as string) || ""}{cur ? `（${cur.status_label || cur.instance_status || ""}）` : ""}</span></td>
                <td><span className="pill blue">仅 DNS</span><br /><span className="muted">TTL 自动</span></td>
                <td><button className="btn ghost" onClick={() => { if (confirm(`删除域名 ${d.name}？`)) run(() => del(`/api/domains/${d.id}`), "已删除"); }}>删除</button></td></tr>
              ); })}</tbody></table>
          <p className="muted">同名存在多条 A / AAAA / CNAME 记录时报告冲突，由用户整理。</p>
          <div className="row">
            <input id="dom-name" placeholder="域名，如 www.example.xyz" style={{ flex: 1 }} />
            <select id="dom-group" defaultValue="">
              <option value="">默认分组</option>
              {groups.map((g) => {
                const bound = domains.some((d) => (d.group_id || 1) === g.id);
                return <option key={g.id as number} value={g.id as number} disabled={bound}>{g.name as string}{bound ? "（已绑定域名，不可选）" : ""}</option>;
              })}
            </select>
            <button className="btn" onClick={() => {
              const name = (document.getElementById("dom-name") as HTMLInputElement).value.trim();
              if (!name) { setMsg("请填写域名"); return; }
              const gv = (document.getElementById("dom-group") as HTMLSelectElement).value;
              run(() => post("/api/domains", { name, zone: name.split(".").slice(-2).join("."), priority: domains.length, group_id: gv ? Number(gv) : 1 }), "域名已添加");
            }}>+ 添加域名</button>
          </div>
        </div>
      )}

      {tab === "groups" && (
        <div className="card"><h2>分组 {groups.length}</h2>
          <div className="notice">一个分组只选一种切换模式（单选）：按时间切换或按负载均衡，不同分组可以选不同模式。1 个分组最多绑定 1 个域名。阈值踢出、保活、备用地址不受切换模式影响。</div>
          {groups.map((g) => {
            const members = accounts.filter((a) => (a.group_id || 1) === g.id);
            const dom = domains.find((d) => (d.group_id || 1) === g.id);
            const cur = dom ? accounts.find((a) => a.id === dom.current_account_id) : null;
            return (
              <div key={g.id as number} className="card" style={{ marginBottom: 12 }}>
                <div className="row"><b>{g.name as string}</b>
                  {g.id === 1 && <span className="pill blue">默认分组</span>}
                  <span className="pill">{g.switch_mode === "time" ? "按时间切换" : "按负载均衡"}</span>
                  <span className="spacer" />
                  <button className="btn ghost" onClick={() => { if (confirm(`删除分组 ${g.name}？须先移走其账户/域名。`)) run(() => del(`/api/groups/${g.id}`), "已删除"); }}>删除</button>
                </div>
                <div className="row" style={{ marginTop: 8 }}>
                  <label>切换模式（单选）：</label>
                  {(["time", "load_balance"] as const).map((m) => (
                    <label key={m} className="check" style={{ marginRight: 12 }}>
                      <input type="radio" name={`gm-${g.id}`} checked={g.switch_mode === m}
                        onChange={() => run(() => put(`/api/groups/${g.id}`, { switch_mode: m }), "已切换模式")} />
                      {m === "time" ? "按时间切换" : "按负载均衡"}
                    </label>
                  ))}
                </div>
                {g.switch_mode === "time" ? (
                  <p className="muted">时间参数：间隔 {g.interval_hours ?? settings.interval_hours ?? 6} 小时
                    {g.custom_interval_minutes ? `（自定义 ${g.custom_interval_minutes} 分钟）` : ""}
                    {g.fixed_switch_time ? `，固定时刻 ${g.fixed_switch_time}` : ""}，
                    夜间 {g.night_window_start ?? settings.night_window_start ?? "00:00"}–{g.night_window_end ?? settings.night_window_end ?? "04:00"}。
                    未填的继承全局设置。点下方“参数”可改：间隔/固定时刻/夜间窗口。</p>
                ) : (
                  <p className="muted">负载参数：差值模式 {g.switch_diff_mode ?? settings.switch_diff_mode ?? "percent"}，
                    {g.switch_diff_mode === "gb" || settings.switch_diff_mode === "gb"
                      ? `${g.switch_diff_gb ?? settings.switch_diff_gb ?? 10} GB`
                      : `${g.switch_diff_pct ?? settings.switch_diff_pct ?? 10}%`}。
                    未填的继承全局设置。</p>
                )}
                <div className="row">
                  <button className="btn ghost" onClick={() => {
                    const v = prompt("间隔小时（1/2/3/4/6/12/24，留空不改）", String(g.interval_hours ?? settings.interval_hours ?? 6));
                    if (v) run(() => put(`/api/groups/${g.id}`, { interval_hours: Number(v) }), "已更新");
                  }}>改间隔</button>
                  <button className="btn ghost" onClick={() => {
                    const v = prompt("切换差值 GB（GB 模式，留空不改）", String(g.switch_diff_gb ?? settings.switch_diff_gb ?? 10));
                    if (v) run(() => put(`/api/groups/${g.id}`, { switch_diff_mode: "gb", switch_diff_gb: Number(v) }), "已更新");
                  }}>改差值 GB</button>
                  <button className="btn ghost" onClick={() => {
                    const v = prompt("切换差值 %（% 模式，留空不改）", String(g.switch_diff_pct ?? settings.switch_diff_pct ?? 10));
                    if (v) run(() => put(`/api/groups/${g.id}`, { switch_diff_mode: "percent", switch_diff_pct: Number(v) }), "已更新");
                  }}>改差值 %</button>
                </div>
                <h3 style={{ marginTop: 12 }}>成员 IP（{members.length}）</h3>
                {members.length === 0 && <p className="muted">暂无成员，去「Ali 账户」添加并选本分组。</p>}
                {members.map((a) => (
                  <div key={a.id as number} className="row" style={{ padding: "4px 0" }}>
                    {dotEl(a.status_dot, a.status_detail || a.status_label || "")}
                    <b>{a.name as string}</b><span className="muted">{a.status_label || a.instance_status || "未知"}</span>
                    <span>{a.public_ip as string || "—"}</span>
                    <span className="muted">{a.usage_rate != null ? `${a.usage_rate}%` : "—"}</span>
                    {a.evicted ? <span className="pill gray">已跳出</span> : null}
                  </div>
                ))}
                <h3 style={{ marginTop: 12 }}>绑定域名（最多 1 个）</h3>
                {dom ? (
                  <div className="row"><b>{dom.name as string}</b><span className="muted">当前指向</span>
                    <span>{(dom.current_ip as string) || "未分配"}</span>
                    <span className="muted">{cur ? cur.name as string : (dom.fallback_active ? "备用地址生效中" : "")}</span></div>
                ) : <p className="muted">未绑定域名，去「域名管理」添加并选本分组。</p>}
              </div>
            );
          })}
          <div className="row">
            <button className="btn" onClick={() => {
              const name = prompt("分组名，如 分组C"); if (!name) return;
              const mode = prompt("切换模式：time 按时间切换 / load_balance 按负载均衡", "load_balance") || "load_balance";
              run(() => post("/api/groups", { name, switch_mode: mode }), "分组已创建");
            }}>+ 新建分组</button>
          </div>
        </div>
      )}

      {tab === "settings" && (
        <>
          <div className="card"><h2>调度参数</h2><p className="muted">账户未配置覆盖值时，使用以下全局设置。</p>
            <div className="notice" style={{ marginBottom: 0 }}><b>切换模式按分组设置，请到「分组管理」选择。</b>
              一个分组只选一种模式（按时间切换 / 按负载均衡），不同分组可以选不同模式。
              这里的时间/差值参数是<b>新建分组的默认值</b>，分组里未填的项继承这里的值。
              旧的全局切换模式只作默认值与无分组 legacy 回退，不再直接控制分组。
              <button className="btn" style={{ marginLeft: 8 }} onClick={() => setTab("groups")}>去分组管理</button>
            </div>
            <div className="form2">
              <div><label>月度流量额度 *（GiB）</label><input defaultValue={String(settings.quota_gb ?? 200)} id="s-quota" /></div>
              <div><label>流量保护阈值 *（%）</label><input defaultValue={String(settings.protection_threshold_pct ?? 95)} id="s-prot" /></div>
            </div>
            <div className="form2">
              <div><label>切换差值模式 *</label><select defaultValue={String(settings.switch_diff_mode ?? "percent")} id="s-diffmode">
                <option value="percent">按使用率差值（%）</option><option value="gb">按已用流量差值（GB）</option></select></div>
              <div><label>切换差值阈值 *（%）</label><input defaultValue={String(settings.switch_diff_pct ?? 10)} id="s-diff" /></div>
              <div><label>切换差值（GB，仅 GB 模式）</label><input defaultValue={String(settings.switch_diff_gb ?? 10)} id="s-diffgb" /></div>
              <div><p className="muted" style={{ marginTop: 28 }}>负载均衡：% 模式差值 ≥ 10% 切，GB 模式差值 ≥ 10 GB 切。</p></div>
              <div><label>API 刷新时间 *（分钟）</label>
                <select defaultValue={["5", "10", "15", "30", "60", "120"].includes(String(settings.api_refresh_minutes ?? 5)) ? String(settings.api_refresh_minutes ?? 5) : "custom"} id="s-refresh">
                  <option value="5">5 分钟</option><option value="10">10 分钟</option>
                  <option value="15">15 分钟</option><option value="30">30 分钟</option>
                  <option value="60">60 分钟</option><option value="120">120 分钟</option>
                  <option value="custom">自定义</option></select>
                <input placeholder="自定义分钟数" defaultValue={["5", "10", "15", "30", "60", "120"].includes(String(settings.api_refresh_minutes ?? 5)) ? "" : String(settings.api_refresh_minutes ?? 5)} id="s-refresh-custom" style={{ marginTop: 6 }} />
                <p className="muted">多久查一次阿里云 CDT/ECS/BSS 状态并重新评估。保存后下次调度按新间隔走（与旧的检查间隔是同一项，已合并）。</p></div>
              <div><label>备用地址 *</label><input defaultValue={String(settings.fallback_address ?? "")} id="s-fallback" /></div>
            </div>
            <h3 style={{ marginTop: 20 }}>阈值</h3>
            <p className="muted">到达阈值默认不停机，只是跳出 DDNS：实例踢出池子、不再参与 DDNS 解析，域名切到下一台用量最低的。恢复线 = 阈值 − 2%（如阈值 95%，回落到 93% 以下才恢复）；被踢出超 24 小时且用量低于阈值时兜底恢复；流量一更新就重新评估，恢复后同一轮重新参与分配。</p>
            <div>
              <label>阈值动作（三选一）</label>
              <div className="radio-group">
                {EXCL_OPTIONS.map((o) => (
                  <label key={o.value} className="radio"><input type="radio" name="s-excl" value={o.value} defaultChecked={(settings.exclusion_action ?? "none") === o.value} /><span><b>{o.title}</b><small>{o.sub}</small></span></label>
                ))}
              </div>
              <p className="muted">默认不停机，只跳出 DDNS；如需省流量费再选停机/节省停机。停机永远在切完 DNS 之后，同一台只停一次。</p>
            </div>
            <div style={{ marginTop: 12 }}>
              <label>阈值恢复</label>
              <div className="radio-group">
                <label className="radio"><input type="radio" name="s-recovery" value="start" defaultChecked={(settings.threshold_recovery_action ?? "start") === "start"} /><span><b>恢复时拉起（默认）</b><small>用量回落后自动开机，再重新参与 DDNS 分配</small></span></label>
                <label className="radio"><input type="radio" name="s-recovery" value="keep_stopped" defaultChecked={settings.threshold_recovery_action === "keep_stopped"} /><span><b>恢复入池但保持停机</b><small>只入池，等保活/手动再拉起</small></span></label>
              </div>
              <p className="muted">每日定时关机窗口内恢复拉起不执行（定时关机优先）；定时开机窗口内正常执行。</p>
            </div>
            <h3 style={{ marginTop: 20 }}>按时间切换</h3>
            <div>
            <div className="form2">
              <div><label>时间切换模式 *</label><select defaultValue={String(settings.time_switch_mode ?? "")} id="s-tsmode">
                <option value="">自动（夜间 + 固定时刻都可用）</option>
                <option value="off">关闭（不按时间切换）</option>
                <option value="night_only">仅夜间窗口</option>
                <option value="interval">仅间隔切换</option>
                <option value="fixed_time">仅固定时刻</option></select></div>
              <div><label>间隔切换周期（仅间隔模式）</label><select defaultValue={String(settings.interval_hours ?? 6)} id="s-ival">
                <option value="1">每 1 小时</option><option value="2">每 2 小时</option>
                <option value="3">每 3 小时</option><option value="4">每 4 小时</option>
                <option value="6">每 6 小时</option><option value="12">每 12 小时</option>
                <option value="24">每 24 小时</option><option value="0">自定义分钟数</option></select></div>
              <div><label>自定义间隔（分钟，周期选“自定义”时生效）</label><input defaultValue={String(settings.custom_interval_minutes ?? 0)} id="s-custmin" /></div>
              <div><label>固定每日切换时刻（自动/固定时刻模式，留空不启用）</label><input type="time" defaultValue={String(settings.fixed_switch_time ?? "")} id="s-fixed" /></div>
            </div>
            <label className="check"><input type="checkbox" defaultChecked={settings.night_optimize_enabled === "1"} id="s-night" /> 启用夜间主动优化</label>
            <div className="form2">
              <div><label>夜间窗口开始 *</label><input type="time" defaultValue={String(settings.night_window_start ?? "00:00")} id="s-nstart" /></div>
              <div><label>夜间窗口结束 *</label><input type="time" defaultValue={String(settings.night_window_end ?? "04:00")} id="s-nend" /></div>
            </div>
            <p className="muted">按时间切换仅在“按时间切换 / 两者都启用”时评估。夜间窗口内第一轮有效检查可切换至使用率更低的候选，每晚仅一次。间隔切换按上次切换锚点每 N 小时切一次（1 小时=每小时整点，6 小时=00/06/12/18），重启后按锚点继续，不重复切。固定时刻每天仅一次。按程序本地时间执行，支持跨午夜。</p>
            </div>
            <button className="btn" onClick={() => saveSettings({
              quota_gb: (document.getElementById("s-quota") as HTMLInputElement).value,
              protection_threshold_pct: (document.getElementById("s-prot") as HTMLInputElement).value,
              switch_diff_mode: (document.getElementById("s-diffmode") as HTMLSelectElement).value,
              switch_diff_pct: (document.getElementById("s-diff") as HTMLInputElement).value,
              switch_diff_gb: (document.getElementById("s-diffgb") as HTMLInputElement).value,
              time_switch_mode: (document.getElementById("s-tsmode") as HTMLSelectElement).value,
              interval_hours: (document.getElementById("s-ival") as HTMLSelectElement).value,
              custom_interval_minutes: (document.getElementById("s-custmin") as HTMLInputElement).value,
              exclusion_action: (document.querySelector('input[name="s-excl"]:checked') as HTMLInputElement)?.value || "none",
              threshold_recovery_action: (document.querySelector('input[name="s-recovery"]:checked') as HTMLInputElement)?.value || "start",
              api_refresh_minutes: (() => {
                const sel = (document.getElementById("s-refresh") as HTMLSelectElement).value;
                if (sel === "custom") return (document.getElementById("s-refresh-custom") as HTMLInputElement).value || "5";
                return sel;
              })(),
              fallback_address: (document.getElementById("s-fallback") as HTMLInputElement).value,
              night_optimize_enabled: (document.getElementById("s-night") as HTMLInputElement).checked ? "1" : "0",
              night_window_start: (document.getElementById("s-nstart") as HTMLInputElement).value,
              night_window_end: (document.getElementById("s-nend") as HTMLInputElement).value,
              fixed_switch_time: (document.getElementById("s-fixed") as HTMLInputElement).value,
              ...ddnsPatch(),
            })}>保存调度参数</button>
          </div>
          <div className="card"><h2>DDNS 设置</h2>
            <p className="muted">Cloudflare Token 用于 DDNS 解析（Zone / 记录 ID 自动发现，IP 不变不改）。</p>
            <div className="form2">
              <div><label>Cloudflare API Token</label>
                <input type="password" id="cf-token" placeholder={settings.cloudflare_configured ? "已配置，留空保持不变" : "未配置，填写 Cloudflare API Token"} />
                <p className="muted">{settings.cloudflare_configured ? "已配置。留空保存时保持不变，只有填了新值才更新。" : "未配置。填写后才能执行 DDNS 更新。"}</p></div>
              <div><label>时区 *</label>
                <select defaultValue={String(settings.timezone ?? "Asia/Shanghai")} id="s-timezone">
                  {(timezones.length ? timezones : TZ_FALLBACK).map((tz) => (
                    <option key={tz.value} value={tz.value}>{tz.label}</option>
                  ))}
                </select>
                <p className="muted">当前：{String(settings.timezone ?? "Asia/Shanghai")}。夜间窗口、固定时刻、间隔锚点、每日开关机、日报推送时间都按此时区执行；保存后立即生效，下次调度按新时区走，不用重启。容器 TZ 环境变量只作后备默认值。</p></div>
            </div>
            <button className="btn" onClick={() => saveSettings(ddnsPatch())}>保存 DDNS 设置</button>
          </div>
          <div className="card"><h2>网页访问</h2><p className="muted">保存后在下次启动时生效。启动参数 --listen 优先用于本次启动。</p>
            <div className="form2">
              <div><label>监听地址 *</label><input defaultValue="0.0.0.0" disabled /></div>
              <div><label>监听端口 *</label><input defaultValue="1999" disabled /></div>
            </div>
            <p className="muted">监听地址/端口由启动参数决定（默认 0.0.0.0:1999），不在此页修改。</p>
          </div>
          <div className="card"><h2>管理员密码</h2><p className="muted">已启用登录验证。保存时需验证当前密码。新密码和确认留空保存时免登录。</p>
            <button className="btn" onClick={() => {
              const cur = prompt("当前密码（首次设置留空）") || "";
              const next = prompt("新密码（留空表示不改）") || "";
              if (!next) { setMsg("未修改密码"); return; }
              run(async () => { const r = await post("/api/auth/setup", { password: next }); return r; }, "管理员密码已设置");
            }}>设置 / 修改管理员密码</button>
          </div>
        </>
      )}

      {tab === "notify" && (
        <div className="card"><h2>通知设置</h2>
          <p className="muted">通知专用代理</p><p><b>{String(settings.notify_proxy || "http://127.0.0.1:7070")}</b></p>
          <div className="row"><span>E-Mail</span><span className="pill gray">{settings.email_enabled === "1" ? "已启用" : "未启用"}</span><span className="spacer" />
            <button className="btn ghost" onClick={() => saveSettings({ email_enabled: settings.email_enabled === "1" ? "0" : "1" })}>{settings.email_enabled === "1" ? "停用" : "启用"} E-Mail</button>
            <button className="btn ghost" onClick={() => run(() => post("/api/notify/test", { event: "error" }), "测试通知已发送")}>发送测试通知</button></div>
          <div className="row" style={{ marginTop: 12 }}><span>Telegram</span><span className="pill">{settings.telegram_enabled === "1" ? "已启用" : "未启用"}</span><span className="spacer" />
            <button className="btn ghost" onClick={() => saveSettings({ telegram_enabled: settings.telegram_enabled === "1" ? "0" : "1" })}>{settings.telegram_enabled === "1" ? "停用" : "启用"} Telegram</button>
            <button className="btn ghost" onClick={() => run(() => post("/api/notify/test", { event: "error" }), "测试通知已发送")}>发送测试通知</button></div>
          <h3 style={{ marginTop: 20 }}>通知事件</h3>
          <p className="muted">应用于所有已启用渠道。关闭期间仍跟踪状态，重新开启不补发历史事件。</p>
          {(["traffic_protection", "dns_change", "fallback", "exclusion", "error", "recovery"] as const).map((k) => (
            <label className="check" key={k}><input type="checkbox" checked={settings[`notify_${k}`] === "1"}
              onChange={(e) => saveSettings({ [`notify_${k}`]: e.target.checked ? "1" : "0" })} />
              {({ traffic_protection: "流量保护", dns_change: "DNS 指向变化", fallback: "备用地址启用及恢复", exclusion: "排除操作完成", error: "查询及操作异常", recovery: "故障恢复" } as Any)[k]}</label>
          ))}
          <p className="muted">持续故障仅在首次发生和恢复时通知。测试使用已保存的配置，调度暂停时也可发送。</p>
        </div>
      )}
      {tab === "notify" && (
        <div className="card"><h2>每日财报推送</h2>
          <p className="muted">每天在推送时间推送一次：标题、日期、逐账户状态 / 流量 / 费用 / 余额 / 评论。只推已启用的 Telegram / 邮件 / Webhook 渠道。“立即推送日报”随时可手动推一次，不占用当天的定时一次（手动推完，定时时间到了照常再推；定时已推过，当天仍可手动再推）。</p>
          <label className="check"><input type="checkbox" defaultChecked={settings.daily_report_enabled !== "0"} id="dr-enabled" /> 启用每日财报推送</label>
          <div className="form2">
            <div><label>推送时间 *</label><input type="time" defaultValue={String(settings.daily_report_time ?? "00:00")} id="dr-time" />
              <p className="muted">每天这个时间推送一次。改时间后点保存才生效；若改到的时间当天已过，等明天再推，不会当天补推。</p></div>
            <div><label>推送渠道</label><p className="muted" style={{ marginTop: 8 }}>复用上方 Telegram / 邮件开关与 Webhook 设置，只推已启用的渠道。</p></div>
          </div>
          <div className="row">
            <button className="btn" onClick={() => saveSettings({
              daily_report_enabled: (document.getElementById("dr-enabled") as HTMLInputElement).checked ? "1" : "0",
              daily_report_time: (document.getElementById("dr-time") as HTMLInputElement).value || "00:00",
            })}>保存财报设置</button>
            <button className="btn ghost" onClick={() => run(async () => {
              const r = await post("/api/notify/test-daily", {}) as Any;
              setPreview(r?.preview || null);
              return r;
            }, "测试财报已发送")}>发送测试财报</button>
            <button className="btn" onClick={() => run(async () => {
              const r = await post("/api/notify/send-daily", {}) as Any;
              setPreview(r?.preview || null);
              return r;
            }, "日报已手动推送")}>立即推送日报</button>
            <button className="btn ghost" onClick={() => { get("/api/notify/daily-preview").then((r) => setPreview(r as Any)).catch(() => {}); }}>刷新预览</button>
          </div>
          {preview && (
            <div style={{ marginTop: 16 }}>
              <h3>推送预览（按实际推送排版：标题 16px / 正文 14px / 日期 12px，手机端不溢出）</h3>
              <div className="report-card">
                <div className="report-title">{String(preview.text || "").split("\n")[0]}</div>
                <div className="report-date">{String(preview.text || "").split("\n")[1] || ""}</div>
                <div className="report-body">{String(preview.text || "").split("\n").slice(2).join("\n")}</div>
              </div>
              <p className="muted">预览为第一条消息；账户多于 5 个时会自动分条发送。</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
