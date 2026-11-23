import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.engine import (AccountView, Action, Config, DomainView, execute_plan,
                        plan_reconcile)

TZ = ZoneInfo("Asia/Shanghai")


def acc(i, used, status="Running", ip=None):
    return AccountView(id=i, name=f"A{i}", quota_gb=200, used_gb=used,
                       status=status, public_ip=ip if ip is not None else f"203.0.113.{i}")


def dom(i, current=None, ip="", gid=1):
    # 默认归默认分组（id=1）；gid=None 表示未绑定域名，不参与任何分组调度
    return DomainView(id=i, name=f"www{i}.example.xyz", priority=i - 1,
                      current_account_id=current, current_ip=ip, group_id=gid)


def noon():
    return dt.datetime(2026, 11, 22, 12, 0, tzinfo=TZ)


def test_assign_lowest_usage_first_and_cycle():
    cfg = Config()
    plan = plan_reconcile([acc(1, 100), acc(2, 20), acc(3, 60)],
                          [dom(1), dom(2), dom(3), dom(4)], cfg, noon())
    # 域名按优先级分给使用率最低的账户；第 4 个域名循环回最低账户
    assert plan.assignments == {1: 2, 2: 3, 3: 1, 4: 2}


def test_threshold_eviction_switches_and_excludes():
    cfg = Config(exclusion_action="stop_charging")
    # A1 190/200=95% 已达阈值，当前指向 A1；应切到 A2 并对 A1 执行节省停机
    plan = plan_reconcile([acc(1, 190, ip="203.0.113.1"), acc(2, 20)],
                          [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    assert 1 in plan.evicted_account_ids
    kinds = [(a.kind, a.reason) for a in plan.actions]
    assert ("dns", "threshold_eviction") in kinds
    assert ("stop", "exclusion") in kinds
    assert any(e == "traffic_protection" for e, _, _ in plan.notifications)


def test_whole_pool_over_threshold_falls_back():
    cfg = Config()
    plan = plan_reconcile([acc(1, 196), acc(2, 192)],
                          [dom(1, current=1)], cfg, noon())
    assert plan.assignments == {1: None}
    assert plan.fallback_domain_ids == [1]
    assert any(a.kind == "dns_fallback" for a in plan.actions)
    assert any(e == "fallback" for e, _, _ in plan.notifications)


def test_traffic_diff_switch_and_no_switch_below_diff():
    cfg = Config()
    # 差值 80% - 10% = 70 >= 10 → 切
    plan = plan_reconcile([acc(1, 160), acc(2, 20)],
                          [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    assert any(a.kind == "dns" and a.reason == "traffic_diff" for a in plan.actions)
    # 差值 30-20=10%... 用 25 vs 20：差 2.5% < 10% → 不切
    plan2 = plan_reconcile([acc(1, 50), acc(2, 40)],
                           [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    assert not [a for a in plan2.actions if a.kind == "dns"]


def test_night_optimize_once_per_night():
    cfg = Config()
    night = dt.datetime(2026, 11, 22, 1, 30, tzinfo=TZ)
    # 差值仅 2.5%，白天不切；夜间第一次切；同一晚第二次不切
    plan = plan_reconcile([acc(1, 50), acc(2, 45)],
                          [dom(1, current=1, ip="203.0.113.1")], cfg, night)
    assert any(a.kind == "dns" and a.reason == "night_optimize" for a in plan.actions)
    assert plan.night_done_date == "2026-11-22"
    plan2 = plan_reconcile([acc(1, 50), acc(2, 45)],
                           [dom(1, current=1, ip="203.0.113.1")], cfg, night,
                           night_done_dates={"2026-11-22"})
    assert not [a for a in plan2.actions if a.kind == "dns"]


def test_fixed_time_switch():
    # 关闭夜间优化，避免 02:00 落在夜间窗口内干扰固定时刻的断言
    cfg = Config(fixed_switch_time="03:00", night_optimize_enabled=False)
    early = dt.datetime(2026, 11, 22, 2, 0, tzinfo=TZ)
    late = dt.datetime(2026, 11, 22, 3, 30, tzinfo=TZ)
    p1 = plan_reconcile([acc(1, 50), acc(2, 45)],
                        [dom(1, current=1, ip="203.0.113.1")], cfg, early)
    assert not [a for a in p1.actions if a.kind == "dns"]
    p2 = plan_reconcile([acc(1, 50), acc(2, 45)],
                        [dom(1, current=1, ip="203.0.113.1")], cfg, late)
    assert any(a.kind == "dns" and a.reason == "fixed_time" for a in p2.actions)


def test_keepalive_starts_stopped_target():
    cfg = Config()
    plan = plan_reconcile([acc(1, 100, status="Stopped"), acc(2, 20)],
                          [dom(1, current=2, ip="203.0.113.2")], cfg, noon())
    # 当前 A2 是最低？A2 20 最低，域名已在 A2 且 Running → 无动作；换个场景：
    plan2 = plan_reconcile([acc(1, 20, status="Stopped"), acc(2, 100)],
                           [dom(1, current=2, ip="203.0.113.2")], cfg, noon())
    assert any(a.kind == "start" for a in plan2.actions)


def test_unknown_traffic_is_conservative():
    cfg = Config()
    unknown = AccountView(id=1, name="A1", quota_gb=200, used_gb=None,
                          status="Running", public_ip="203.0.113.1")
    plan = plan_reconcile([unknown, acc(2, 20)],
                          [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    assert not [a for a in plan.actions if a.kind == "stop"]
    assert not [a for a in plan.actions
                if a.kind == "dns" and a.reason == "threshold_eviction"]


class FakeCloud:
    def __init__(self):
        self.calls = []
    def start(self, account):
        self.calls.append(("start", account.id))
    def stop(self, account, stop_charging):
        self.calls.append(("stop", account.id, stop_charging))


class FakeDns:
    def __init__(self):
        self.calls = []
    def set_record(self, domain, ip):
        self.calls.append((domain.name, ip))
        return True


def test_dry_run_makes_no_calls():
    cfg = Config()
    plan = plan_reconcile([acc(1, 190), acc(2, 20)],
                          [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    assert plan.actions  # 有动作要执行，dry-run 仍应零调用
    cloud, dns = FakeCloud(), FakeDns()
    res = execute_plan(plan, [acc(1, 190), acc(2, 20)], [dom(1, current=1)],
                       cloud, dns, dry_run=True)
    assert res["dry_run"] is True
    assert cloud.calls == [] and dns.calls == []


def test_execute_plan_order_start_dns_stop():
    cfg = Config(exclusion_action="stop")
    accounts = [acc(1, 190), acc(2, 20)]
    plan = plan_reconcile(accounts, [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    cloud, dns = FakeCloud(), FakeDns()
    res = execute_plan(plan, accounts, [dom(1, current=1)], cloud, dns, dry_run=False)
    assert cloud.calls == [("stop", 1, False)]
    assert dns.calls == [("www1.example.xyz", "203.0.113.2")]
    assert res["executed"]


# ---------------------------------------------------------------------------
# 细化 1：阈值踢出 — 停机可选（none = 只踢出、不停机）
# ---------------------------------------------------------------------------

def test_threshold_eviction_none_switches_dns_but_no_stop():
    cfg = Config(exclusion_action="none")
    accounts = [acc(1, 190), acc(2, 20)]
    plan = plan_reconcile(accounts, [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    assert 1 in plan.evicted_account_ids
    assert any(a.kind == "dns" and a.reason == "threshold_eviction" for a in plan.actions)
    assert not [a for a in plan.actions if a.kind == "stop"]
    # 真执行：DNS 改了，停机一次都没调
    cloud, dns = FakeCloud(), FakeDns()
    execute_plan(plan, accounts, [dom(1, current=1)], cloud, dns, dry_run=False)
    assert dns.calls == [("www1.example.xyz", "203.0.113.2")]
    assert cloud.calls == []


def test_evicted_account_readmitted_when_usage_falls():
    cfg = Config()
    # 190 GB 时被踢出；下月/用量回落到 100 GB 后自动恢复可分配
    from app.engine import assign_domains, is_allocatable
    assert not is_allocatable(acc(1, 190), cfg)
    assert is_allocatable(acc(1, 100), cfg)
    assert assign_domains([dom(1)], [acc(1, 100), acc(2, 120)], cfg) == {1: 1}


def test_eviction_stop_deduped_across_domains():
    cfg = Config(exclusion_action="stop")
    accounts = [acc(1, 190), acc(2, 20)]
    plan = plan_reconcile(accounts,
                          [dom(1, current=1, ip="203.0.113.1"),
                           dom(2, current=1, ip="203.0.113.1")], cfg, noon())
    assert [a for a in plan.actions if a.kind == "stop"].__len__() == 1
    assert len([a for a in plan.actions if a.kind == "dns"]) == 2


def test_stop_always_after_dns_switch():
    cfg = Config(exclusion_action="stop")
    accounts = [acc(1, 190), acc(2, 20)]
    plan = plan_reconcile(accounts, [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    cloud, dns = FakeCloud(), FakeDns()
    res = execute_plan(plan, accounts, [dom(1, current=1)], cloud, dns, dry_run=False)
    dns_i = next(i for i, e in enumerate(res["executed"]) if e.startswith("dns:"))
    stop_i = next(i for i, e in enumerate(res["executed"]) if e.startswith("stop:"))
    assert dns_i < stop_i


# ---------------------------------------------------------------------------
# 细化 2：按时间切换 — 间隔 / 固定时刻
# ---------------------------------------------------------------------------

def test_interval_switch_2h_only_when_due():
    cfg = Config(time_switch_mode="interval", interval_hours=2,
                 night_optimize_enabled=False)
    base = dt.datetime(2026, 11, 22, 12, 0, tzinfo=TZ)
    accts = [acc(1, 50), acc(2, 45)]
    d = [dom(1, current=1, ip="203.0.113.1")]
    # 锚点 1 小时前：未到 2 小时，不切
    p1 = plan_reconcile(accts, d, cfg, base,
                        last_time_switch_at=base - dt.timedelta(hours=1))
    assert not [a for a in p1.actions if a.kind == "dns"]
    assert p1.time_switch_at is None
    # 锚点 2 小时前：到点，不论差值切到最低
    p2 = plan_reconcile(accts, d, cfg, base,
                        last_time_switch_at=base - dt.timedelta(hours=2))
    assert any(a.kind == "dns" and a.reason == "interval_switch" for a in p2.actions)
    assert p2.time_switch_at == base
    # 锚点更新为本轮后：下一轮不再切
    p3 = plan_reconcile(accts, d, cfg, base + dt.timedelta(minutes=30),
                        last_time_switch_at=base)
    assert not [a for a in p3.actions if a.kind == "dns"]


def test_interval_first_check_sets_anchor_without_switching():
    cfg = Config(time_switch_mode="interval", interval_hours=2,
                 night_optimize_enabled=False)
    now = dt.datetime(2026, 11, 22, 12, 30, tzinfo=TZ)  # 不在整点边界
    p = plan_reconcile([acc(1, 50), acc(2, 45)],
                       [dom(1, current=1, ip="203.0.113.1")], cfg, now,
                       last_time_switch_at=None)
    assert not [a for a in p.actions if a.kind == "dns"]
    # 有开始时间（默认 00:00:00）时锚点取上一个切换时刻 12:00，而不是 now，
    # 下轮从 12:00 起按 2 小时计时（14:00 切）
    assert p.interval_anchor_at == dt.datetime(2026, 11, 22, 12, 0, tzinfo=TZ)


def test_interval_clock_alignment_6h():
    cfg = Config(time_switch_mode="interval", interval_hours=6,
                 night_optimize_enabled=False)
    # 无锚点且正好 06:00（00/06/12/18 边界）：到点切
    now = dt.datetime(2026, 11, 22, 6, 0, tzinfo=TZ)
    p = plan_reconcile([acc(1, 50), acc(2, 45)],
                       [dom(1, current=1, ip="203.0.113.1")], cfg, now,
                       last_time_switch_at=None)
    assert any(a.kind == "dns" and a.reason == "interval_switch" for a in p.actions)


def test_fixed_time_mode_once_per_day():
    cfg = Config(time_switch_mode="fixed_time", fixed_switch_time="03:00",
                 night_optimize_enabled=False)
    late = dt.datetime(2026, 11, 22, 3, 30, tzinfo=TZ)
    p = plan_reconcile([acc(1, 50), acc(2, 45)],
                       [dom(1, current=1, ip="203.0.113.1")], cfg, late)
    assert any(a.kind == "dns" and a.reason == "fixed_time" for a in p.actions)
    assert p.fixed_done_date == "2026-11-22"
    p2 = plan_reconcile([acc(1, 50), acc(2, 45)],
                        [dom(1, current=1, ip="203.0.113.1")], cfg, late,
                        fixed_done_dates={"2026-11-22"})
    assert not [a for a in p2.actions if a.kind == "dns"]


def test_interval_start_time_anchors_to_clock():
    # 开始时间 00:00:00、间隔 2 小时：切换时刻 = 00:00/02:00/04:00…
    cfg = Config(time_switch_mode="interval", interval_hours=2,
                 interval_start_time="00:00:00", night_optimize_enabled=False)
    accts = [acc(1, 50), acc(2, 45)]
    d = [dom(1, current=1, ip="203.0.113.1")]
    # 无锚点且正好 02:00（切换时刻）：到点切
    now = dt.datetime(2026, 11, 22, 2, 0, tzinfo=TZ)
    p = plan_reconcile(accts, d, cfg, now, last_time_switch_at=None)
    assert any(a.kind == "dns" and a.reason == "interval_switch" for a in p.actions)
    # 无锚点且 02:30（不在切换时刻）：不切，锚点取上一个切换时刻 02:00
    now2 = dt.datetime(2026, 11, 22, 2, 30, tzinfo=TZ)
    p2 = plan_reconcile(accts, d, cfg, now2, last_time_switch_at=None)
    assert not [a for a in p2.actions if a.kind == "dns"]
    assert p2.interval_anchor_at == dt.datetime(2026, 11, 22, 2, 0, tzinfo=TZ)
    # 有锚点：按上次切换锚点计时，2 小时未到不切、到了切
    p3 = plan_reconcile(accts, d, cfg, dt.datetime(2026, 11, 22, 3, 30, tzinfo=TZ),
                        last_time_switch_at=dt.datetime(2026, 11, 22, 2, 0, tzinfo=TZ))
    assert not [a for a in p3.actions if a.kind == "dns"]
    p4 = plan_reconcile(accts, d, cfg, dt.datetime(2026, 11, 22, 4, 0, tzinfo=TZ),
                        last_time_switch_at=dt.datetime(2026, 11, 22, 2, 0, tzinfo=TZ))
    assert any(a.kind == "dns" and a.reason == "interval_switch" for a in p4.actions)


def test_interval_start_time_custom_minutes():
    # 开始时间 08:00:00、自定义 90 分钟：切换时刻 = 08:00/09:30/11:00…
    cfg = Config(time_switch_mode="interval", interval_hours=0,
                 custom_interval_minutes=90, interval_start_time="08:00:00",
                 night_optimize_enabled=False)
    accts = [acc(1, 50), acc(2, 45)]
    d = [dom(1, current=1, ip="203.0.113.1")]
    now = dt.datetime(2026, 11, 22, 9, 30, tzinfo=TZ)
    p = plan_reconcile(accts, d, cfg, now, last_time_switch_at=None)
    assert any(a.kind == "dns" and a.reason == "interval_switch" for a in p.actions)
    now2 = dt.datetime(2026, 11, 22, 10, 0, tzinfo=TZ)
    p2 = plan_reconcile(accts, d, cfg, now2, last_time_switch_at=None)
    assert not [a for a in p2.actions if a.kind == "dns"]
    assert p2.interval_anchor_at == dt.datetime(2026, 11, 22, 9, 30, tzinfo=TZ)


def test_interval_start_time_invalid_falls_back():
    # 非法开始时间：当作没设，走旧的整点对齐逻辑（6h=00/06/12/18）
    cfg = Config(time_switch_mode="interval", interval_hours=6,
                 interval_start_time="99:99", night_optimize_enabled=False)
    now = dt.datetime(2026, 11, 22, 6, 0, tzinfo=TZ)
    p = plan_reconcile([acc(1, 50), acc(2, 45)],
                       [dom(1, current=1, ip="203.0.113.1")], cfg, now,
                       last_time_switch_at=None)
    assert any(a.kind == "dns" and a.reason == "interval_switch" for a in p.actions)


def test_group_time_defaults_to_interval_mode():
    # time 模式的分组未显式设置 time_switch_mode 时默认按 interval 走，
    # 否则默认 auto 下间隔永不触发；显式设置过的 auto 不受影响
    from app.engine import GroupView, _group_config
    g = GroupView(id=1, name="G", switch_mode="time", interval_hours=2)
    assert _group_config(g, Config()).time_switch_mode == "interval"
    g2 = GroupView(id=2, name="G2", switch_mode="time", interval_hours=2,
                   time_switch_mode="auto")
    assert _group_config(g2, Config()).time_switch_mode == "auto"
    g3 = GroupView(id=3, name="G3", switch_mode="load_balance")
    assert _group_config(g3, Config()).time_switch_mode == ""


def test_group_follow_global_inherits_switch_mode():
    # 顶层“跟随系统”（switch_mode=None）：有效模式继承全局；全局 time 时
    # time_switch_mode 同样默认 interval，全局 both 时有效模式就是 both
    from app.engine import GroupView, _group_config
    g = GroupView(id=1, name="G", switch_mode=None, interval_hours=2)
    assert g.switch_mode is None  # __post_init__ 保留 None，不回退 load_balance
    assert _group_config(g, Config(switch_mode="time")).switch_mode == "time"
    assert _group_config(g, Config(switch_mode="time")).time_switch_mode == "interval"
    assert _group_config(g, Config()).switch_mode == "both"
    assert _group_config(g, Config(switch_mode="load_balance")).switch_mode == "load_balance"
    # 显式模式不受影响
    g2 = GroupView(id=2, name="G2", switch_mode="time", interval_hours=2)
    assert _group_config(g2, Config()).switch_mode == "time"


def test_group_interval_start_time_override():
    # 分组开始时间覆盖全局：全局 00:00:00，分组 08:00:00、间隔 2 小时，
    # 08:00 到点切、07:00 不切（开始时间是锚点相位：切换时刻=08:00+k*2h）
    from app.engine import GroupView
    cfg = Config(night_optimize_enabled=False)
    g = GroupView(id=1, name="G", switch_mode="time", interval_hours=2,
                  interval_start_time="08:00:00")
    accts = [acc(1, 50), acc(2, 45)]
    d = [dom(1, current=1, ip="203.0.113.1")]
    p = plan_reconcile(accts, d, cfg, dt.datetime(2026, 11, 22, 8, 0, tzinfo=TZ),
                       groups=[g], last_switch_by_group={1: None})
    assert any(a.kind == "dns" and a.reason == "interval_switch" for a in p.actions)
    p2 = plan_reconcile(accts, d, cfg, dt.datetime(2026, 11, 22, 7, 0, tzinfo=TZ),
                        groups=[g], last_switch_by_group={1: None})
    assert not [a for a in p2.actions if a.kind == "dns"]


def test_interval_custom_minutes():
    cfg = Config(time_switch_mode="interval", interval_hours=0,
                 custom_interval_minutes=90, night_optimize_enabled=False)
    base = dt.datetime(2026, 11, 22, 12, 0, tzinfo=TZ)
    accts = [acc(1, 50), acc(2, 45)]
    d = [dom(1, current=1, ip="203.0.113.1")]
    p1 = plan_reconcile(accts, d, cfg, base,
                        last_time_switch_at=base - dt.timedelta(minutes=60))
    assert not [a for a in p1.actions if a.kind == "dns"]
    p2 = plan_reconcile(accts, d, cfg, base,
                        last_time_switch_at=base - dt.timedelta(minutes=90))
    assert any(a.kind == "dns" and a.reason == "interval_switch" for a in p2.actions)


# ---------------------------------------------------------------------------
# 细化 3：按流量均衡 — 差值支持 % 或 GB
# ---------------------------------------------------------------------------

def test_gb_diff_switches_on_12gb_gap_despite_small_pct_gap():
    cfg = Config(switch_diff_mode="gb", switch_diff_gb=10)
    # 100 vs 88 GB：差 12 GB ≥ 10 GB，但使用率差仅 6%（<10%），gb 模式应切
    plan = plan_reconcile([acc(1, 100), acc(2, 88)],
                          [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    assert any(a.kind == "dns" and a.reason == "traffic_diff_gb" for a in plan.actions)
    # 同样一对账户在 percent 模式下不切
    plan_pct = plan_reconcile([acc(1, 100), acc(2, 88)],
                              [dom(1, current=1, ip="203.0.113.1")], Config(), noon())
    assert not [a for a in plan_pct.actions if a.kind == "dns"]


def test_gb_diff_no_switch_on_5gb_gap():
    cfg = Config(switch_diff_mode="gb", switch_diff_gb=10)
    plan = plan_reconcile([acc(1, 100), acc(2, 95)],
                          [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    assert not [a for a in plan.actions if a.kind == "dns"]


def test_gb_diff_per_account_override():
    # 全局 percent 10%，A1 单独覆盖为 gb 模式 10 GB：100 vs 88 应切
    a1 = acc(1, 100)
    a1.switch_diff_mode = "gb"
    a1.switch_diff_gb = 10
    plan = plan_reconcile([a1, acc(2, 88)],
                          [dom(1, current=1, ip="203.0.113.1")], Config(), noon())
    assert any(a.kind == "dns" and a.reason == "traffic_diff_gb" for a in plan.actions)


def test_ip_change_resyncs_dns_same_instance():
    # 实例重启换 IP：域名仍指向同一台，但记录是旧 IP → 必须重新同步 DNS
    cfg = Config()
    plan = plan_reconcile(
        [acc(1, 20, ip="203.0.113.99"), acc(2, 60)],
        [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    kinds = [(a.kind, a.reason, a.target_ip) for a in plan.actions]
    assert ("dns", "ip_changed", "203.0.113.99") in kinds


def test_ip_unchanged_no_dns_action():
    # IP 没变：已是目标时不产生多余 DNS 动作，避免无意义的 CF 查询
    cfg = Config()
    plan = plan_reconcile(
        [acc(1, 20, ip="203.0.113.1"), acc(2, 60)],
        [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    assert [a for a in plan.actions if a.kind == "dns"] == []


def test_first_ip_syncs_dns():
    # 域名从未解析过（current_ip 为空）：实例有 IP 就补解析
    cfg = Config()
    plan = plan_reconcile(
        [acc(1, 20, ip="203.0.113.99"), acc(2, 60)],
        [dom(1, current=1, ip="")], cfg, noon())
    kinds = [(a.kind, a.reason, a.target_ip) for a in plan.actions]
    assert ("dns", "ip_changed", "203.0.113.99") in kinds


def test_stopped_target_without_ip_only_starts():
    # 切到已停机目标且还没拿到 IP：只拉起，不产生空 IP 的 DNS 动作
    # （下轮 Running 拿到新 IP 后由 ip_changed 分支重新同步）
    cfg = Config()
    plan = plan_reconcile(
        [acc(1, 160), acc(2, 20, status="Stopped", ip="")],
        [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    dns = [a for a in plan.actions if a.kind == "dns"]
    assert dns == []
    assert any(a.kind == "start" and a.account_id == 2 for a in plan.actions)


def test_account_in_multiple_groups_scheduled_in_each():
    # 同一台实例在两个分组的池子里：两个域名各分给它（用量最低时）
    from app.engine import GroupView
    g1 = GroupView(id=1, name="A", switch_mode="time", time_switch_mode="interval",
                   interval_hours=2)
    g2 = GroupView(id=2, name="B", switch_mode="load_balance")
    a1 = acc(1, 10)
    a1.group_ids = [1, 2]
    a2 = acc(2, 90)
    a2.group_ids = [1]
    a3 = acc(3, 80)
    a3.group_ids = [2]
    cfg = Config(night_optimize_enabled=False)
    d1 = dom(1)
    d1.group_id = 1
    d2 = dom(2)
    d2.group_id = 2
    plan = plan_reconcile(
        [a1, a2, a3],
        [d1, d2], cfg, noon(),
        groups=[g1, g2],
        last_switch_by_group={1: noon() - dt.timedelta(hours=2)})
    assert plan.assignments[1] == 1  # 分组 A：a1 用量最低
    assert plan.assignments[2] == 1  # 分组 B：a1 也在池里，同样最低


def test_no_serving_instance_assigns_most_remaining_traffic():
    # 负载均衡：域名无人服务（都在排队）时，差值不到阈值也不卡住——
    # 直接分给池内剩余流量最多的（用量最低的），不受差值阈值限制。
    # 差值阈值只阻止“从正在服务的实例切走”。
    from app.engine import GroupView
    g = GroupView(id=1, name="Premium", switch_mode="load_balance")
    cfg = Config(night_optimize_enabled=False)
    # acc() 的第二个参数是已用 GB（quota 200）：9.48 GiB=4.74%，23.68 GiB=11.84%
    a1 = acc(1, 9.48)    # 剩余最多
    a2 = acc(2, 23.68)   # 差值 7.1% < 10%
    # 1) 无人服务
    plan = plan_reconcile([a1, a2], [dom(1)], cfg, noon(), groups=[g])
    assert plan.assignments[1] == 1
    assert any(a.kind == "dns" and a.account_id == 1 and a.reason == "initial_assign"
               for a in plan.actions)
    # 2) 当前实例流量未知：不再保守维持，同样分给剩余流量最多的
    a3 = acc(3, None)
    d = dom(1)
    d.current_account_id = 3
    plan = plan_reconcile([a1, a2, a3], [d], cfg, noon(), groups=[g])
    assert any(a.kind == "dns" and a.account_id == 1 and a.reason == "initial_assign"
               for a in plan.actions)
    assert not any(a.reason == "unknown_keep" for a in plan.actions)
    # 3) 当前实例已停机（差值不够也不卡住）
    a4 = acc(4, 16.0, status="Stopped")  # 8%
    d = dom(1)
    d.current_account_id = 4
    plan = plan_reconcile([a1, a2, a4], [d], cfg, noon(), groups=[g])
    assert any(a.kind == "dns" and a.account_id == 1 and a.reason == "initial_assign"
               for a in plan.actions)
    # 4) 正在服务、差值不足：仍不切（差值阈值继续保护正在服务的实例）
    d = dom(1)
    d.current_account_id = 2
    plan = plan_reconcile([a1, a2], [d], cfg, noon(), groups=[g])
    assert not any(a.kind == "dns" for a in plan.actions)
    # 5) 正在服务、差值够：按差值切
    a2b = acc(2, 40.0)  # 20%
    plan = plan_reconcile([a1, a2b], [d], cfg, noon(), groups=[g])
    assert any(a.kind == "dns" and a.account_id == 1 and a.reason == "traffic_diff"
               for a in plan.actions)


def test_unbound_domain_not_scheduled():
    # 未绑定域名（group_id NULL）不参与任何分组的调度
    from app.engine import GroupView
    g = GroupView(id=1, name="A", switch_mode="load_balance")
    cfg = Config(night_optimize_enabled=False)
    a1 = acc(1, 10)
    a1.group_id = 1
    a1.group_ids = [1]
    d = dom(1, gid=None)
    plan = plan_reconcile([a1], [d], cfg, noon(), groups=[g])
    assert plan.assignments == {}
    assert plan.actions == []
    # 绑到分组后正常调度
    d2 = dom(1, gid=1)
    plan = plan_reconcile([a1], [d2], cfg, noon(), groups=[g])
    assert plan.assignments[1] == 1


def test_paused_group_points_domain_to_zero():
    # 停止的分组：域名指向 0.0.0.0，不分配实例；恢复后正常分配
    from app.engine import GroupView
    g = GroupView(id=1, name="A", switch_mode="load_balance", paused=True)
    cfg = Config(night_optimize_enabled=False)
    a1 = acc(1, 10)
    a1.group_id = 1
    a1.group_ids = [1]
    d = dom(1, current=1, ip="203.0.113.1")
    plan = plan_reconcile([a1], [d], cfg, noon(), groups=[g])
    assert plan.assignments == {}
    assert 1 in plan.paused_domain_ids
    assert any(a.kind == "dns" and a.target_ip == "0.0.0.0"
               and a.reason == "group_paused" for a in plan.actions)
    # 已是 0.0.0.0 时不再重复发 DNS 动作
    d2 = dom(1, current=None, ip="0.0.0.0")
    plan = plan_reconcile([a1], [d2], cfg, noon(), groups=[g])
    assert 1 in plan.paused_domain_ids
    assert not any(a.kind == "dns" for a in plan.actions)
    # 恢复启动后正常分配
    g2 = GroupView(id=1, name="A", switch_mode="load_balance", paused=False)
    plan = plan_reconcile([a1], [d], cfg, noon(), groups=[g2])
    assert plan.assignments[1] == 1
    assert plan.paused_domain_ids == []
