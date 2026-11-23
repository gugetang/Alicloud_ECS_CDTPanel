"""调度引擎（核心，可纯函数测试，不依赖 DB / 云 API）。

规则（与用户截图 + 文字说明一致）：
1. 排序：按流量使用率升序；域名按优先级顺序，依次分给使用率最低的可分配账户；
   可分配 VPS 少于域名时按顺序循环使用。
2. 保活：被分配的 VPS 若为 Stopped → 下发开机，确认 Running 后才切 DNS。
3. 阈值踢出（两步，先踢后停，可选停）：
   使用率 >= 流量保护阈值（默认 95%）→ 该账户先退出分配池（本轮不再被选中），
   其域名先切到下一个最低账户（DNS 切换在前），然后才按排除操作决定是否停机：
   exclusion=none → 只踢出、不停机，实例保持 Running，用量回落后自动恢复入池；
   exclusion=stop → 普通停机；stop_charging → 节省停机。
   全池超阈值 → 域名指到备用地址，通知「备用地址启用」。
   踢出不是持久状态：每轮按当前用量重算，低于阈值即自动恢复可分配。
0. 切换模式按分组（一个分组只选一种模式，time | load_balance | dynamic，
   分组层无 both）：
   分组 A（IP1–IP4）只能选定时轮巡、负载均衡、动态切换其中一种，不同分组
   可以选不同模式。time=仅定时轮巡（规则 5），差值切换关闭；load_balance=
   仅负载均衡（规则 4），定时轮巡关闭；dynamic=仅动态切换（规则 4b），
   定时轮巡与差值切换都关闭。旧的全局 switch_mode（time | load_balance |
   dynamic | both，默认 both）只作为无分组 legacy 调用与新建分组的
   默认模式，不再是分组账户的有效控制。
   阈值踢出（规则 3）、保活（规则 2）、首次分配、备用地址（规则 6）是
   保护与核心行为，不受切换模式影响，在各分组、各模式下都生效。
0b. 1 分组 = 1 域名：一个分组最多绑定 1 个域名，一个域名只属于 1 个分组。
   分组内调度就是“1 个域名 ← 该组 IP 池里用量最低的可分配 IP”，不再循环
   分配；多域名循环分配只保留在无分组 legacy 路径里。
4. 负载均衡：差值模式 percent（默认）时当班使用率 - 候选使用率 >= 切换阀值
   （默认 10%）→ 切 DNS；gb 模式时当班已用 GB - 候选已用 GB >= 切换阀值
   （默认 10 GB）→ 切 DNS，与百分比无关。仅在 switch_mode 为
   load_balance 或 both 时评估。
4b. 动态切换：仅在 switch_mode 为 dynamic 时评估。按动态间隔（分钟，
   默认 30，最少 30，可选 30/60/90/120/自定义）切到剩余流量最多的
   服务器（池内用量最低），不看差值阈值；间隔按上次切换锚点计时，
   首次检查建锚点、本轮不切，重启后按持久化的锚点继续，不重复切。
5. 定时轮巡（模式 time_switch_mode，""=自动），仅在 switch_mode 为
   time 或 both 时评估：
   夜间窗口（默认 00:00–04:00）内每晚第一次有效检查，不论差值直接切到最低
   账户，每晚仅一次；固定每日切换时刻（fixed_switch_time，如 "03:00"），
   当天仅一次；间隔切换（interval）按上次切换锚点每 N 小时切一次，
   N 可选 1/2/3/4/6/12/24 小时或自定义分钟数，整点对齐（1h=每小时，
   6h=00/06/12/18），重启后按持久化的锚点继续，不重复切。
6. 备用地址：无可分配 VPS 时指到备用地址并通知；有 VPS 可用时恢复并通知。
7. 保守原则：流量或状态未知 → 不踢出、不切换、不停机，只记异常事件。
   只有确认目标 Running 且拿到公网 IP 后才改 DNS。

plan_reconcile() 是纯函数：输入快照 → 输出 Plan；
execute_plan() 执行 Plan，dry_run 时不调任何云/DNS/通知。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 数据视图（调度器把 DB 行 + 云端快照转成这些结构再交给引擎）
# ---------------------------------------------------------------------------


@dataclass
class GroupView:
    """一个分组：IP 池 + 最多 1 个域名 + 只选一种切换模式（无 both）。"""
    id: int
    name: str = ""
    # time | load_balance | dynamic（分组层无 both）；None = 跟随系统（继承全局）
    switch_mode: str | None = "load_balance"
    # 以下全部可空 = 继承全局设置
    interval_hours: int | None = None
    custom_interval_minutes: int | None = None
    interval_start_time: str | None = None  # "HH:MM:SS"，空=继承全局（默认 00:00:00）
    fixed_switch_time: str | None = None
    night_window_start: str | None = None
    night_window_end: str | None = None
    night_optimize_enabled: bool | None = None
    switch_diff_mode: str | None = None
    switch_diff_gb: float | None = None
    switch_diff_pct: float | None = None
    time_switch_mode: str | None = None
    # 动态切换间隔（分钟）：仅 dynamic 模式用；None=用默认 30，最少 30
    dynamic_interval_minutes: int | None = None
    # 阈值策略（分组级）：None/""=继承全局设置；exclusion_action=none/stop/
    # stop_charging，recovery_action=start/keep_stopped
    exclusion_action: str | None = None
    recovery_action: str | None = None
    paused: bool = False  # 停止：域名不再解析（指向 0.0.0.0），不分配实例

    def __post_init__(self):
        # switch_mode 为 None = 跟随系统，保留 None（继承全局）；其余非法值回退
        if self.switch_mode is None:
            return
        m = (self.switch_mode or "load_balance").strip().lower()
        self.switch_mode = m if m in ("time", "load_balance", "dynamic") else "load_balance"


@dataclass
class AccountView:
    id: int
    name: str
    quota_gb: float = 200.0
    protection_threshold_pct: float | None = None
    switch_diff_pct: float | None = None
    switch_diff_mode: str | None = None   # percent | gb（None=继承全局）
    switch_diff_gb: float | None = None   # gb 模式下的差值（GB）
    # 分组级阈值动作（由调度器按所属分组填入；None/""=跟随全局）
    exclusion_action: str | None = None
    priority: int = 0
    used_gb: float | None = None          # None = 未知
    status: str = ""                       # Running / Stopped / "" 未知
    public_ip: str = ""
    group_id: int | None = None            # None=默认分组
    # 多对多成员关系：一个实例可同时属于多个分组；空列表时回退 group_id
    group_ids: list[int] = field(default_factory=list)
    # 每日定时开关机（未启用=始终在窗口内；启用时仅 [开机, 关机) 内可分配）
    daily_schedule_enabled: bool = False
    daily_on_time: str = "08:00"
    daily_off_time: str = "20:00"
    # 阈值恢复操作（分组级，由调度器按所属分组填入）：
    # None/""=跟随全局 threshold_recovery_action；
    # start=恢复时拉起；keep_stopped=恢复入池但保持停机
    recovery_action: str | None = None
    # 是否参与 DDNS（分组管理里点选，多选；取消的实例不参与域名分配）
    ddns_enabled: bool = True

    @property
    def usage_rate(self) -> float | None:
        if self.used_gb is None or not self.quota_gb:
            return None
        return self.used_gb / self.quota_gb * 100.0


@dataclass
class DomainView:
    id: int
    name: str
    zone: str = ""
    priority: int = 0
    record_type: str = "A"
    ttl: int = 1
    proxied: bool = False
    current_account_id: int | None = None
    current_ip: str = ""
    fallback_active: bool = False
    enabled: bool = True
    group_id: int | None = None            # None=默认分组；1 分组最多 1 域名


@dataclass
class Config:
    switch_mode: str = "both"  # time | load_balance | dynamic | both（默认 both）
    quota_gb: float = 200.0
    protection_threshold_pct: float = 95.0
    switch_diff_pct: float = 10.0
    switch_diff_mode: str = "percent"     # percent | gb
    switch_diff_gb: float = 10.0
    exclusion_action: str = "none"        # none | stop | stop_charging
    check_interval_minutes: int = 60
    night_optimize_enabled: bool = True
    night_window_start: str = "00:00"
    night_window_end: str = "04:00"
    fixed_switch_time: str = ""           # "" = 不启用
    # ""=自动（夜间+固定时刻都可用）；off/night_only/interval/fixed_time=显式模式
    time_switch_mode: str = ""
    interval_hours: int = 6               # 1/2/3/4/6/12/24；0=用自定义分钟数
    custom_interval_minutes: int = 0
    interval_start_time: str = "00:00:00"  # 间隔切换的开始时间（锚点）：切换时刻=开始时间+k*间隔
    # 动态切换间隔（分钟）：仅 dynamic 模式用；默认 30，最少 30
    dynamic_interval_minutes: int = 30
    fallback_address: str = "fallback.example.xyz"

    @classmethod
    def from_settings(cls, s: dict) -> "Config":
        def f(key, default):
            try:
                return float(s.get(key, default))
            except (TypeError, ValueError):
                return float(default)
        _mode = (s.get("switch_mode") or "both").strip().lower()
        if _mode not in ("time", "load_balance", "dynamic", "both"):
            _mode = "both"
        return cls(
            switch_mode=_mode,
            quota_gb=f("quota_gb", 200),
            protection_threshold_pct=f("protection_threshold_pct", 95),
            switch_diff_pct=f("switch_diff_pct", 10),
            switch_diff_mode=(s.get("switch_diff_mode") or "percent").strip() or "percent",
            switch_diff_gb=f("switch_diff_gb", 10),
            exclusion_action=_norm_exclusion(s.get("exclusion_action") or "none"),
            # API 刷新时间（已与旧 check_interval_minutes 合并为同一项）：
            # 读新字段，旧库无新字段时回退旧字段。
            check_interval_minutes=int(f("api_refresh_minutes",
                                         f("check_interval_minutes", 60))),
            night_optimize_enabled=(s.get("night_optimize_enabled", "1") == "1"),
            night_window_start=(s.get("night_window_start") or "00:00").strip(),
            night_window_end=(s.get("night_window_end") or "04:00").strip(),
            fixed_switch_time=(s.get("fixed_switch_time") or "").strip(),
            time_switch_mode=(s.get("time_switch_mode") or "").strip(),
            interval_hours=int(f("interval_hours", 6)),
            custom_interval_minutes=int(f("custom_interval_minutes", 0)),
            interval_start_time=(s.get("interval_start_time") or "00:00:00").strip() or "00:00:00",
            dynamic_interval_minutes=max(30, int(f("dynamic_interval_minutes", 30))),
            fallback_address=(s.get("fallback_address") or "fallback.example.xyz").strip(),
        )


@dataclass
class Action:
    kind: str          # start | stop | dns | dns_fallback | dns_restore
    domain_id: int | None = None
    account_id: int | None = None
    target_ip: str = ""
    reason: str = ""
    detail: str = ""
    stop_charging: bool = False  # 仅 stop 动作有效：True=节省停机


@dataclass
class Plan:
    assignments: dict[int, int | None] = field(default_factory=dict)  # domain_id -> account_id / None=fallback
    actions: list[Action] = field(default_factory=list)
    notifications: list[tuple[str, str, str]] = field(default_factory=list)  # (event, title, body)
    evicted_account_ids: list[int] = field(default_factory=list)
    fallback_domain_ids: list[int] = field(default_factory=list)
    paused_domain_ids: list[int] = field(default_factory=list)  # 已停止分组的域名：DNS 指向 0.0.0.0
    night_done_date: str = ""   # 本轮触发夜间优化的日期（YYYY-MM-DD），否则 ""
    fixed_done_date: str = ""
    time_switch_at: dt.datetime | None = None  # 本轮触发间隔切换的时刻（需持久化为锚点）
    interval_anchor_at: dt.datetime | None = None  # 本轮新建的间隔锚点（首次检查未到点时）
    summary: str = ""
    # 按组输出（分组路径用；legacy 路径为空，由标量字段承载）
    night_done_by_group: dict[int, str] = field(default_factory=dict)
    fixed_done_by_group: dict[int, str] = field(default_factory=dict)
    time_switch_by_group: dict[int, dt.datetime] = field(default_factory=dict)
    interval_anchor_by_group: dict[int, dt.datetime] = field(default_factory=dict)
    group_ids: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def usage_rate(used_gb: float | None, quota_gb: float) -> float | None:
    if used_gb is None or not quota_gb:
        return None
    return used_gb / quota_gb * 100.0


def _parse_hhmm(value: str) -> tuple[int, int]:
    h, m = (value or "00:00").split(":")
    return int(h), int(m)


def in_night_window(now: dt.datetime, start: str, end: str) -> bool:
    sh, sm = _parse_hhmm(start)
    eh, em = _parse_hhmm(end)
    t = (now.hour, now.minute)
    s, e = (sh, sm), (eh, em)
    if s == e:
        return False
    if s < e:
        return s <= t < e
    return t >= s or t < e  # 跨午夜


def _eff_protection(a: AccountView, cfg: Config) -> float:
    return a.protection_threshold_pct if a.protection_threshold_pct is not None else cfg.protection_threshold_pct


def _eff_diff(a: AccountView, cfg: Config) -> float:
    return a.switch_diff_pct if a.switch_diff_pct is not None else cfg.switch_diff_pct


def _eff_diff_mode(a: AccountView, cfg: Config) -> str:
    mode = (a.switch_diff_mode or cfg.switch_diff_mode or "percent").strip().lower()
    return mode if mode in ("percent", "gb") else "percent"


def _eff_diff_gb(a: AccountView, cfg: Config) -> float:
    return a.switch_diff_gb if a.switch_diff_gb is not None else cfg.switch_diff_gb


def _group_config(group: GroupView | None, cfg: Config) -> Config:
    """分组的有效配置：分组字段非空时覆盖全局，否则继承全局。
    分组 switch_mode 只取 time/load_balance/dynamic，None=跟随系统（沿用全局
    switch_mode，both 仍合法）；group 为 None 时沿用全局 switch_mode（legacy
    路径，both 仍合法）。time 模式的分组未显式设置
    time_switch_mode 时默认按 interval 走——分组界面只剩间隔周期这一项，
    间隔就是分组定时轮巡的机制；否则默认 auto 下间隔永不触发，间隔周期
    形同虚设。显式设置过的（auto/night_only/fixed_time/off）不受影响。"""
    if group is None:
        return cfg
    import dataclasses
    g = (dataclasses.replace(cfg, switch_mode=group.switch_mode)
         if group.switch_mode is not None else dataclasses.replace(cfg))
    if group.interval_hours is not None:
        g.interval_hours = group.interval_hours
    if group.custom_interval_minutes is not None:
        g.custom_interval_minutes = group.custom_interval_minutes
    if group.interval_start_time is not None:
        g.interval_start_time = group.interval_start_time
    if group.fixed_switch_time is not None:
        g.fixed_switch_time = group.fixed_switch_time
    if group.night_window_start is not None:
        g.night_window_start = group.night_window_start
    if group.night_window_end is not None:
        g.night_window_end = group.night_window_end
    if group.night_optimize_enabled is not None:
        g.night_optimize_enabled = group.night_optimize_enabled
    if group.switch_diff_mode is not None:
        g.switch_diff_mode = group.switch_diff_mode
    if group.switch_diff_gb is not None:
        g.switch_diff_gb = group.switch_diff_gb
    if group.switch_diff_pct is not None:
        g.switch_diff_pct = group.switch_diff_pct
    if group.time_switch_mode is not None:
        g.time_switch_mode = group.time_switch_mode
    elif g.switch_mode == "time":
        # 按有效模式判断（跟随系统且全局是 time 时同样默认 interval）
        g.time_switch_mode = "interval"
    if group.dynamic_interval_minutes is not None:
        g.dynamic_interval_minutes = group.dynamic_interval_minutes
    return g


def effective_dynamic_interval_minutes(cfg: Config) -> int:
    """动态切换的有效间隔（分钟）：默认 30，最少 30（小于 30 按 30 算）。"""
    try:
        n = int(cfg.dynamic_interval_minutes) if cfg.dynamic_interval_minutes is not None else 30
    except (TypeError, ValueError):
        n = 30
    return max(30, n)


def effective_interval_minutes(cfg: Config) -> int:
    """间隔切换的有效间隔（分钟）：dynamic 模式用动态间隔；否则自定义分钟数
    优先，再 interval_hours 小时。"""
    if (cfg.switch_mode or "").strip().lower() == "dynamic":
        return effective_dynamic_interval_minutes(cfg)
    if cfg.custom_interval_minutes and cfg.custom_interval_minutes > 0:
        return int(cfg.custom_interval_minutes)
    if cfg.interval_hours and cfg.interval_hours > 0:
        return int(cfg.interval_hours) * 60
    return 360  # 兜底 6 小时


def _parse_hhmmss(value: str | None) -> tuple[int, int, int] | None:
    """解析开始时间 HH:MM:SS（也接受 HH:MM），非法返回 None。"""
    if not value:
        return None
    parts = str(value).strip().split(":")
    try:
        if len(parts) == 2:
            h, m, s = int(parts[0]), int(parts[1]), 0
        elif len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        else:
            return None
    except ValueError:
        return None
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
        return None
    return h, m, s


def _latest_scheduled_moment(now: dt.datetime, start: tuple[int, int, int],
                             minutes: int) -> dt.datetime:
    """开始时间 + k*间隔，<= now 的最近一个切换时刻。"""
    h, m, s = start
    base = now.replace(hour=h, minute=m, second=s, microsecond=0)
    if base > now:
        base -= dt.timedelta(days=1)
    step = dt.timedelta(minutes=minutes)
    while base + step <= now:
        base += step
    return base


def interval_anchor_for(now: dt.datetime, cfg: Config) -> dt.datetime | None:
    """首次间隔检查时应建立的锚点：有开始时间时取上一个切换时刻，
    否则 None（调用方用 now 作锚点）。"""
    start = _parse_hhmmss(cfg.interval_start_time)
    if start is None:
        return None
    return _latest_scheduled_moment(now, start, effective_interval_minutes(cfg))


def interval_due(now: dt.datetime, anchor: dt.datetime | None, cfg: Config) -> bool:
    """间隔是否到点。锚点存在时：now - anchor >= 间隔即到点（按上次切换锚点计时）。
    锚点不存在时：有开始时间则看是否落在开始时间+k*间隔的切换时刻附近
    （1 分钟内），否则不到点、由调用方把上一个切换时刻存为锚点；无开始时间
    时沿用旧逻辑——自定义分钟无整点对齐概念，固定小时看整点对齐
    （1h=每小时整点，6h=00/06/12/18，24h=每天 00:00）。"""
    minutes = effective_interval_minutes(cfg)
    if anchor is None:
        start = _parse_hhmmss(cfg.interval_start_time)
        if start is not None:
            s = _latest_scheduled_moment(now, start, minutes)
            return (now - s) < dt.timedelta(minutes=1)
        if cfg.custom_interval_minutes and cfg.custom_interval_minutes > 0:
            return False  # 自定义分钟无整点对齐概念，等锚点建立后再算
        hours = cfg.interval_hours or 6
        return now.minute == 0 and (now.hour % hours == 0 if hours else False)
    return (now - anchor) >= dt.timedelta(minutes=minutes)


def _norm_exclusion(value: str | None) -> str:
    """阈值动作归一化：evict_ddns 是 none（踢出DDNS）的别名，只存一档的值。"""
    v = (value or "none").strip().lower()
    if v == "evict_ddns":
        return "none"
    return v if v in ("none", "stop", "stop_charging") else "none"


def _eff_exclusion(a: AccountView, cfg: Config) -> str:
    return _norm_exclusion(a.exclusion_action or cfg.exclusion_action or "none")


# 阈值恢复滞后值（防抖动）：已踢出的账户只有回落到 阈值 - 2% 以下才算恢复，
# 介于恢复线与阈值之间仍视为踢出，避免在阈值上下反复横跳踢出又拉起。
RECOVERY_HYSTERESIS_PCT = 2.0


def is_in_daily_window(a: AccountView, now: dt.datetime) -> bool:
    """每日定时开关机窗口：未启用=始终在窗口内；启用时仅 [开机, 关机) 内在窗口。

    关机时间 ≤ 开机时间表示跨午夜（如 20:00 开机、次日 08:00 关机）。
    窗口外：不参与分配、保活不拉起，调度器在关机时间停机、开机时间开机。
    """
    if not a.daily_schedule_enabled:
        return True
    on_h, on_m = _parse_hhmm(a.daily_on_time or "08:00")
    off_h, off_m = _parse_hhmm(a.daily_off_time or "20:00")
    on_dt = now.replace(hour=on_h, minute=on_m, second=0, microsecond=0)
    off_dt = now.replace(hour=off_h, minute=off_m, second=0, microsecond=0)
    if off_dt <= on_dt:
        off_dt += dt.timedelta(days=1)
    if now < on_dt:
        on_dt -= dt.timedelta(days=1)
        off_dt -= dt.timedelta(days=1)
    return on_dt <= now < off_dt


def is_allocatable(a: AccountView, cfg: Config, now: dt.datetime | None = None) -> bool:
    """可分配：已选中参与 DDNS、流量已知、未超保护阈值、状态已知
    （Running/Stopped 均可，Stopped 由保活拉起）、在每日开机窗口内
    （now 给出时才判断窗口；不给 now 时跳过窗口判断）。"""
    if not a.ddns_enabled:
        return False
    rate = a.usage_rate
    if rate is None:
        return False
    if rate >= _eff_protection(a, cfg):
        return False
    if a.status not in ("Running", "Stopped"):
        return False
    if now is not None and not is_in_daily_window(a, now):
        return False
    return True


def sorted_accounts(accounts: list[AccountView]) -> list[AccountView]:
    """使用率升序；未知排最后；同率按 priority / id。"""
    return sorted(accounts, key=lambda a: (
        a.usage_rate is None, a.usage_rate or 0.0, a.priority, a.id))


def assign_domains(domains: list[DomainView], accounts: list[AccountView],
                   cfg: Config, now: dt.datetime | None = None,
                   exclude_ids: set[int] | None = None) -> dict[int, int]:
    """域名按优先级 → 最低使用率账户；账户不足时循环。返回 domain_id -> account_id。
    exclude_ids：额外排除的账户（如仍在恢复滞后区间的已踢出账户）。"""
    pool = [a for a in sorted_accounts(accounts)
            if a.id not in (exclude_ids or set()) and is_allocatable(a, cfg, now)]
    out: dict[int, int] = {}
    if not pool:
        return out
    for i, d in enumerate(sorted([d for d in domains if d.enabled],
                                key=lambda x: (x.priority, x.id))):
        out[d.id] = pool[i % len(pool)].id
    return out


# ---------------------------------------------------------------------------
# 纯函数：排班
# ---------------------------------------------------------------------------

def _maybe_stop_evicted(plan: Plan, current: AccountView, cfg: Config,
                        stopped_ids: set[int], domain: DomainView) -> None:
    """阈值踢出的第二步（可选停机）：DNS 切换动作已先于本调用入队，
    这里只按排除操作决定是否追加停机。exclusion=none → 不追加、不停机，
    实例保持 Running，用量回落后自动恢复入池。同一账户只停一次。"""
    if current.id in stopped_ids:
        return
    excl = _eff_exclusion(current, cfg)
    if excl == "none":
        # 只踢出、不停机：不追加任何停机动作；“已踢出”通知由调用方统一发，
        # 这里只记已处理，避免同一账户多域名重复处理。
        stopped_ids.add(current.id)
        return
    if excl in ("stop", "stop_charging"):
        stopped_ids.add(current.id)
        plan.actions.append(Action("stop", domain_id=domain.id, account_id=current.id,
                                   reason="exclusion", stop_charging=(excl == "stop_charging"),
                                   detail=f"{current.name} 排除操作："
                                          f"{'节省停机' if excl == 'stop_charging' else '停机'}"))
        plan.notifications.append(
            ("exclusion", "排除操作完成",
             f"{current.name} 已执行{'节省停机' if excl == 'stop_charging' else '停机'}"))


def _account_in_group(a: AccountView, gid: int) -> bool:
    """账户是否属于该分组：多对多关系 group_ids 为准；未填时回退主分组
    group_id（None=默认分组 1）。"""
    ids = getattr(a, "group_ids", None)
    if ids:
        return gid in ids
    return (a.group_id or 1) == gid


def _plan_single(accounts: list[AccountView], domains: list[DomainView],
                 cfg: Config, now: dt.datetime,
                 night_done_dates: set[str] | None = None,
                 fixed_done_dates: set[str] | None = None,
                 last_time_switch_at: dt.datetime | None = None,
                 grouped: bool = False,
                 previously_evicted_ids: set[int] | None = None) -> Plan:
    """单个调度单元（一个分组，或 legacy 的全量）的排班。
    grouped=True 时 1 分组=1 域名：只调度该组优先级最高的 1 个域名，
    目标永远是池内用量最低的可分配 IP，不循环；grouped=False（legacy）
    保留多域名按优先级循环分配。
    previously_evicted_ids：上一轮已踢出的账户（调度器经 flags 传入），
    用于恢复滞后判断。"""
    night_done_dates = night_done_dates or set()
    fixed_done_dates = fixed_done_dates or set()
    previously_evicted_ids = previously_evicted_ids or set()
    today = now.strftime("%Y-%m-%d")
    by_id = {a.id: a for a in accounts}
    plan = Plan()
    # 阈值踢出（带滞后/防抖动）：首次超阈值即踢出；已踢出的账户只有回落到
    # 恢复线（阈值 - RECOVERY_HYSTERESIS_PCT）以下才算恢复，介于恢复线与
    # 阈值之间仍视为踢出，避免在阈值上下反复横跳。流量未知时不踢出、不恢复。
    evicted_ids: set[int] = set()
    for a in accounts:
        rate = a.usage_rate
        if rate is None:
            continue
        thr = _eff_protection(a, cfg)
        if rate >= thr or (a.id in previously_evicted_ids
                           and rate >= thr - RECOVERY_HYSTERESIS_PCT):
            evicted_ids.add(a.id)
    plan.evicted_account_ids = sorted(evicted_ids)
    pool = [a for a in sorted_accounts(accounts)
            if a.id not in evicted_ids and is_allocatable(a, cfg, now)]
    pool_ids = {a.id for a in pool}

    # 顶层切换模式门控：时间触发只在 time/both 下评估，差值触发只在
    # load_balance/both 下评估，动态触发只在 dynamic 下评估（分组层无 both，
    # dynamic 不进 legacy both，避免改动旧行为）。阈值踢出/保活/首次分配/
    # 备用地址不受影响。
    time_enabled = cfg.switch_mode in ("time", "both")
    diff_enabled = cfg.switch_mode in ("load_balance", "both")
    dynamic_enabled = cfg.switch_mode == "dynamic"
    mode = (cfg.time_switch_mode or "").strip().lower() or "auto"
    night_active = (time_enabled
                    and mode in ("auto", "night_only")
                    and cfg.night_optimize_enabled
                    and in_night_window(now, cfg.night_window_start, cfg.night_window_end)
                    and today not in night_done_dates)
    fixed_due = False
    if time_enabled and mode in ("auto", "fixed_time") and cfg.fixed_switch_time:
        fh, fm = _parse_hhmm(cfg.fixed_switch_time)
        fixed_due = (now.hour, now.minute) >= (fh, fm) and today not in fixed_done_dates
    interval_due_flag = False
    if time_enabled and mode == "interval":
        if interval_due(now, last_time_switch_at, cfg):
            interval_due_flag = True
        elif last_time_switch_at is None:
            # 首次检查：建锚点，本轮不切；有开始时间时锚点=上一个切换时刻，
            # 下轮从这里开始按开始时间+k*间隔计时，不漂移
            plan.interval_anchor_at = interval_anchor_for(now, cfg) or now

    # 动态切换（dynamic）：按动态间隔切到剩余流量最多的服务器（池首位），
    # 不看差值阈值。间隔取动态间隔（默认 30 分钟，最少 30 分钟），与 time
    # 的间隔共用 last_time_switch 锚点；首次检查建锚点，本轮不切。
    dynamic_due_flag = False
    if dynamic_enabled:
        if interval_due(now, last_time_switch_at, cfg):
            dynamic_due_flag = True
        elif last_time_switch_at is None:
            plan.interval_anchor_at = interval_anchor_for(now, cfg) or now

    if grouped:
        # 1 分组=1 域名：只取优先级最高的 1 个启用域名，分给池内最低 IP。
        # 同组若误配第 2 个域名，本轮不调度它（DB/API 层已禁止这种情况）。
        enabled = sorted([d for d in domains if d.enabled],
                         key=lambda x: (x.priority, x.id))
        assignments = {}
        if enabled and pool:
            assignments[enabled[0].id] = pool[0].id
    else:
        assignments = assign_domains(domains, accounts, cfg, now,
                                     exclude_ids=evicted_ids)
    night_triggered = False
    fixed_triggered = False
    interval_triggered = False
    dynamic_triggered = False
    stopped_ids: set[int] = set()   # 同一账户多域名只停一次
    started_ids: set[int] = set()   # 同一账户多域名只拉起一次

    for d in sorted([d for d in domains if d.enabled], key=lambda x: (x.priority, x.id)):
        target_id = assignments.get(d.id)
        # 分组模式：同组第 2 个域名（DB/API 已禁止）本轮不调度、不进备用，
        # 只调度该组优先级最高的 1 个域名；池空时首个域名才走备用逻辑。
        if grouped and pool and d.id not in assignments:
            continue
        current = by_id.get(d.current_account_id) if d.current_account_id else None

        # --- 无可分配 VPS：备用地址 ---
        if target_id is None:
            plan.assignments[d.id] = None
            plan.fallback_domain_ids.append(d.id)
            if not d.fallback_active:
                plan.actions.append(Action("dns_fallback", domain_id=d.id,
                                           target_ip=cfg.fallback_address,
                                           reason="no_allocatable",
                                           detail=f"{d.name} 无可分配 VPS，切备用地址"))
                plan.notifications.append(
                    ("fallback", "备用地址已启用",
                     f"{d.name}：无可分配 VPS，已指向备用地址 {cfg.fallback_address}"))
            # 当前账户若因超阈值被踢出：DNS 已切到备用地址，停机仍按排除操作、
            # 且只停一次（_maybe_stop_evicted）；none=只踢出、不停机。
            if current is not None and current.id in plan.evicted_account_ids:
                plan.notifications.append(
                    ("traffic_protection", "流量保护：账户已跳出",
                     f"{current.name} 使用率 {current.usage_rate:.2f}% ≥ 阈值 "
                     f"{_eff_protection(current, cfg):.0f}%，{d.name} 已切到备用地址"
                     + ("；排除操作=无，不停机，用量回落后自动恢复。"
                        if _eff_exclusion(current, cfg) == "none" else "")))
                _maybe_stop_evicted(plan, current, cfg, stopped_ids, d)
            continue

        target = by_id[target_id]
        plan.assignments[d.id] = target_id

        # 恢复：之前在备用地址，现在有 VPS 了
        if d.fallback_active:
            plan.notifications.append(
                ("fallback", "备用地址已恢复",
                 f"{d.name}：已有可分配 VPS，恢复指向 {target.name}"))

        # --- 已是目标：只做保活 ---
        if current is not None and current.id == target_id:
            if current.status == "Stopped" and current.id not in started_ids:
                started_ids.add(current.id)
                plan.actions.append(Action("start", domain_id=d.id, account_id=current.id,
                                           reason="keepalive",
                                           detail=f"{current.name} 已停机，保活拉起"))
            elif (current.status == "Running" and current.public_ip
                  and current.public_ip != (d.current_ip or "")):
                # 实例已拿到新 IP（如重启后换 IP），但域名记录仍是旧 IP：
                # 即使还是同一台，也要重新同步 DNS，否则解析一直指向旧 IP。
                # ddns.sync 会先读 Cloudflare 现值，IP 一致时不发 PUT。
                plan.actions.append(Action("dns", domain_id=d.id, account_id=current.id,
                                           target_ip=current.public_ip, reason="ip_changed",
                                           detail=f"{d.name} IP 已变（{d.current_ip or '—'} → "
                                                  f"{current.public_ip}），重新同步"))
            continue

        # --- 需要切换：定原因 ---
        # 优先级：阈值踢出 > 每日窗口出窗 > 无人服务（首次分配） > 夜间 > 固定时刻 > 间隔 > 流量差值
        # 无人服务 = 当前实例不是池内 Running 实例（无指向、流量未知、已停机、
        # 未参与 DDNS、已踢出等）：域名实际上没人服务、池里都在排队时，直接分给
        # 池内剩余流量最多的（用量最低的），不受差值阈值限制——差值阈值只用来
        # 阻止“从正在服务的实例切走”，不用来阻止首次分配。
        serving = (current is not None and current.id in pool_ids
                   and current.status == "Running" and current.usage_rate is not None)
        reason = ""
        if current is not None and current.id in plan.evicted_account_ids:
            reason = "threshold_eviction"
        elif current is not None and not is_in_daily_window(current, now):
            # 当前账户已出每日开机窗口：域名切走（切到窗口内账户或备用地址），
            # 不等夜间/差值触发；目标停机时先保活拉起（见下方）。
            reason = "daily_window"
        elif not serving:
            reason = "initial_assign"
        elif night_active:
            reason = "night_optimize"
            night_triggered = True
        elif fixed_due:
            reason = "fixed_time"
            fixed_triggered = True
        elif interval_due_flag:
            reason = "interval_switch"
            interval_triggered = True
        elif dynamic_due_flag:
            # 动态切换：间隔到点即切到剩余流量最多的服务器，不看差值阈值。
            # 目标已是池首位（用量最低）；当前已是目标时上方的“已是目标”分支
            # 早已 continue，不会走到这里重复切。
            reason = "dynamic_switch"
            dynamic_triggered = True
        elif (diff_enabled and current is not None
              and current.usage_rate is not None and target.usage_rate is not None):
            if _eff_diff_mode(current, cfg) == "gb":
                gap_gb = (current.used_gb or 0.0) - (target.used_gb or 0.0)
                if gap_gb >= _eff_diff_gb(current, cfg):
                    reason = "traffic_diff_gb"
            else:
                diff = current.usage_rate - target.usage_rate
                if diff >= _eff_diff(current, cfg):
                    reason = "traffic_diff"

        if not reason:
            continue  # 差值不够，维持现状

        # 目标停机 → 先保活拉起；Running → 切 DNS；中间态 → 等，不切
        if target.status == "Stopped":
            if target.id not in started_ids:
                started_ids.add(target.id)
                plan.actions.append(Action("start", domain_id=d.id, account_id=target.id,
                                           reason="keepalive_before_switch",
                                           detail=f"切换目标 {target.name} 已停机，先保活拉起"))
            if target.public_ip:
                plan.actions.append(Action("dns", domain_id=d.id, account_id=target.id,
                                           target_ip=target.public_ip, reason=reason,
                                           detail=f"{d.name} 待 {target.name} Running 后切过去"))
            # 没拿到 IP 时只拉起不切 DNS（空 IP 会报 invalid_ip）；
            # 下轮 Running 拿到新 IP 后，“已是目标 + IP 已变”分支会重新同步。
        elif target.status == "Running":
            if not target.public_ip:
                continue  # 没拿到 IP 不切
            plan.actions.append(Action("dns", domain_id=d.id, account_id=target.id,
                                       target_ip=target.public_ip, reason=reason,
                                       detail=f"{d.name} → {target.name}（{reason}）"))
        else:
            continue  # Starting 等中间态：等下一轮

        if reason == "threshold_eviction" and current is not None:
            # 第一步已完成：DNS 切换动作先入队；第二步停机可选，见 _maybe_stop_evicted
            excl = _eff_exclusion(current, cfg)
            plan.notifications.append(
                ("traffic_protection", "流量保护：账户已跳出",
                 f"{current.name} 使用率 {current.usage_rate:.2f}% ≥ 阈值 "
                 f"{_eff_protection(current, cfg):.0f}%，{d.name} 已切到 {target.name}"
                 + ("；排除操作=无，只踢出、不停机，用量回落后自动恢复。"
                    if excl == "none" else "")))
            _maybe_stop_evicted(plan, current, cfg, stopped_ids, d)
        elif reason in ("traffic_diff", "traffic_diff_gb", "night_optimize",
                        "fixed_time", "interval_switch", "dynamic_switch",
                        "daily_window"):
            if reason == "daily_window":
                detail = (f"{d.name} → {target.name}（当前账户 {current.name} "
                          f"不在每日开机窗口内）")
            elif reason == "traffic_diff_gb":
                detail = (f"{d.name} → {target.name}（按流量差值切换："
                          f"{current.used_gb:.1f} - {target.used_gb:.1f} = "
                          f"{(current.used_gb or 0) - (target.used_gb or 0):.1f} GB "
                          f"≥ {_eff_diff_gb(current, cfg):.0f} GB）")
            elif reason == "interval_switch":
                detail = (f"{d.name} → {target.name}（间隔切换："
                          f"每 {effective_interval_minutes(cfg)} 分钟，"
                          "不论差值切到用量最低）")
            elif reason == "dynamic_switch":
                detail = (f"{d.name} → {target.name}（动态切换："
                          f"每 {effective_dynamic_interval_minutes(cfg)} 分钟，"
                          "切到剩余流量最多）")
            else:
                detail = f"{d.name} → {target.name}（{reason}）"
            plan.notifications.append(("dns_change", "DNS 指向变化", detail))

    if night_triggered:
        plan.night_done_date = today
    if fixed_triggered:
        plan.fixed_done_date = today
    if interval_triggered or dynamic_triggered:
        plan.time_switch_at = now
    plan.summary = (f"域名 {len([d for d in domains if d.enabled])} 个，"
                    f"可分配账户 {len(pool)} 个，踢出 {len(plan.evicted_account_ids)} 个，"
                    f"备用 {len(plan.fallback_domain_ids)} 个，动作 {len(plan.actions)} 条")
    return plan


def _lookup(mapping, gid, default):
    """按组查旗标：dict 按组 id 取，set/标量按 legacy 方式取。"""
    if isinstance(mapping, dict):
        return mapping.get(gid, default)
    return mapping if mapping is not None else default


def plan_reconcile(accounts: list[AccountView], domains: list[DomainView],
                   cfg: Config, now: dt.datetime,
                   night_done_dates: set[str] | None = None,
                   fixed_done_dates: set[str] | None = None,
                   last_time_switch_at: dt.datetime | None = None,
                   groups: list[GroupView] | None = None,
                   night_done_by_group: dict[int, set[str]] | None = None,
                   fixed_done_by_group: dict[int, set[str]] | None = None,
                   last_switch_by_group: dict[int, dt.datetime | None] | None = None,
                   previously_evicted_ids: set[int] | None = None,
                   ) -> Plan:
    """排班入口（保持向后兼容）。
    groups=None：legacy 单组全量路径，使用全局 switch_mode（含 both），
      多域名循环分配保留，旗标用标量参数。
    groups 给出：按组独立调度。每组只用自己的模式（time、load_balance 或
    dynamic，
      无 both），旗标按组命名空间（调用方用 night_done_by_group 等传入，
      也可用旧的 set/标量参数——此时各组共用同一份 legacy 旗标，仅用于
      兼容测试）。group_id 为 None 的账户/域名归默认分组（id=1）。
    previously_evicted_ids：上一轮已踢出的账户 id（调度器经 flags 传入），
      用于阈值恢复滞后判断（恢复线=阈值-2%）。
    阈值踢出/保活/首次分配/备用地址在各组、各模式下都生效。"""
    if groups is None:
        return _plan_single(accounts, domains, cfg, now,
                            night_done_dates, fixed_done_dates,
                            last_time_switch_at, grouped=False,
                            previously_evicted_ids=previously_evicted_ids)
    by_gid: dict[int, GroupView] = {g.id: g for g in groups}
    if 1 not in by_gid:
        by_gid[1] = GroupView(id=1, name="默认分组", switch_mode="load_balance")
    ordered_gids = sorted(by_gid)
    merged = Plan(group_ids=ordered_gids)
    for gid in ordered_gids:
        group = by_gid[gid]
        gcfg = _group_config(group, cfg)
        gaccs = [a for a in accounts if _account_in_group(a, gid)]
        # 未绑定域名（group_id NULL）不参与任何分组的调度
        gdoms = [d for d in domains if d.group_id == gid]
        if not gaccs and not gdoms:
            continue
        if group.paused:
            # 停止的分组：域名不再解析（指向 0.0.0.0），不分配实例
            for d in gdoms:
                if d.enabled:
                    merged.paused_domain_ids.append(d.id)
                    if d.current_ip != "0.0.0.0":
                        merged.actions.append(Action(kind="dns", domain_id=d.id,
                                                     account_id=None, target_ip="0.0.0.0",
                                                     reason="group_paused"))
            continue
        nd = _lookup(night_done_by_group if night_done_by_group is not None
                     else night_done_dates, gid, set())
        fd = _lookup(fixed_done_by_group if fixed_done_by_group is not None
                     else fixed_done_dates, gid, set())
        anchor = _lookup(last_switch_by_group if last_switch_by_group is not None
                         else last_time_switch_at, gid, None)
        sub = _plan_single(gaccs, gdoms, gcfg, now, nd, fd, anchor, grouped=True,
                           previously_evicted_ids=previously_evicted_ids)
        merged.assignments.update(sub.assignments)
        merged.actions.extend(sub.actions)
        merged.notifications.extend(sub.notifications)
        merged.evicted_account_ids.extend(sub.evicted_account_ids)
        merged.fallback_domain_ids.extend(sub.fallback_domain_ids)
        if sub.night_done_date:
            merged.night_done_by_group[gid] = sub.night_done_date
            merged.night_done_date = sub.night_done_date
        if sub.fixed_done_date:
            merged.fixed_done_by_group[gid] = sub.fixed_done_date
            merged.fixed_done_date = sub.fixed_done_date
        if sub.time_switch_at is not None:
            merged.time_switch_by_group[gid] = sub.time_switch_at
            merged.time_switch_at = sub.time_switch_at
        if sub.interval_anchor_at is not None:
            merged.interval_anchor_by_group[gid] = sub.interval_anchor_at
            merged.interval_anchor_at = sub.interval_anchor_at
    n_dom = len([d for d in domains if d.enabled])
    merged.summary = (f"分组 {len(ordered_gids)} 个，域名 {n_dom} 个，"
                      f"可分配账户 {len([a for a in accounts if is_allocatable(a, cfg, now)])} 个，"
                      f"踢出 {len(merged.evicted_account_ids)} 个，"
                      f"备用 {len(merged.fallback_domain_ids)} 个，"
                      f"动作 {len(merged.actions)} 条")
    return merged


# ---------------------------------------------------------------------------
# 执行 Plan（dry_run 时零调用）
# ---------------------------------------------------------------------------

class CloudOps:
    """调度器提供的云操作接口（测试用 Fake 替换）。"""
    def start(self, account: AccountView) -> None:
        raise NotImplementedError

    def stop(self, account: AccountView, stop_charging: bool) -> None:
        raise NotImplementedError


class DnsOps:
    def set_record(self, domain: DomainView, ip: str) -> bool:
        """返回 True 表示实际改了记录。"""
        raise NotImplementedError


def execute_plan(plan: Plan, accounts: list[AccountView], domains: list[DomainView],
                 cloud: CloudOps, dns: DnsOps,
                 notify_fn=None, dry_run: bool = False) -> dict:
    """三段式执行，强制顺序 start → dns/dns_fallback → stop：
    停机永远在 DNS 切换之后（即使 Plan 动作顺序被打乱），dry_run 直接返回，
    不调任何外部接口。"""
    if dry_run:
        return {"executed": [], "dry_run": True, "summary": plan.summary}
    by_acc = {a.id: a for a in accounts}
    by_dom = {d.id: d for d in domains}
    executed: list[str] = []
    # 1) 保活拉起
    for act in plan.actions:
        if act.kind == "start" and act.account_id in by_acc:
            cloud.start(by_acc[act.account_id])
            executed.append(f"start:{by_acc[act.account_id].name}")
    # 2) DNS 切换 / 备用地址
    for act in plan.actions:
        if act.kind in ("dns", "dns_fallback") and act.domain_id in by_dom:
            changed = dns.set_record(by_dom[act.domain_id], act.target_ip)
            executed.append(f"{act.kind}:{by_dom[act.domain_id].name}->{act.target_ip}"
                            f"{' changed' if changed else ' unchanged'}")
    # 3) 排除停机（最后，且仅当 Plan 里有 stop 动作，即排除操作 != none）
    for act in plan.actions:
        if act.kind == "stop" and act.account_id in by_acc:
            acc = by_acc[act.account_id]
            stop_charging = act.stop_charging or ("节省停机" in act.detail)
            cloud.stop(acc, stop_charging=stop_charging)
            executed.append(f"stop:{acc.name}")
    if notify_fn:
        for event, title, body in plan.notifications:
            notify_fn(event, title, body)
    return {"executed": executed, "dry_run": False, "summary": plan.summary}
