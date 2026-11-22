"""顶层切换模式 switch_mode：time | load_balance | both（默认 both）。

- time：只按时间切换（夜间/间隔/固定时刻），差值切换关闭。
- load_balance：只按负载均衡（差值 %/GB），时间切换关闭。
- both：两者都启用（旧行为）。
阈值踢出/保活/首次分配/备用地址不受切换模式影响，三种模式都生效。
"""
import datetime as dt
from zoneinfo import ZoneInfo

from app.engine import (AccountView, Config, DomainView, execute_plan,
                        plan_reconcile)

TZ = ZoneInfo("Asia/Shanghai")


def acc(i, used, status="Running"):
    return AccountView(id=i, name=f"IP{i}", quota_gb=200, used_gb=used,
                       status=status, public_ip=f"203.0.113.{i}")


def dom(i, current=None):
    return DomainView(id=i, name=f"www{i}.example.xyz", priority=i - 1,
                      current_account_id=current, current_ip="")


def noon():
    return dt.datetime(2026, 11, 22, 12, 0, tzinfo=TZ)


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


# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------

def test_switch_mode_default_is_both():
    assert Config().switch_mode == "both"
    assert Config.from_settings({}).switch_mode == "both"
    assert Config.from_settings({"switch_mode": "time"}).switch_mode == "time"
    assert Config.from_settings({"switch_mode": "load_balance"}).switch_mode == "load_balance"
    # 非法值回落到 both，不静默变成别的模式
    assert Config.from_settings({"switch_mode": "bogus"}).switch_mode == "both"


# ---------------------------------------------------------------------------
# 4-IP 池：IP1 达阈值被踢出，不再参与 DDNS，域名切到最低的 IP2
# ---------------------------------------------------------------------------

def test_four_ip_pool_ip1_evicted_switches_to_lowest():
    # IP1: 192/200=96% ≥ 95% 阈值；IP2 20 GB 最低，IP3 40，IP4 60
    cfg = Config(exclusion_action="stop_charging")
    accounts = [acc(1, 192), acc(2, 20), acc(3, 40), acc(4, 60)]
    plan = plan_reconcile(accounts, [dom(1, current=1)], cfg, noon())
    assert 1 in plan.evicted_account_ids
    assert plan.assignments == {1: 2}  # 切到用量最低的 IP2，不是 IP3/IP4
    assert any(a.kind == "dns" and a.reason == "threshold_eviction"
               for a in plan.actions)
    # 执行顺序 start → dns → stop，且节省停机恰好一次、只停 IP1
    cloud, dns = FakeCloud(), FakeDns()
    res = execute_plan(plan, accounts, [dom(1, current=1)], cloud, dns, dry_run=False)
    assert cloud.calls == [("stop", 1, True)]
    assert dns.calls == [("www1.example.xyz", "203.0.113.2")]
    dns_i = next(i for i, e in enumerate(res["executed"]) if e.startswith("dns:"))
    stop_i = next(i for i, e in enumerate(res["executed"]) if e.startswith("stop:"))
    assert dns_i < stop_i


def test_four_ip_pool_eviction_none_no_stop():
    cfg = Config(exclusion_action="none")
    accounts = [acc(1, 192), acc(2, 20), acc(3, 40), acc(4, 60)]
    plan = plan_reconcile(accounts, [dom(1, current=1)], cfg, noon())
    assert 1 in plan.evicted_account_ids
    assert plan.assignments == {1: 2}
    assert not [a for a in plan.actions if a.kind == "stop"]
    cloud, dns = FakeCloud(), FakeDns()
    execute_plan(plan, accounts, [dom(1, current=1)], cloud, dns, dry_run=False)
    assert cloud.calls == []
    assert dns.calls == [("www1.example.xyz", "203.0.113.2")]


# ---------------------------------------------------------------------------
# switch_mode=time：差值再大也不切，时间到点才切
# ---------------------------------------------------------------------------

def test_time_mode_gb_gap_does_not_switch_until_interval_due():
    cfg = Config(switch_mode="time", switch_diff_mode="gb", switch_diff_gb=10,
                 time_switch_mode="interval", interval_hours=2,
                 night_optimize_enabled=False)
    accts = [acc(1, 100), acc(2, 88)]  # 差 12 GB，GB 模式本应切
    d = [dom(1, current=1)]
    base = noon()
    # 间隔未到：12 GB 差值也不切
    p1 = plan_reconcile(accts, d, cfg, base,
                        last_time_switch_at=base - dt.timedelta(hours=1))
    assert not [a for a in p1.actions if a.kind == "dns"]
    # 间隔到点：不论差值切到最低
    p2 = plan_reconcile(accts, d, cfg, base,
                        last_time_switch_at=base - dt.timedelta(hours=2))
    assert any(a.kind == "dns" and a.reason == "interval_switch"
               for a in p2.actions)


def test_time_mode_night_still_works():
    cfg = Config(switch_mode="time", night_optimize_enabled=True)
    night = dt.datetime(2026, 11, 22, 1, 30, tzinfo=TZ)
    plan = plan_reconcile([acc(1, 50), acc(2, 45)],
                          [dom(1, current=1)], cfg, night)
    assert any(a.kind == "dns" and a.reason == "night_optimize"
               for a in plan.actions)


# ---------------------------------------------------------------------------
# switch_mode=load_balance：时间到点也不切，差值到点才切
# ---------------------------------------------------------------------------

def test_load_balance_mode_time_triggers_do_not_switch():
    # 间隔到点 + 夜间窗口，小差值下都不切
    cfg = Config(switch_mode="load_balance", time_switch_mode="interval",
                 interval_hours=2, night_optimize_enabled=True)
    accts = [acc(1, 50), acc(2, 45)]
    d = [dom(1, current=1)]
    p1 = plan_reconcile(accts, d, cfg, noon(),
                        last_time_switch_at=noon() - dt.timedelta(hours=2))
    assert not [a for a in p1.actions if a.kind == "dns"]
    night = dt.datetime(2026, 11, 22, 1, 30, tzinfo=TZ)
    p2 = plan_reconcile(accts, d, cfg, night)
    assert not [a for a in p2.actions if a.kind == "dns"]


def test_load_balance_mode_gb_gap_switches():
    cfg = Config(switch_mode="load_balance", switch_diff_mode="gb",
                 switch_diff_gb=10)
    # 差 12 GB ≥ 10 GB：切；差 5 GB：不切
    plan = plan_reconcile([acc(1, 100), acc(2, 88)],
                          [dom(1, current=1)], cfg, noon())
    assert any(a.kind == "dns" and a.reason == "traffic_diff_gb"
               for a in plan.actions)
    plan2 = plan_reconcile([acc(1, 100), acc(2, 95)],
                           [dom(1, current=1)], cfg, noon())
    assert not [a for a in plan2.actions if a.kind == "dns"]


def test_load_balance_mode_percent_gap_switches():
    cfg = Config(switch_mode="load_balance")  # 默认 percent 10%
    # 60/200=30% vs 36/200=18%：差 12% ≥ 10% 切
    plan = plan_reconcile([acc(1, 60), acc(2, 36)],
                          [dom(1, current=1)], cfg, noon())
    assert any(a.kind == "dns" and a.reason == "traffic_diff"
               for a in plan.actions)
    # 50/200=25% vs 38/200=19%：差 6% < 10% 不切
    plan2 = plan_reconcile([acc(1, 50), acc(2, 38)],
                           [dom(1, current=1)], cfg, noon())
    assert not [a for a in plan2.actions if a.kind == "dns"]


# ---------------------------------------------------------------------------
# switch_mode=both：任一触发都切（旧行为）
# ---------------------------------------------------------------------------

def test_both_mode_either_trigger_switches():
    # 间隔到点（小差值）切
    cfg = Config(switch_mode="both", time_switch_mode="interval",
                 interval_hours=2, night_optimize_enabled=False)
    p1 = plan_reconcile([acc(1, 50), acc(2, 45)], [dom(1, current=1)],
                        cfg, noon(),
                        last_time_switch_at=noon() - dt.timedelta(hours=2))
    assert any(a.kind == "dns" and a.reason == "interval_switch"
               for a in p1.actions)
    # 差值到点（无时间触发）也切
    cfg2 = Config(switch_mode="both", switch_diff_mode="gb", switch_diff_gb=10,
                  time_switch_mode="off", night_optimize_enabled=False)
    p2 = plan_reconcile([acc(1, 100), acc(2, 88)], [dom(1, current=1)],
                        cfg2, noon())
    assert any(a.kind == "dns" and a.reason == "traffic_diff_gb"
               for a in p2.actions)


def test_threshold_eviction_applies_in_all_modes():
    # 阈值踢出不受切换模式影响：time / load_balance / both 都踢出 IP1
    for mode in ("time", "load_balance", "both"):
        cfg = Config(switch_mode=mode, exclusion_action="none")
        plan = plan_reconcile([acc(1, 192), acc(2, 20)],
                              [dom(1, current=1)], cfg, noon())
        assert 1 in plan.evicted_account_ids, mode
        assert plan.assignments == {1: 2}, mode
        assert any(a.kind == "dns" and a.reason == "threshold_eviction"
                   for a in plan.actions), mode
