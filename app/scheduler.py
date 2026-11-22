"""后台调度线程：定期对账 + 手动触发 + 试算。

一轮流程：
1. 刷新云端快照（每账户 DescribeInstances + CDT 流量 + BSS 费用，可选）；
   任一查询失败 → 该账户流量/状态记未知，引擎保守维持，记异常事件。
2. engine.plan_reconcile() 出 Plan。
3. dry_run 时只返回 Plan，不执行；否则 execute_plan()，落库事件/采样/DNS 记录，
   更新 domains.current_account_id / current_ip / fallback_active。
4. 夜间/固定时刻的“已执行日期”写 flags，避免一晚重复切。
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from . import db, engine
from .aliyun import AliyunClient, AliyunError, brief_error
from .ddns import CloudflareClient
from .notify import Notifier
from .security import decrypt_secret
from . import settings as cfg

log = logging.getLogger("cdt.scheduler")

_stop = threading.Event()
_thread: threading.Thread | None = None
_wake = threading.Event()
_last_run: dict = {}
_lock = threading.Lock()


def _tz():
    """调度时区：优先读 settings 表的 timezone（UI 可改，立即生效）；
    无值/非法时回退环境变量 TZ，再回退 UTC+8。"""
    try:
        name = (db.get_settings().get("timezone") or "").strip() or cfg.TZ
        return ZoneInfo(name)
    except Exception:
        pass
    try:
        return ZoneInfo(cfg.TZ)
    except Exception:
        return dt.timezone(dt.timedelta(hours=8))


def _now() -> dt.datetime:
    return dt.datetime.now(_tz())


def _month_key(now: dt.datetime) -> str:
    return now.strftime("%Y-%m")


def _to_views(accounts: list[dict], settings: dict) -> list[engine.AccountView]:
    # 多对多成员关系一次取齐（group_ids 为准；未填时引擎回退主分组 group_id）
    try:
        _membership: dict[int, list[int]] = {}
        for row in db.list_account_groups():
            _membership.setdefault(int(row["account_id"]), []).append(int(row["group_id"]))
    except Exception:
        _membership = {}
    views = []
    for a in accounts:
        quota = a.get("quota_gb") if a.get("quota_gb") is not None else float(settings.get("quota_gb", 200))
        views.append(engine.AccountView(
            id=a["id"], name=a["name"], quota_gb=float(quota),
            protection_threshold_pct=a.get("protection_threshold_pct"),
            switch_diff_pct=a.get("switch_diff_pct"),
            switch_diff_mode=a.get("switch_diff_mode"),
            switch_diff_gb=a.get("switch_diff_gb"),
            exclusion_action=a.get("exclusion_action"),
            priority=a.get("priority") or 0,
            used_gb=a.get("traffic_used_gb"),
            status=a.get("instance_status") or "",
            public_ip=a.get("public_ip") or "",
            group_id=a.get("group_id"),
            group_ids=_membership.get(int(a["id"]), []),
            daily_schedule_enabled=bool(a.get("daily_schedule_enabled")),
            daily_on_time=a.get("daily_on_time") or "08:00",
            daily_off_time=a.get("daily_off_time") or "20:00",
            recovery_action=a.get("recovery_action"),
            ddns_enabled=bool(a.get("ddns_enabled", 1))))
    return views


def _to_domain_views(domains: list[dict]) -> list[engine.DomainView]:
    return [engine.DomainView(
        id=d["id"], name=d["name"], zone=d.get("zone") or "",
        priority=d.get("priority") or 0, record_type=d.get("record_type") or "A",
        ttl=d.get("ttl") or 1, proxied=bool(d.get("proxied")),
        current_account_id=d.get("current_account_id"),
        current_ip=d.get("current_ip") or "",
        fallback_active=bool(d.get("fallback_active")),
        enabled=bool(d.get("enabled", 1)),
        group_id=d.get("group_id")) for d in domains]


def _to_group_views(groups: list[dict]) -> list[engine.GroupView]:
    return [engine.GroupView(
        id=g["id"], name=g["name"], switch_mode=g.get("switch_mode") or "load_balance",
        interval_hours=g.get("interval_hours"),
        custom_interval_minutes=g.get("custom_interval_minutes"),
        interval_start_time=g.get("interval_start_time"),
        fixed_switch_time=g.get("fixed_switch_time"),
        night_window_start=g.get("night_window_start"),
        night_window_end=g.get("night_window_end"),
        night_optimize_enabled=(None if g.get("night_optimize_enabled") is None
                                else bool(g.get("night_optimize_enabled"))),
        switch_diff_mode=g.get("switch_diff_mode"),
        switch_diff_gb=g.get("switch_diff_gb"),
        switch_diff_pct=g.get("switch_diff_pct"),
        time_switch_mode=g.get("time_switch_mode"),
        paused=bool(g.get("paused"))) for g in groups]


def _flag_dt(key: str) -> dt.datetime | None:
    raw = db.get_flag(key)
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw)
    except ValueError:
        return None


def _refresh_one(a: dict, settings: dict) -> None:
    """刷新单个账户的云快照并写回 DB（DescribeInstances + CDT 流量 + BSS）。"""
    secret = decrypt_secret(a.get("access_key_secret_enc") or "")
    if not a.get("access_key_id") or not secret or not a.get("instance_id"):
        return
    try:
        client = AliyunClient(a, secret)
        inst = client.describe_instance()
        used = client.get_traffic_gb()
        updates: dict = {"instance_status": inst["status"],
                         "public_ip": inst["public_ip"],
                         "traffic_used_gb": used,
                         "last_seen_at": int(time.time())}
        if inst.get("instance_type"):
            # 只存 API 返回的原始规格；友好的 2C0.5G 式显示规格
            # 由用户在 instance_spec 里填，程序不凭空编造
            updates["instance_type"] = inst["instance_type"]
        if settings.get("billing_enabled", "1") == "1":
            bill = client.get_billing()
            if bill:
                updates["billing_month_cost"] = bill["cost"]
                updates["billing_currency"] = bill["currency"]
                if bill.get("balance") is not None:
                    updates["billing_balance"] = bill["balance"]
                    updates["billing_balance_currency"] = (
                        bill.get("balance_currency") or bill["currency"])
                updates["billing_updated_at"] = int(time.time())
        db.update_account(a["id"], updates)
        db.add_traffic_sample(a["id"], used, _month_key(_now()))
    except AliyunError as exc:
        # 保守：查不到就把流量记未知（None），引擎不会据此停机/切换
        db.update_account(a["id"], {"traffic_used_gb": None,
                                    "last_seen_at": int(time.time())})
        db.add_event("error", "查询异常",
                     f"{a['name']}：{brief_error(exc)}（已保守维持，不做切换）",
                     account_id=a["id"])
        log.warning("刷新 %s 失败: %s", a["name"], brief_error(exc))
    except Exception as exc:  # noqa: BLE001
        db.update_account(a["id"], {"traffic_used_gb": None,
                                    "last_seen_at": int(time.time())})
        db.add_event("error", "查询异常",
                     f"{a['name']}：{exc}（已保守维持，不做切换）",
                     account_id=a["id"])
        log.warning("刷新 %s 失败: %s", a["name"], exc)


def refresh_one_account(account_id: int) -> dict:
    """刷新单个账户（概览卡片/列表的“刷新”按钮用），返回更新后的账户行。"""
    a = db.get_account(account_id)
    if not a:
        raise ValueError("账户不存在")
    _refresh_one(a, db.get_settings())
    return db.get_account(account_id)


def refresh_cloud_status(settings: dict) -> list[dict]:
    """刷新全部账户快照并写回 DB。返回更新后的账户行。

    并行刷新：每账户独立调阿里云接口（DescribeInstances + CDT 流量 +
    BSS 费用，最多 3 次往返），串行时 N 个账户要 N×3 次往返，一轮
    耗时随账户数线性增长；线程池并行后一轮耗时接近最慢的 1 个账户。
    _refresh_one 内部已捕获全部业务异常；DB 写经 db._lock 串行，
    单连接跨线程共享安全；_refresh_one_safe 再兜一层防意外抛错
    导致整轮失败。"""
    accounts = db.list_accounts()
    if len(accounts) > 1:
        workers = min(REFRESH_MAX_WORKERS, len(accounts))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(lambda a: _refresh_one_safe(a, settings), accounts))
    else:
        for a in accounts:
            _refresh_one_safe(a, settings)
    return db.list_accounts()


# 并行刷新线程数：阿里云接口以网络等待为主，5 路并行足够把一轮
# 耗时压到接近单个账户；再多对总耗时改善有限，反而容易触发限流。
REFRESH_MAX_WORKERS = 5


def _refresh_one_safe(a: dict, settings: dict) -> None:
    """_refresh_one 的线程池包装：任何意外异常只记日志跳过，
    不让单个账户的失败拖垮整轮并行刷新。"""
    try:
        _refresh_one(a, settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("刷新 %s 失败（已跳过）: %s", a.get("name"), exc)


class _CloudOps(engine.CloudOps):
    def __init__(self, settings: dict):
        self.settings = settings

    def _client(self, account: engine.AccountView) -> AliyunClient:
        row = db.get_account(account.id)
        return AliyunClient(row, decrypt_secret(row.get("access_key_secret_enc") or ""))

    def start(self, account: engine.AccountView) -> None:
        self._client(account).start_instance()

    def stop(self, account: engine.AccountView, stop_charging: bool) -> None:
        self._client(account).stop_instance(stop_charging=stop_charging)


class _DnsOps(engine.DnsOps):
    def __init__(self, settings: dict):
        self.settings = settings
        self.cf = CloudflareClient(settings.get("cloudflare_token") or "")

    def set_record(self, domain: engine.DomainView, ip: str) -> bool:
        if not self.settings.get("cloudflare_token"):
            db.add_event("error", "DDNS 未配置", "Cloudflare Token 未配置，无法同步 DNS",
                         domain_id=domain.id)
            return False
        # 备用地址可能是域名：Cloudflare A 记录只能写 IP，备用域名需由用户在 CF
        # 上另建 CNAME；这里若备用值不是 IP，仍尝试按 A 记录写会失败并记事件。
        from .ddns import is_valid_ip
        if not is_valid_ip(ip):
            db.add_event("error", "备用地址不是 IP",
                         f"{domain.name} 备用地址 {ip} 不是 IPv4/IPv6，"
                         "请在 Cloudflare 上为其建 CNAME 或改填 IP", domain_id=domain.id)
            return False
        # Zone 取值优先级：DDNS 设置里填的域名（Zone 名）> 域名自带的 zone
        # 字段（添加域名时按后两段算）> 域名去掉第一段。配了域名就不用再靠
        # 后两段推算，二级/三级域名也能识别。
        zone = ((self.settings.get("cloudflare_zone") or "").strip()
                or domain.zone or domain.name.split(".", 1)[-1])
        res = self.cf.sync(domain.name, zone, ip, domain.record_type,
                           domain.ttl, domain.proxied)
        if res.changed:
            db.upsert_dns_record(domain.id, res.zone_id, res.record_id, ip)
            db.add_event("dns_change", "DNS 已同步",
                         f"{domain.name} → {ip}（{res.previous} → {ip}）",
                         domain_id=domain.id)
        elif res.status in ("failed", "no_record", "invalid_ip"):
            db.add_event("error", "DDNS 同步失败",
                         f"{domain.name} → {ip}：{res.detail or res.status}",
                         domain_id=domain.id)
        return res.changed


def run_once(dry_run: bool = False, refresh: bool = True,
             quiet: bool = False) -> dict:
    """跑一轮调度。dry_run=True 时不调 VPS/DNS，只返回 Plan（试算）。
    quiet=True：DDNS 检查用，不写“调度完成”事件、不建 job、不更新
    _last_run，避免每分钟一条日志刷屏。"""
    settings = db.get_settings()
    now = _now()
    if refresh and not dry_run:
        accounts = refresh_cloud_status(settings)
    else:
        accounts = db.list_accounts()
    domains = db.list_domains()
    cfg_obj = engine.Config.from_settings(settings)
    groups = _to_group_views(db.list_groups())
    # 旗标按组命名空间：night_done:<gid> / fixed_done:<gid> /
    # last_time_switch:<gid>；无分组 legacy 路径仍用旧的无后缀键。
    # 批量一次查齐（3×分组数 次 get_flag → 1 次查询）。
    _flag_keys = ([f"night_done:{g.id}" for g in groups]
                  + [f"fixed_done:{g.id}" for g in groups]
                  + [f"last_time_switch:{g.id}" for g in groups])
    _flags = db.get_flags(_flag_keys)
    night_by_group = {g.id: set(filter(None, [_flags.get(f"night_done:{g.id}", "")]))
                      for g in groups}
    fixed_by_group = {g.id: set(filter(None, [_flags.get(f"fixed_done:{g.id}", "")]))
                      for g in groups}
    anchor_by_group = {g.id: _flag_dt(_flags.get(f"last_time_switch:{g.id}", ""))
                       for g in groups}
    # 流量更新后自动重新加入 DDNS：每轮开始先恢复评估，再算分配。
    # 恢复的实例在同一轮就重新进入候选池（dry-run 同样体现，不等下一轮）。
    recovered = _evaluate_threshold_recovery(settings, accounts, now)
    recovered_ids = {r["account"]["id"] for r in recovered}
    # 上一轮已踢出的账户（flags.evicted:<id>），去掉本轮已恢复的：
    # 供引擎做恢复滞后判断。一次查询取齐，不再每账户查一次 flag。
    prev_evicted = db.list_evicted_account_ids() - recovered_ids
    plan = engine.plan_reconcile(_to_views(accounts, settings),
                                 _to_domain_views(domains), cfg_obj, now,
                                 set(filter(None, [db.get_flag("night_done")])),
                                 set(filter(None, [db.get_flag("fixed_done")])),
                                 _flag_dt("last_time_switch_at"),
                                 groups=groups,
                                 night_done_by_group=night_by_group,
                                 fixed_done_by_group=fixed_by_group,
                                 last_switch_by_group=anchor_by_group,
                                 previously_evicted_ids=prev_evicted)
    notifier = Notifier(settings)

    def _notify(event, title, body):
        notifier.send(event, title, body)
        db.add_event(event, title, body)

    if dry_run:
        result = {"dry_run": True, "summary": plan.summary,
                  "threshold_recovery": [
                      {"account_id": r["account"]["id"],
                       "name": r["account"]["name"],
                       "rate": round(r["rate"], 2),
                       "threshold": r["threshold"], "line": r["line"],
                       "fallback": r["fallback"]} for r in recovered],
                  "assignments": {str(k): v for k, v in plan.assignments.items()},
                  "actions": [{"kind": a.kind, "domain_id": a.domain_id,
                               "account_id": a.account_id, "reason": a.reason,
                               "detail": a.detail} for a in plan.actions],
                  "notifications": [{"event": e, "title": t, "body": b}
                                    for e, t, b in plan.notifications],
                  "evicted": plan.evicted_account_ids,
                  "fallback_domains": plan.fallback_domain_ids,
                  "paused_domains": plan.paused_domain_ids,
                  "groups": [{"id": g.id, "name": g.name,
                              "switch_mode": g.switch_mode} for g in groups]}
        job_id = db.create_job("dry_run")
        db.finish_job(job_id, "done", result)
        return result

    executed = engine.execute_plan(plan, _to_views(accounts, settings),
                                   _to_domain_views(domains),
                                   _CloudOps(settings), _DnsOps(settings),
                                   notify_fn=_notify, dry_run=False)
    # 落库：域名当前指向 / 备用标记 / 已停止分组的域名（指向 0.0.0.0）
    for d in domains:
        target = plan.assignments.get(d["id"])
        if d["id"] in plan.paused_domain_ids:
            db.update_domain(d["id"], {"fallback_active": 0, "current_account_id": None,
                                       "current_ip": "0.0.0.0"})
        elif d["id"] in plan.fallback_domain_ids:
            db.update_domain(d["id"], {"fallback_active": 1, "current_account_id": None})
        elif target is not None:
            acc = next((a for a in accounts if a["id"] == target), None)
            db.update_domain(d["id"], {
                "fallback_active": 0, "current_account_id": target,
                "current_ip": (acc or {}).get("public_ip") or d.get("current_ip") or ""})
    if plan.group_ids:
        # 分组路径：旗标按组命名空间持久化
        for gid, date in plan.night_done_by_group.items():
            db.set_flag(f"night_done:{gid}", date)
        for gid, date in plan.fixed_done_by_group.items():
            db.set_flag(f"fixed_done:{gid}", date)
        for gid, at in plan.time_switch_by_group.items():
            db.set_flag(f"last_time_switch:{gid}", at.isoformat())
        for gid, at in plan.interval_anchor_by_group.items():
            if gid not in plan.time_switch_by_group:
                # 首次间隔检查未到点：把本轮时刻存为锚点，下轮从这里开始计时
                db.set_flag(f"last_time_switch:{gid}", at.isoformat())
    else:
        # legacy 无分组路径：沿用旧的无后缀键
        if plan.night_done_date:
            db.set_flag("night_done", plan.night_done_date)
        if plan.fixed_done_date:
            db.set_flag("fixed_done", plan.fixed_done_date)
        if plan.time_switch_at is not None:
            db.set_flag("last_time_switch_at", plan.time_switch_at.isoformat())
        elif plan.interval_anchor_at is not None:
            db.set_flag("last_time_switch_at", plan.interval_anchor_at.isoformat())
    if not quiet:
        db.add_event("schedule", "调度完成", plan.summary)
    daily_schedule = _apply_daily_schedule(settings, now)
    # 恢复持久化：清 evicted 旗标、记恢复事件、按需拉起（dry-run 不走到这里）
    started_by_plan = {a.account_id for a in plan.actions
                       if a.kind == "start" and a.account_id}
    threshold_recovery = _persist_threshold_recovery(
        settings, accounts, plan, recovered, started_by_plan, now)
    result = {"dry_run": False, "summary": plan.summary,
              "executed": executed["executed"],
              "daily_schedule": daily_schedule,
              "threshold_recovery": threshold_recovery,
              "assignments": {str(k): v for k, v in plan.assignments.items()},
              "evicted": plan.evicted_account_ids,
              "fallback_domains": plan.fallback_domain_ids,
              "paused_domains": plan.paused_domain_ids,
              "ran_at": now.isoformat()}
    if not quiet:
        job_id = db.create_job("schedule")
        db.finish_job(job_id, "done", result)
        with _lock:
            _last_run.clear()
            _last_run.update(result)
    return result


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        h, m = (value or "00:00").split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (ValueError, AttributeError):
        pass
    return 0, 0


def _refresh_minutes(settings: dict) -> int:
    """API 刷新间隔（分钟）：读新字段 api_refresh_minutes，旧库回退旧字段
    check_interval_minutes；非法值回退 60 分钟，绝不让调度线程崩溃。"""
    for key in ("api_refresh_minutes", "check_interval_minutes"):
        try:
            v = int(float(settings.get(key, 60)))
            if 1 <= v <= 1440:
                return v
        except (TypeError, ValueError):
            continue
    return 60


def _apply_daily_schedule(settings: dict, now: dt.datetime,
                          cloud_factory=None) -> list[str]:
    """每日定时开关机（按账户独立判断，不影响分组切换模式）。

    开机时刻后、关机时刻后各有一次补偿窗口（max(30 分钟, API 刷新间隔)，
    覆盖进程重启/延迟导致的错过），窗口内执行一次并用
    flags.daily_on_done:<id>:<YYYY-MM-DD> / daily_off_done:<id>:<YYYY-MM-DD>
    记下，当天不再重复。阈值踢出优先于定时开机：已被踢出的实例到点也不拉起
    （引擎的每日窗口门控同时保证保活不拉起、域名不分给窗口外实例）。
    定时关机用普通停机（KeepCharging，不走节省停机）。
    返回执行/跳过的动作描述列表。
    """
    accounts = db.list_accounts()
    if not accounts:
        return []
    cfg_obj = engine.Config.from_settings(settings)
    views = {v.id: v for v in _to_views(accounts, settings)}
    refresh_min = _refresh_minutes(settings)
    window = dt.timedelta(minutes=max(30, refresh_min))
    done: list[str] = []
    for a in accounts:
        if not a.get("daily_schedule_enabled"):
            continue
        v = views[a["id"]]
        on_h, on_m = _parse_hhmm(a.get("daily_on_time") or "08:00")
        off_h, off_m = _parse_hhmm(a.get("daily_off_time") or "20:00")
        # 最近一次开机时刻（跨午夜窗口时属于昨天，如 20:00 开机、次日 08:00
        # 关机，则次日 08:00 的关机属于昨天 20:00 那一次开机）。
        on_dt = now.replace(hour=on_h, minute=on_m, second=0, microsecond=0)
        if on_dt > now:
            on_dt -= dt.timedelta(days=1)
        off_dt = on_dt.replace(hour=off_h, minute=off_m)
        if off_dt <= on_dt:
            off_dt += dt.timedelta(days=1)  # 跨午夜：关机在开机次日
        secret = decrypt_secret(a.get("access_key_secret_enc") or "")
        # --- 开机 ---
        on_key = f"daily_on_done:{a['id']}:{on_dt.strftime('%Y-%m-%d')}"
        if on_dt <= now < on_dt + window and not db.get_flag(on_key):
            db.set_flag(on_key, now.isoformat())
            rate = v.usage_rate
            evicted = (rate is not None
                       and rate >= engine._eff_protection(v, cfg_obj))
            if evicted:
                msg = (f"{a['name']} 定时开机已跳过（已跳出：使用率 {rate:.1f}% "
                       f"≥ 阈值 {engine._eff_protection(v, cfg_obj):.0f}%）")
                done.append(msg)
                db.add_event("schedule", "定时开机已跳过", msg)
            elif (v.status or "") == "Stopped":
                try:
                    client = (cloud_factory(a, secret) if cloud_factory
                              else AliyunClient(a, secret))
                    client.start_instance()
                    msg = f"{a['name']} 定时开机已执行"
                    done.append(msg)
                    db.add_event("schedule", "定时开机", msg)
                except Exception as exc:
                    msg = f"{a['name']} 定时开机失败：{brief_error(exc)}"
                    done.append(msg)
                    db.add_event("error", "定时开机失败", msg)
        # --- 关机 ---
        off_key = f"daily_off_done:{a['id']}:{off_dt.strftime('%Y-%m-%d')}"
        if off_dt <= now < off_dt + window and not db.get_flag(off_key):
            db.set_flag(off_key, now.isoformat())
            if (v.status or "") == "Running":
                try:
                    client = (cloud_factory(a, secret) if cloud_factory
                              else AliyunClient(a, secret))
                    client.stop_instance(stop_charging=False)
                    msg = f"{a['name']} 定时关机已执行"
                    done.append(msg)
                    db.add_event("schedule", "定时关机", msg)
                except Exception as exc:
                    msg = f"{a['name']} 定时关机失败：{brief_error(exc)}"
                    done.append(msg)
                    db.add_event("error", "定时关机失败", msg)
    return done


def _parse_flag_dt(raw: str, now: dt.datetime) -> dt.datetime | None:
    if not raw:
        return None
    try:
        d = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=now.tzinfo)
    return d


def _evaluate_threshold_recovery(settings: dict, accounts: list[dict],
                                 now: dt.datetime) -> list[dict]:
    """流量更新后自动重新加入 DDNS：恢复评估（纯计算，不改 flags、不记事件）。

    调度器经 flags.evicted:<id> 记住上一轮已踢出的账户（旗标值=首次踢出时刻）；
    每次 API 刷新（CDT/BSS 流量拉取后）与每轮调度开始前都调用本函数，
    对全部被踢出实例重新评估：
    - 恢复线 = 生效保护阈值 - 2%（engine.RECOVERY_HYSTERESIS_PCT）：
      使用率回落到恢复线以下即恢复；
    - 24 小时兜底：被踢出超过 24 小时且最新流量显示用量低于阈值
      （哪怕没到恢复线，如 94%），也自动恢复，避免恢复线把实例永久卡在外面；
    - 流量未知（本次接口查不到）时不恢复也不误判，保持原状态。
    恢复条件覆盖的流量更新来源：CDT 用量下降、月度额度重置（新月份已用清零）、
    账户级阈值调高、额度调大（都体现在当前使用率/阈值上）。
    返回恢复项列表：{"account", "view", "rate", "threshold", "line",
    "fallback": bool}。调用方在分配计算前把恢复的账户从 previously_evicted_ids
    里去掉，使其在同一轮就重新进入候选池；dry-run 同样走本函数，只是不持久化。
    """
    cfg_obj = engine.Config.from_settings(settings)
    views = {v.id: v for v in _to_views(accounts, settings)}
    recovered: list[dict] = []
    for a in accounts:
        aid = a["id"]
        evicted_since_raw = db.get_flag(f"evicted:{aid}")
        if not evicted_since_raw:
            continue  # 上一轮也没踢出：不是恢复
        v = views[aid]
        rate = v.usage_rate
        if rate is None:
            continue  # 流量未知：不恢复，保持原状态
        thr = engine._eff_protection(v, cfg_obj)
        line = thr - engine.RECOVERY_HYSTERESIS_PCT
        fallback = False
        if rate < line:
            pass  # 正常恢复
        elif rate < thr:
            since = _parse_flag_dt(evicted_since_raw, now)
            if since is not None and (now - since) >= dt.timedelta(hours=24):
                fallback = True  # 超时兜底恢复
            else:
                continue  # 在恢复线与阈值之间且未满 24 小时：仍视为踢出
        else:
            continue  # 仍在阈值之上：未恢复
        recovered.append({"account": a, "view": v, "rate": rate,
                          "threshold": thr, "line": line, "fallback": fallback})
    return recovered


def _persist_threshold_recovery(settings: dict, accounts: list[dict],
                                plan: engine.Plan,
                                recovered: list[dict],
                                started_by_plan: set[int],
                                now: dt.datetime,
                                cloud_factory=None) -> list[str]:
    """恢复持久化（真实调度轮次、执行 Plan 之后调用；dry-run 不调用）。

    对每一条已评估出的恢复：清除 evicted 旗标、记恢复事件、按
    threshold_recovery_action 执行拉起（默认拉起；三选一选了踢出DDNS 的
    实例本来就在跑，不调开机）。月度重置导致全部实例同时恢复时逐个处理、
    逐个记事件。同一实例恢复后 1 小时内不再重复记恢复事件、不重复拉起
    （flags.recovered_at:<id>）。本轮仍踢出的账户补记 evicted 旗标。
    started_by_plan：Plan 里已有 start 动作的账户（保活已拉起），不再重复开机。
    返回动作描述列表。
    """
    done: list[str] = []
    evicted_now = set(plan.evicted_account_ids)
    for a in accounts:
        aid = a["id"]
        key = f"evicted:{aid}"
        if aid in evicted_now:
            if not db.get_flag(key):
                db.set_flag(key, now.isoformat())
            continue
    for item in recovered:
        a = item["account"]
        aid = a["id"]
        v = item["view"]
        rate, thr, line = item["rate"], item["threshold"], item["line"]
        fallback = item["fallback"]
        db.delete_flag(f"evicted:{aid}")
        last_raw = db.get_flag(f"recovered_at:{aid}")
        last = _parse_flag_dt(last_raw, now)
        within_hour = bool(last is not None
                           and (now - last) < dt.timedelta(hours=1))
        db.set_flag(f"recovered_at:{aid}", now.isoformat())
        if fallback:
            title = "超时兜底恢复，已重新加入 DDNS"
            detail = (f"{a['name']} 被踢出已超 24 小时，使用率 {rate:.1f}% "
                      f"低于阈值 {thr:.0f}%（未到恢复线 {line:.0f}%），兜底恢复")
        else:
            title = "流量更新，已自动重新加入 DDNS"
            detail = f"{a['name']} 使用率 {rate:.1f}% < 恢复线 {line:.0f}%"
        action = (v.recovery_action
                  or settings.get("threshold_recovery_action") or "start").strip().lower()
        if action not in ("start", "keep_stopped"):
            action = "start"
        if action == "keep_stopped":
            msg = f"阈值恢复，已入池，保持停机：{a['name']}"
            done.append(msg)
            if not within_hour:
                db.add_event(
                    "recovery", "阈值恢复，已入池，保持停机",
                    f"{a['name']}（使用率 {rate:.1f}% < 恢复线 {line:.0f}%）"
                    + ("；超时兜底恢复" if fallback else ""))
            continue
        # start（默认）
        if (v.status or "") == "Stopped" and not within_hour \
                and aid not in started_by_plan:
            if not engine.is_in_daily_window(v, now):
                msg = (f"阈值恢复，已入池，定时关机窗口内暂不拉起：{a['name']}"
                       f"（使用率 {rate:.1f}% < 恢复线 {line:.0f}%）")
                done.append(msg)
                db.add_event("recovery", "阈值恢复，定时关机窗口内暂不拉起",
                             f"{a['name']} 使用率 {rate:.1f}% < 恢复线 {line:.0f}%；"
                             "每日定时关机优先，待开机窗口再拉起")
            else:
                try:
                    secret = decrypt_secret(a.get("access_key_secret_enc") or "")
                    client = (cloud_factory(a, secret) if cloud_factory
                              else AliyunClient(a, secret))
                    client.start_instance()
                    msg = (f"阈值恢复，已拉起：{a['name']}"
                           f"（使用率 {rate:.1f}% < 恢复线 {line:.0f}%）")
                    done.append(msg)
                    # 事件标题=重新加入 DDNS（保证性要求的可观测性），
                    # 详情=拉起文案（上一条“恢复默认拉起”的要求）：两者都留档。
                    db.add_event("recovery", title, msg)
                except Exception as exc:
                    msg = f"{a['name']} 阈值恢复拉起失败：{brief_error(exc)}"
                    done.append(msg)
                    db.add_event("error", "阈值恢复拉起失败", msg)
        else:
            # Running（停机模式=踢出DDNS 时最常见，本来就在跑）或 1 小时内已
            # 拉起过、或保活已拉起：不重复调用开机。
            msg = (f"阈值恢复，已入池：{a['name']}"
                   f"（使用率 {rate:.1f}% < 恢复线 {line:.0f}%）")
            done.append(msg)
            if not within_hour:
                db.add_event("recovery", title, detail)
    return done


def _maybe_send_daily_report(settings: dict, now: dt.datetime) -> bool:
    """每天在 daily_report_time（默认 00:00，可配）推送一次每日财报。

    触发规则（用 flags 持久化，重启不丢）：
    - 上次检查时刻记在 flags.daily_report_last_check，每次检查都更新；
    - 只有当本次检查跨过了当日推送时刻（last_check < 当日时刻 <= now），
      且当天未推过（flags.daily_report_done:<YYYY-MM-DD> 未设置）时才推；
    - 改时间后若新时刻已过（last_check 已在新时刻之后），当天不补推，等明天；
    - 全新数据库第一次检查不补推（last_check 为空时只记下次检查起点）；
    - 调度暂停或财报开关关闭时不推，但仍更新 last_check，避免恢复/重开后立刻补推。
    返回 True 表示当日财报已触发（渠道可能为空，仍记当天已推，避免每小时重试）。
    """
    hh, mm = _parse_hhmm(settings.get("daily_report_time") or "00:00")
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    today = now.strftime("%Y-%m-%d")
    last_raw = db.get_flag("daily_report_last_check")
    db.set_flag("daily_report_last_check", now.isoformat())
    # 注意：这里不因全局 daily_report_enabled=0 而直接返回——实例级
    # daily_report=on 的账户即使全局关了也要推；过滤后无账户时再返回。
    if settings.get("scheduler_paused") == "1":
        return False
    if db.get_flag(f"daily_report_done:{today}"):
        return False
    if not last_raw:
        return False  # 全新数据库第一次检查：不补推，等下一次到点
    try:
        last = dt.datetime.fromisoformat(last_raw)
    except ValueError:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=now.tzinfo)
    if not (last < due <= now):
        return False
    accounts = db.list_accounts()
    if not accounts:
        return False
    from .api import _public_account, _status_context  # 延迟导入，避免循环导入
    from .notify import filter_daily_report_accounts
    serving_map, queue_ranks = _status_context(accounts)
    pub = [_public_account(a, serving_map, queue_ranks) for a in accounts]
    pub = filter_daily_report_accounts(pub, settings)
    if not pub:
        return False  # 全局关且无单独开启的账户：不推，也不记当天已推
    notifier = Notifier(settings)
    sent = notifier.send_daily_report(pub, today, skip_global_switch=True)
    db.set_flag(f"daily_report_done:{today}", now.isoformat())
    if sent:
        db.add_event("daily_report", "每日财报已推送",
                     f"{today} 共 {len(pub)} 个账户，渠道：{','.join(sent)}")
    else:
        db.add_event("daily_report", "每日财报未推送",
                     f"{today} 已到推送时间，但未启用任何通知渠道")
    return True


def _maybe_prune_daily() -> None:
    """每天清理一次过期事件/流量采样（见 db.prune_old_data）。
    用 flags.prune_done 记日期，重启当天不重复；清理失败只记日志，
    不影响调度轮次，失败也记当天已处理、明天再试。"""
    today = _now().strftime("%Y-%m-%d")
    if db.get_flag("prune_done") == today:
        return
    try:
        pruned = db.prune_old_data()
        log.info("数据清理：事件 %s 条，流量采样 %s 条",
                 pruned["events_deleted"], pruned["traffic_samples_deleted"])
    except Exception as exc:  # noqa: BLE001
        log.warning("数据清理失败: %s", exc)
    db.set_flag("prune_done", today)


def last_run() -> dict:
    with _lock:
        return dict(_last_run)


DDNS_CHECK_INTERVAL_SECONDS = 60  # DDNS 检查固定每分钟一次，无后端设置


def ddns_check() -> dict:
    """DDNS 检查：每分钟一次（硬编码，无后端设置）。用库里已有的数据
    重算分配并同步 DNS，不调阿里云 API；不写调度事件、不建 job，
    避免每分钟一条日志刷屏。"""
    return run_once(refresh=False, quiet=True)


def _loop() -> None:
    last_full = 0.0  # 上次完整轮次（带 API 刷新）的时间戳
    while not _stop.is_set():
        settings = db.get_settings()
        try:
            # 每日财报：每轮检查一次到点与否（暂停时也检查，只更新检查时刻不推送）
            _maybe_send_daily_report(settings, _now())
        except Exception as exc:  # noqa: BLE001
            log.exception("每日财报检查失败: %s", exc)
        if settings.get("scheduler_paused") == "1":
            _wake.wait(timeout=60)
            _wake.clear()
            continue
        try:
            _maybe_prune_daily()
        except Exception as exc:  # noqa: BLE001
            log.exception("数据清理检查失败: %s", exc)
        # DDNS 检查固定每分钟一次（无后端设置）；完整轮次（带 API 刷新）
        # 按 API 刷新时间跑。第一次立即跑完整轮次。
        now_ts = time.time()
        full_interval = _refresh_minutes(settings) * 60
        try:
            if now_ts - last_full >= full_interval:
                run_once()
                last_full = time.time()
            else:
                ddns_check()
        except Exception as exc:  # noqa: BLE001
            log.exception("调度轮次失败: %s", exc)
            db.add_event("error", "调度异常", str(exc)[:300])
        _wake.wait(timeout=DDNS_CHECK_INTERVAL_SECONDS)
        _wake.clear()


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="cdt-scheduler", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    _wake.set()


def wake() -> None:
    _wake.set()
