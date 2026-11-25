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
_thread_lock = threading.Lock()


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


def _to_views(accounts: list[dict], settings: dict,
              groups: list[dict] | None = None) -> list[engine.AccountView]:
    # 多对多成员关系一次取齐（group_ids 为准；未填时引擎回退主分组 group_id）
    try:
        _membership: dict[int, list[int]] = {}
        for row in db.list_account_groups():
            _membership.setdefault(int(row["account_id"]), []).append(int(row["group_id"]))
    except Exception:
        _membership = {}
    # 分组级阈值策略：实例的阈值动作/恢复取其所属分组的值（空=跟随全局）；
    # groups 未传时现场查（与成员关系同理，查不到时按跟随全局处理）。
    if groups is None:
        try:
            groups = db.list_groups()
        except Exception:
            groups = []
    _gmap = {int(g["id"]): g for g in groups}
    views = []
    for a in accounts:
        quota = a.get("quota_gb") if a.get("quota_gb") is not None else float(settings.get("quota_gb", 200))
        # 所属分组：主分组 group_id 优先，其次多对多成员分组的首个，再次默认分组 1
        _gids = _membership.get(int(a["id"]), [])
        _gid = a.get("group_id") or (_gids[0] if _gids else 1)
        _g = _gmap.get(int(_gid)) if _gid is not None else None
        views.append(engine.AccountView(
            id=a["id"], name=a["name"], quota_gb=float(quota),
            protection_threshold_pct=a.get("protection_threshold_pct"),
            switch_diff_pct=a.get("switch_diff_pct"),
            switch_diff_mode=a.get("switch_diff_mode"),
            switch_diff_gb=a.get("switch_diff_gb"),
            exclusion_action=(_g.get("exclusion_action") if _g else None),
            priority=a.get("priority") or 0,
            used_gb=a.get("traffic_used_gb"),
            status=a.get("instance_status") or "",
            public_ip=a.get("public_ip") or "",
            group_id=a.get("group_id"),
            group_ids=_membership.get(int(a["id"]), []),
            daily_schedule_enabled=bool(a.get("daily_schedule_enabled")),
            daily_on_time=a.get("daily_on_time") or "08:00",
            daily_off_time=a.get("daily_off_time") or "20:00",
            recovery_action=(_g.get("recovery_action") if _g else None),
            ddns_enabled=bool(a.get("ddns_enabled", 1)),
            stop_reason=a.get("stop_reason") or ""))
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
        dynamic_interval_minutes=g.get("dynamic_interval_minutes"),
        exclusion_action=g.get("exclusion_action"),
        recovery_action=g.get("recovery_action"),
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
            # B-08：账单周期按用户设置的调度时区算，不再硬编码 Asia/Shanghai。
            tz_name = (settings.get("timezone") or "Asia/Shanghai").strip()
            bill = client.get_billing(timezone=tz_name)
            if bill:
                updates["billing_month_cost"] = bill["cost"]
                updates["billing_currency"] = bill["currency"]
                if bill.get("balance") is not None:
                    updates["billing_balance"] = bill["balance"]
                    updates["billing_balance_currency"] = (
                        bill.get("balance_currency") or bill["currency"])
                updates["billing_updated_at"] = int(time.time())
        db.update_account(a["id"], updates)
        if inst["status"] == "Running" and (a.get("stop_reason") or ""):
            # 刷新发现实例已 Running（如用户在阿里云控制台手动拉起）：
            # 清除停机原因标记，保活恢复默认拉起。
            db.clear_stop_reason(a["id"])
        elif (a.get("stop_reason") or "") in ("manual", "threshold"):
            # 排除只到下一次 API 流量刷新为止：本次刷新显示用量在阈值内就
            # 清标记、重新加入默认拉起（本轮调度随即拉起）；用量仍在阈值外
            # 则继续排除。判断见 _stop_exclusion_lifted。
            _reason = _stop_exclusion_lifted(a, used, settings)
            if _reason:
                db.clear_stop_reason(a["id"])
                msg = (f"{a['name']} {_reason}，重新加入保活拉起")
                db.add_event("keepalive_rejoin", "流量更新，重新加入保活",
                             msg, account_id=a["id"])
                log.info("保活重新加入 %s：%s", a["name"], _reason)
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


def _eff_refresh_minutes(a: dict, settings: dict) -> int:
    """单账户生效的 API 刷新间隔（分钟）：实例级刷新时间已取消，
    所有账户强制跟随全局 api_refresh_minutes。"""
    return _refresh_minutes(settings)


def _is_refresh_due(a: dict, settings: dict, now_ts: int) -> bool:
    """该账户是否到了刷新时间：上次刷新（last_seen_at）距今 >= 生效间隔。"""
    last = a.get("last_seen_at") or 0
    return now_ts - last >= _eff_refresh_minutes(a, settings) * 60


def refresh_cloud_status(settings: dict) -> list[dict]:
    """刷新到期的账户快照并写回 DB。返回更新后的账户行。

    并行刷新：每账户独立调阿里云接口（DescribeInstances + CDT 流量 +
    BSS 费用，最多 3 次往返），串行时 N 个账户要 N×3 次往返，一轮
    耗时随账户数线性增长；线程池并行后一轮耗时接近最慢的 1 个账户。
    _refresh_one 内部已捕获全部业务异常；DB 写经 db._lock 串行，
    单连接跨线程共享安全；_refresh_one_safe 再兜一层防意外抛错
    导致整轮失败。
    单账户 API 刷新时间：实例级刷新时间已取消，所有账户跟随全局；
    没到时间的账户跳过（用库里已有数据），避免每轮都调全部账户的 API。
    """
    accounts = db.list_accounts()
    now_ts = int(time.time())
    due = [a for a in accounts if _is_refresh_due(a, settings, now_ts)]
    if len(due) > 1:
        workers = min(REFRESH_MAX_WORKERS, len(due))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(lambda a: _refresh_one_safe(a, settings), due))
    else:
        for a in due:
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


def _stop_exclusion_lifted(a: dict, used_gb: float | None,
                           settings: dict) -> str:
    """停机排除是否因本次 API 流量刷新而解除：返回原因文案，否返回 ""。

    排除的有效期只到下一次 API 流量刷新为止：刷新显示用量在阈值内就清除
    标记、重新加入默认拉起（保活在同轮调度里拉起）；用量仍在阈值外则继续
    排除。阈值内/外的判断沿用现有阈值恢复的恢复线（保护阈值-2%，
    engine.RECOVERY_HYSTERESIS_PCT），不另起一套线：用量回落到恢复线以下
    才算在阈值内，介于恢复线与阈值之间仍视为阈值外（防抖动，与
    _evaluate_threshold_recovery 一致）。流量未知（used_gb 为 None）时
    不解除，保持排除。schedule（定时关机）标记不走这里——窗口外本来也不
    拉，由每日窗口控制。
    注意这个含义下的后果：用量正常的手动停机，通常在下一次 API 刷新后就
    会被保活重新拉起（排除只保一轮刷新间隔）。
    """
    reason = (a.get("stop_reason") or "")
    if reason not in ("manual", "threshold"):
        return ""
    if used_gb is None:
        return ""
    quota = a.get("quota_gb") or settings.get("quota_gb") or 200
    if not quota:
        return ""
    thr = a.get("protection_threshold_pct")
    thr = float(thr) if thr is not None else float(
        settings.get("protection_threshold_pct") or 95)
    line = thr - engine.RECOVERY_HYSTERESIS_PCT
    rate = float(used_gb) / float(quota) * 100.0
    if rate < line:
        return f"用量 {rate:.2f}% 在阈值内（恢复线 {line:.0f}%）"
    return ""


def _keepalive_rejoined(before: list[dict], after: list[dict],
                        settings: dict) -> dict[int, str]:
    """本轮 API 刷新里重新加入保活的实例：刷新前停机原因标记为 manual /
    threshold、实例状态为 Stopped，刷新后标记已被 _refresh_one 清除
    （用量回到恢复线以下，见 _stop_exclusion_lifted）。返回
    {account_id: 解除原因文案}，供 run_once 把本轮随即拉起的 start 动作
    原因改成 keepalive_rejoin——否则拉起推送只写“保活拉起”，丢了
    “流量更新后重新加入”这个上下文。刷新前已 Running 的不算（那是
    阿里云控制台手动拉起后刷新发现 Running，不走这里）；未到刷新时间、
    标记没被清除的也不算。
    """
    after_by_id = {a["id"]: a for a in after}
    rejoined: dict[int, str] = {}
    for row in before:
        if (row.get("stop_reason") or "") not in ("manual", "threshold"):
            continue
        if (row.get("instance_status") or "") != "Stopped":
            continue
        new = after_by_id.get(row["id"])
        if new is None or (new.get("stop_reason") or ""):
            continue
        reason = _stop_exclusion_lifted(row, new.get("traffic_used_gb"), settings)
        if reason:
            rejoined[row["id"]] = reason
    return rejoined


def _log_keepalive_skips(accounts: list[dict],
                         skipped: list[tuple[int, str]]) -> None:
    """保活跳过被排除实例的可观测性：每轮都写日志；事件只在同一停机标记
    下记一次（flags.keepalive_skip_logged:<id>:<原因>:<stopped_at>），
    避免每分钟的 DDNS 检查把事件表刷满。标记清除/重记后 stopped_at 变化，
    下一次停机可再记。"""
    if not skipped:
        return
    by_id = {a["id"]: a for a in accounts}
    for aid, reason in skipped:
        row = by_id.get(aid) or {}
        name = row.get("name") or str(aid)
        label = db.STOP_REASON_LABELS.get(reason, reason)
        log.info("保活跳过 %s：%s，保活不拉起", name, label)
        key = (f"keepalive_skip_logged:{aid}:{reason}:"
               f"{row.get('stopped_at') or 0}")
        if db.get_flag(key):
            continue
        db.set_flag(key, "1")
        db.add_event("keepalive_skip", "保活跳过",
                     f"{name} {label}，保活不拉起", account_id=aid)


def _check_threshold_alarms(settings: dict, accounts: list[dict],
                            notifier: Notifier) -> None:
    """阈值报警（80% 警告线，固定值，不在设置里加新阈值项）：
    用量首次达到 80% 且未到保护阈值时推送一次（事件 threshold_alarm，
    可开关，默认开）；恢复到 80% 以下后清旗标，下次再跨越可再报。
    已到保护阈值的不再发报警（踢出时走原有的 traffic_protection），
    但要把旗标置上，避免从阈值回落到 80%+ 时立刻又报一次。
    无论开关开还是关，都记一条 threshold_alarm 日志事件（与 _notify
    对其它事件的做法一致：开关只决定是否推送，不决定是否记日志）。"""
    for a in accounts:
        used = a.get("traffic_used_gb")
        quota = a.get("quota_gb") or settings.get("quota_gb") or 200
        if used is None or not quota:
            continue
        rate = float(used) / float(quota) * 100.0
        thr = a.get("protection_threshold_pct")
        thr = float(thr) if thr is not None else float(settings.get("protection_threshold_pct") or 95)
        key = f"threshold_alarm:{a['id']}"
        if rate < THRESHOLD_ALARM_PCT:
            if db.get_flag(key):
                db.delete_flag(key)
            continue
        if rate >= thr:
            # 已到保护阈值：不发报警，走 traffic_protection；置旗标防回落重报
            db.set_flag(key, "1")
            continue
        if db.get_flag(key):
            continue  # 已报过，未回落到 80% 以下：不再重复报
        db.set_flag(key, "1")
        title = "阈值报警"
        body = (f"{a['name']} 使用率 {rate:.2f}% 已达到 80%，"
                f"阈值 {thr:.0f}%（已用 {float(used):.2f} / {float(quota):.0f} GiB），"
                "请注意流量余量")
        sent = notifier.send("threshold_alarm", title, body)
        if not sent and getattr(notifier, "last_errors", None):
            db.add_event("error", "通知发送失败",
                         f"{title}："
                         + "; ".join(f"{k}：{v}" for k, v in
                                     notifier.last_errors.items()))
        db.add_event("threshold_alarm", title, body, account_id=a["id"])


THRESHOLD_ALARM_PCT = 80.0


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
        # P1-02：先读库里已存的 zone_id/record_id，按 ID 直接取记录，
        # 省掉每次的 Zone/Record 发现调用；取不到时 sync() 内部回退发现流程。
        cached = None
        try:
            row = db.get_dns_record(domain.id)
            if row and row.get("zone_id") and row.get("record_id"):
                cached = {"zone_id": row["zone_id"],
                          "record_id": row["record_id"]}
        except Exception:
            cached = None
        res = self.cf.sync(domain.name, zone, ip, domain.record_type,
                           domain.ttl, domain.proxied, cached=cached)
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
    # 刷新前快照：用来判断本轮哪些停机标记是被 API 流量刷新解除的
    # （_keepalive_rejoined），拉起推送里要写“流量更新后重新加入”。
    _pre_refresh = db.list_accounts() if (refresh and not dry_run) else []
    if refresh and not dry_run:
        accounts = refresh_cloud_status(settings)
    else:
        accounts = db.list_accounts()
    # 本轮刷新里重新加入保活的实例（标记被清除且用量在阈值内）。
    _rejoined = _keepalive_rejoined(_pre_refresh, accounts, settings)
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
    # 流量更新后重新加入的实例：本轮随即拉起的 start 动作原因改成
    # keepalive_rejoin，推送里写清“流量更新后重新加入”，而不是只写
    # “保活拉起”。只改原因/detail，不改动作本身；dry_run 也同样体现。
    if _rejoined:
        _by_id = {a["id"]: a for a in accounts}
        for _act in plan.actions:
            if _act.kind == "start" and _act.account_id in _rejoined:
                _name = (_by_id.get(_act.account_id) or {}).get("name") or str(_act.account_id)
                _act.reason = "keepalive_rejoin"
                _act.detail = (f"{_name} 流量更新后重新加入保活拉起"
                               f"（{_rejoined[_act.account_id]}）")
    notifier = Notifier(settings)

    def _notify(event, title, body):
        sent = notifier.send(event, title, body)
        # E-07：通知失败不要只留在内存 last_errors——记一条 error 事件，
        # 让失败在事件里看得到。开关关闭时 sent 与 last_errors 都为空，不记。
        if not sent and getattr(notifier, "last_errors", None):
            db.add_event("error", "通知发送失败",
                         f"{title}："
                         + "; ".join(f"{k}：{v}" for k, v in
                                     notifier.last_errors.items()))
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
                  "keepalive_skipped": [
                      {"account_id": aid, "reason": r}
                      for aid, r in plan.keepalive_skipped],
                  "groups": [{"id": g.id, "name": g.name,
                              "switch_mode": g.switch_mode} for g in groups]}
        job_id = db.create_job("dry_run")
        db.finish_job(job_id, "done", result)
        return result

    executed = engine.execute_plan(plan, _to_views(accounts, settings),
                                   _to_domain_views(domains),
                                   _CloudOps(settings), _DnsOps(settings),
                                   notify_fn=_notify, dry_run=False)
    # 停机原因标记流转（保活用）：
    # - Plan 里拉起成功的实例 → 清标记（保活恢复默认拉起）；
    # - Plan 里执行的阈值停机（exclusion）→ 记 threshold，保活不再拉起；
    # - 保活跳过的被排除实例 → 记日志事件（同一标记只记一次）。
    started_by_plan = {a.account_id for a in plan.actions
                       if a.kind == "start" and a.account_id}
    for _aid in started_by_plan:
        db.clear_stop_reason(_aid)
    for _act in plan.actions:
        if _act.kind == "stop" and _act.reason == "exclusion" and _act.account_id:
            # 记 threshold，保活不再拉起；下一次 API 刷新显示用量回到
            # 恢复线以下时解除（_stop_exclusion_lifted，沿用现有恢复线），
            # 与阈值恢复逻辑一致。
            db.set_stop_reason(_act.account_id, "threshold")
    _log_keepalive_skips(accounts, plan.keepalive_skipped)
    # 阈值报警：刷新后的用量首次跨 80% 时推一次（见 _check_threshold_alarms）
    _check_threshold_alarms(settings, accounts, notifier)
    # 落库：域名当前指向 / 备用标记 / 已停止分组的域名（指向 0.0.0.0）
    for d in domains:
        target = plan.assignments.get(d["id"])
        if d["id"] in plan.paused_domain_ids:
            db.update_domain(d["id"], {"fallback_active": 0, "current_account_id": None,
                                       "current_ip": "0.0.0.0"})
        elif d["id"] in plan.fallback_domain_ids:
            # B-12：备用地址不是 IP 时 set_record 已返回失败并记事件，
            # 但这里原来仍把 fallback_active 写成 1，界面会误显示“备用生效”。
            # 备用地址无效时不写生效标记。
            from .ddns import is_valid_ip
            fallback_ip = (settings.get("fallback_address") or "").strip()
            db.update_domain(d["id"], {"fallback_active": 1 if is_valid_ip(fallback_ip) else 0,
                                       "current_account_id": None})
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
        # 日志：只写“调度完成，动作 N 条”查不到是哪台、做了什么、为什么。
        # 把本轮实际执行的动作（启动/停机/DNS，含实例名与域名）写进同一条
        # 事件的 detail，让人不用点开别处就能看懂；无动作时仍只写汇总。
        _done = executed.get("executed") or []
        _detail = plan.summary + (f"；动作：{'、'.join(_done)}" if _done else "")
        db.add_event("schedule", "调度完成", _detail)
    daily_schedule = _apply_daily_schedule(settings, now)
    # 恢复持久化：清 evicted 旗标、记恢复事件、按需拉起（dry-run 不走到这里）
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
              "keepalive_skipped": [
                  {"account_id": aid, "reason": r}
                  for aid, r in plan.keepalive_skipped],
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
    check_interval_minutes；0=不刷新（关闭调度）；非法值回退 60 分钟，
    绝不让调度线程崩溃。"""
    for key in ("api_refresh_minutes", "check_interval_minutes"):
        try:
            v = int(float(settings.get(key, 60)))
            if 0 <= v <= 1440:
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
            rate = v.usage_rate
            evicted = (rate is not None
                       and rate >= engine._eff_protection(v, cfg_obj))
            if evicted:
                # B-03：被踢出而跳过时不写开机旗标，否则当天的开机机会
                # 就被这次跳过消耗掉，恢复后当天也不再补开机。
                msg = (f"{a['name']} 定时开机已跳过（已跳出：使用率 {rate:.1f}% "
                       f"≥ 阈值 {engine._eff_protection(v, cfg_obj):.0f}%）")
                done.append(msg)
                db.add_event("schedule", "定时开机已跳过", msg)
            else:
                db.set_flag(on_key, now.isoformat())
                if (v.status or "") == "Stopped":
                    try:
                        client = (cloud_factory(a, secret) if cloud_factory
                                  else AliyunClient(a, secret))
                        client.start_instance()
                        # 定时开机成功 → 清停机原因标记，保活恢复默认拉起
                        db.clear_stop_reason(a["id"])
                        msg = (f"{a['name']} 定时开机已执行"
                               f"（每日 {a.get('daily_on_time') or '08:00'}）")
                        done.append(msg)
                        db.add_event("schedule", "定时开机", msg)
                        # 推送：实例启动，原因=定时开机（开关默认开）
                        Notifier(settings).send(
                            "instance_start", "实例启动",
                            f"{a['name']} 已启动，原因：定时开机"
                            f"（每日 {a.get('daily_on_time') or '08:00'}）")
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
                    # 定时关机成功 → 记 schedule（窗口外本来也不拉，双重保险）。
                    db.set_stop_reason(a["id"], "schedule")
                    msg = (f"{a['name']} 定时关机已执行"
                           f"（每日 {a.get('daily_off_time') or '20:00'}）")
                    done.append(msg)
                    db.add_event("schedule", "定时关机", msg)
                    # 推送：实例停机，原因=定时关机（开关默认开）
                    Notifier(settings).send(
                        "instance_stop", "实例停机",
                        f"{a['name']} 已停机（普通停机），原因：定时关机"
                        f"（每日 {a.get('daily_off_time') or '20:00'}）")
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
                # B-04：24 小时兜底只在流量数据新鲜（1 小时内有采样）时触发；
                # 若流量接口连续几天查不到，rate 是旧值，兜底会按旧数据误恢复。
                # 无采样记录时无法判断新旧，沿用旧行为（不因此卡住恢复）。
                latest = db.get_latest_traffic_sample_at(aid)
                now_ts = int(now.timestamp())
                if latest is None or now_ts - latest < 3600:
                    fallback = True  # 超时兜底恢复
                else:
                    continue  # 采样太旧：不兜底，等下次刷新拿到新流量再说
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
        # B-02：恢复旗标只在真正执行/记录恢复时才写。原来在判断之前就写成
        # 现在，若本轮因定时关机窗口未拉起，下次会误认为“1 小时内已拉起过”。
        if not within_hour:
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
                    # 阈值恢复拉起成功 → 清停机原因标记（用量已回到恢复线
                    # 以下，排除随恢复解除，与现有恢复逻辑一致）。
                    db.clear_stop_reason(aid)
                    msg = (f"阈值恢复，已拉起：{a['name']}"
                           f"（使用率 {rate:.1f}% < 恢复线 {line:.0f}%）")
                    done.append(msg)
                    # 事件标题=重新加入 DDNS（保证性要求的可观测性），
                    # 详情=拉起文案（上一条“恢复默认拉起”的要求）：两者都留档。
                    db.add_event("recovery", title, msg)
                    # 推送：实例启动，原因=阈值恢复（开关默认开）
                    Notifier(settings).send(
                        "instance_start", "实例启动",
                        f"{a['name']} 已启动，原因：阈值恢复"
                        f"（使用率 {rate:.1f}% < 恢复线 {line:.0f}%）")
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


def _maybe_notify_update(settings: dict) -> bool:
    """发现面板新版本时推送一次到 TG/Email（每个版本只推一次）。

    每轮（60 秒）都检查一次：update.status() 自带 1 小时 GitHub 缓存，
    开销只是一次字典查找。满足以下条件才推：
    - update_available 为真（当前版本不是 dev 且与最新 SHA 不同）；
    - 该最新版本还没推过（flags.update_notified_version != latest）。
    推完把最新 SHA 记到 flags.update_notified_version；下次再有更新
    （SHA 变了）才再推。开关关闭时不推也不记（Notifier.send 会拦）。
    调度暂停时也检查（与每日财报同款位置），只为让更新通知不受调度影响。
    返回 True 表示本次推送了更新通知。
    """
    from . import update as updater

    try:
        st = updater.status()
    except Exception as exc:  # noqa: BLE001 - 检查失败不影响调度
        log.warning("面板更新检查失败（跳过）: %s", exc)
        return False
    if not st.get("update_available"):
        return False
    latest = st.get("latest_version") or ""
    if not latest:
        return False
    if db.get_flag("update_notified_version") == latest:
        return False
    title = "面板有新版本"
    body = (
        f"当前版本：{(st.get('current_version') or '')[:7] or '—'}\n"
        f"最新版本：{latest[:7]}\n"
        f"{(st.get('latest_message') or '').splitlines()[0] if st.get('latest_message') else ''}\n"
        "请到面板右上角铃铛点“立即更新”。"
    ).strip()
    notifier = Notifier(settings)
    sent = notifier.send("update_available", title, body)
    if sent:
        db.set_flag("update_notified_version", latest)
        db.add_event("update_available", title, body)
        return True
    if getattr(notifier, "last_errors", None):
        db.add_event("error", "更新通知发送失败",
                     f"{title}："
                     + "; ".join(f"{k}：{v}" for k, v in
                                 notifier.last_errors.items()))
    return False


def _maybe_send_daily_report(settings: dict, now: dt.datetime) -> bool:
    """每天在 daily_report_time（默认 00:00，可配）推送每日财报。

    规则（用 flags 持久化，重启不丢）：
    - 每天固定时间，各渠道各推送一次。推送按“日期 + 设定时间 + 渠道”
      记成功（flags.daily_report_sent:<YYYY-MM-DD>:<HH:MM>:<渠道>）：
      同一设定时间同一渠道当天只推一次，已成功的绝不重发；
      失败的渠道每轮重试，直到成功为止。
    - 改时间后也推送：设定时间一改，新的“日期 + 时间”就是一次新的
      推送（比如 00:00 已推过，改成 12:00 后到 12:00 依旧各推一次；
      改成已过的时间，下一轮就补推，不等明天）。
    - 全新数据库第一次检查不补推（last_check 为空时只记下次检查起点）；
    - 调度暂停或不刷新（API 刷新时间=0）时不推，但仍更新 last_check。
    - 部分渠道失败时记一条 error 事件（以前只有全部失败才留痕），
      同一渠道同一设定时间只记一次，避免每分钟重试都刷屏。
    返回 True 表示本次检查触发了推送（或补推）。
    """
    hh, mm = _parse_hhmm(settings.get("daily_report_time") or "00:00")
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    today = now.strftime("%Y-%m-%d")
    time_key = f"{hh:02d}:{mm:02d}"
    last_raw = db.get_flag("daily_report_last_check")
    # 上次处理推送时刻时见到的设定时间；与当前设定时间不同即改过时间。
    time_seen = db.get_flag("daily_report_time_seen")

    def _touch_last_check() -> None:
        db.set_flag("daily_report_last_check", now.isoformat())

    # 注意：这里不因全局 daily_report_enabled=0 而直接返回——过滤后无账户时
    # 再返回（全局关时过滤结果为空，自然不推）。
    # 调度暂停或不刷新（API 刷新时间=0）时不推。
    if settings.get("scheduler_paused") == "1" or _refresh_minutes(settings) == 0:
        _touch_last_check()
        return False
    if not last_raw:
        _touch_last_check()
        return False  # 全新数据库第一次检查：不补推，等下一次到点
    try:
        last = dt.datetime.fromisoformat(last_raw)
    except ValueError:
        _touch_last_check()
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=now.tzinfo)
    if now < due:
        return False  # 未到推送时刻，不写旗标（B-09）
    # 设定时间是否改过：只有到点后才更新 time_seen，未到点不更新，
    # 这样改到未来的时间在到点时仍能认出是“改过时间”。
    time_changed = time_seen != "" and time_seen != time_key
    db.set_flag("daily_report_time_seen", time_key)
    # 到点（或已过）：只推这个“日期 + 设定时间”还没成功的渠道。
    # 已处理过（发过 / 因关开关、无账户、无渠道而跳过）的“日期 + 时间”
    # 不再重复处理；改时间会产生新的“日期 + 时间”，照常推送。
    # 上次检查已在推送时刻之后才认“已处理”（测试重置 last_check 后
    # 应视为重新到点）。
    handled_key = f"daily_report_handled:{today}:{time_key}"
    if db.get_flag(handled_key) and last >= due:
        return False
    enabled = []
    if settings.get("telegram_enabled") == "1":
        enabled.append("telegram")
    if settings.get("email_enabled") == "1":
        enabled.append("email")
    # 是否已尝试过这个“日期 + 时间”（有渠道已推成功，或有渠道失败留痕）：
    # 尝试过 → 是重试；没尝试过、时间没改过、上次检查已过时刻 → 错过，不补推。
    attempted = any(
        db.get_flag(f"daily_report_sent:{today}:{time_key}:{ch}")
        or db.get_flag(f"daily_report_fail_logged:{today}:{time_key}:{ch}")
        for ch in enabled)
    if not time_changed and not attempted and not last < due:
        # 设定时间没改过、也没尝试过、且上次检查已在推送时刻之后
        # （比如时区切换后同一时刻已过、或面板在到点时正好停着）：
        # 当天不补推，记已处理，等明天。
        db.set_flag(handled_key, now.isoformat())
        _touch_last_check()
        return False
    accounts = db.list_accounts()
    if not accounts:
        db.set_flag(handled_key, now.isoformat())
        _touch_last_check()
        return False
    from .api import _public_account, _status_context  # 延迟导入，避免循环导入
    from .notify import filter_daily_report_accounts
    serving_map, queue_ranks = _status_context(accounts)
    pub = [_public_account(a, serving_map, queue_ranks) for a in accounts]
    pub = filter_daily_report_accounts(pub, settings)
    if not pub:
        # 全局关：不推，记已处理（重开后不会立刻补推），并更新 last_check。
        db.set_flag(handled_key, now.isoformat())
        _touch_last_check()
        return False
    notifier = Notifier(settings)
    if not notifier.enabled("daily_report"):
        db.set_flag(handled_key, now.isoformat())
        _touch_last_check()
        return False
    if not enabled:
        # 未启用任何通知渠道：记已处理，每个“日期 + 时间”只记一次。
        db.set_flag(handled_key, now.isoformat())
        _touch_last_check()
        db.add_event("daily_report", "每日财报未推送",
                     f"{today} 已到推送时间，但未启用任何通知渠道")
        return True
    pending = [ch for ch in enabled
               if not db.get_flag(
                   f"daily_report_sent:{today}:{time_key}:{ch}")]
    if not pending:
        return False  # 这个设定时间各渠道都已推过
    # 有渠道已推过、现在只推剩下的，就是补推；一个都没推过就是首次推送。
    is_retry = len(pending) < len(enabled)
    _touch_last_check()
    sent: list[str] = []
    failed: dict[str, str] = {}
    for ch in pending:
        got = notifier.send_daily_report(pub, today, skip_global_switch=True,
                                         channel=ch)
        if ch in got:
            db.set_flag(f"daily_report_sent:{today}:{time_key}:{ch}",
                        now.isoformat())
            sent.append(ch)
        else:
            detail = (getattr(notifier, "last_errors", None) or {}).get(ch, "")
            failed[ch] = detail
    if sent:
        title = "每日财报已补推" if is_retry else "每日财报已推送"
        db.add_event("daily_report", title,
                     f"{today} {time_key} 共 {len(pub)} 个账户，"
                     f"渠道：{','.join(sent)}")
    if failed:
        _log_daily_fail(today, time_key, failed, now)
    return True


def _log_daily_fail(today: str, time_key: str, errors: dict,
                    now: dt.datetime) -> None:
    """记一条每日财报推送失败的 error 事件；同一渠道同一设定时间只记一次，
    避免每分钟重试都刷一条。"""
    new_fail = [ch for ch in errors
                if not db.get_flag(
                    f"daily_report_fail_logged:{today}:{time_key}:{ch}")]
    if not new_fail:
        return
    for ch in new_fail:
        db.set_flag(f"daily_report_fail_logged:{today}:{time_key}:{ch}",
                    now.isoformat())
    db.add_event("error", "每日财报推送失败",
                 f"{today} {time_key} 通知渠道失败："
                 + "; ".join(f"{ch}：{errors[ch] or '发送失败'}"
                             for ch in new_fail))


def _maybe_cleanup_daily(settings: dict,
                         now: dt.datetime | None = None) -> bool:
    """每天在 cleanup_time（默认 03:00，可配）到达后清理一次
    （见 db.run_cleanup：过期事件/流量采样 + VACUUM + WAL 检查点 +
    数据目录临时文件）。
    用 flags.cleanup_done 记日期，重启当天不重复；到点前不跑；
    清理失败只记日志，不影响调度轮次，失败也记当天已处理、明天再试。
    返回 True 表示当天清理已执行。"""
    now = now or _now()
    today = now.strftime("%Y-%m-%d")
    if db.get_flag("cleanup_done") == today:
        return False
    hh, mm = _parse_hhmm(db.norm_cleanup_time(settings.get("cleanup_time")))
    if (now.hour, now.minute) < (hh, mm):
        return False
    try:
        result = db.run_cleanup(now=int(now.timestamp()),
                                ran_at=now.strftime("%Y-%m-%d %H:%M"))
        log.info("数据清理：事件 %s 条，流量采样 %s 条，临时文件 %s 个，"
                 "回收 %s 字节",
                 result["events_deleted"], result["traffic_samples_deleted"],
                 result["temp_files_deleted"], result["db_size_reclaimed"])
    except Exception as exc:  # noqa: BLE001
        log.warning("数据清理失败: %s", exc)
    db.set_flag("cleanup_done", today)
    return True


def last_run() -> dict:
    with _lock:
        return dict(_last_run)


DDNS_CHECK_INTERVAL_SECONDS = 60  # DDNS 检查固定每分钟一次，无后端设置


def ddns_check() -> dict:
    """DDNS 检查：每分钟一次（硬编码，无后端设置）。刷新到期的账户
    （按各账户生效的 API 刷新时间：自定义或跟随全局），再用库里已有
    的数据重算分配并同步 DNS；不写调度事件、不建 job，
    避免每分钟一条日志刷屏。"""
    return run_once(refresh=True, quiet=True)


def _loop() -> None:
    last_full = 0.0  # 上次完整轮次（带 API 刷新）的时间戳
    while not _stop.is_set():
        settings = db.get_settings()
        try:
            # 每日财报：每轮检查一次到点与否（暂停时也检查，只更新检查时刻不推送）
            _maybe_send_daily_report(settings, _now())
        except Exception as exc:  # noqa: BLE001
            log.exception("每日财报检查失败: %s", exc)
        try:
            # 面板更新通知：每轮检查一次，有新版本且未推过时推 TG/Email
            _maybe_notify_update(settings)
        except Exception as exc:  # noqa: BLE001
            log.exception("面板更新通知检查失败: %s", exc)
        # 不刷新（API 刷新时间=0）相当于关闭调度：与暂停一样只等 60 秒，
        # 不跑轮次、不做 DDNS 检查、不推每日财报（只更新财报检查时刻）。
        if settings.get("scheduler_paused") == "1" or _refresh_minutes(settings) == 0:
            _wake.wait(timeout=60)
            _wake.clear()
            continue
        try:
            _maybe_cleanup_daily(settings)
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
    """启动调度线程。P0-01：先等旧线程真正退出再清 _stop、建新线程，
    否则旧线程跑完即退出、新线程又没启动，调度会永久静默停掉。"""
    global _thread
    with _thread_lock:
        old = _thread
        if old is not None and old.is_alive():
            return
        if old is not None:
            # 已不在运行：join 立即返回，只是回收线程对象。
            old.join(timeout=10)
        _stop.clear()
        _wake.clear()
        _thread = threading.Thread(target=_loop, name="cdt-scheduler", daemon=True)
        _thread.start()


def stop(timeout: float = 30) -> None:
    """停掉调度线程并等本轮结束（带超时 join，避免停机接口一直挂起）。
    仍在跑一轮时 _loop 会在轮次结束、进入 _wake.wait 前退出。"""
    _stop.set()
    _wake.set()
    with _thread_lock:
        t = _thread
    if t is not None:
        t.join(timeout=timeout)


def wake() -> None:
    _wake.set()
