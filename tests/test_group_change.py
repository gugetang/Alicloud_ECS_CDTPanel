"""分组动态：分组当前服务实例从 A 切到 B 时推一条，写明切换规则与组名。

- 只在真的 A→B 切换时发：流量差值 / 间隔 / 动态 / 夜间 / 固定时刻 /
  每日窗口切换与阈值踢出都发；首次分配（initial_assign）不发。
- 正文格式：【组名】A 已替换为 B（模式：原因），如
  【Premium】A1 已替换为 A2（负载均衡：流量差值）。
- dns_change / traffic_protection 的日志仍在（只记日志不推送），
  分组动态是额外的一条推送事件。
"""
import datetime as dt
from zoneinfo import ZoneInfo

from app.engine import (AccountView, Config, DomainView, GroupView,
                        plan_reconcile)

TZ = ZoneInfo("Asia/Shanghai")


def noon():
    return dt.datetime(2026, 11, 22, 12, 0, tzinfo=TZ)


def acc(i, used, status="Running"):
    return AccountView(id=i, name=f"A{i}", quota_gb=200, used_gb=used,
                       status=status, public_ip=f"203.0.113.{i}", group_id=2)


def dom(i, current=None):
    return DomainView(id=i, name=f"premium{i}.example.xyz", priority=i - 1,
                      current_account_id=current, current_ip="203.0.113.1",
                      group_id=2)


GROUP = GroupView(id=2, name="Premium", switch_mode="load_balance")


def _group_changes(plan):
    return [b for e, t, b in plan.notifications if e == "group_change"]


def test_traffic_diff_switch_pushes_group_change():
    cfg = Config()
    # 差值 80% - 10% = 70 >= 10 → 从 A1 切到 A2
    plan = plan_reconcile([acc(1, 160), acc(2, 20)], [dom(1, current=1)],
                          cfg, noon(), groups=[GROUP])
    assert any(a.kind == "dns" and a.reason == "traffic_diff"
               for a in plan.actions)
    gc = _group_changes(plan)
    assert len(gc) == 1
    assert gc[0] == "【Premium】A1 已替换为 A2（负载均衡：流量差值）"
    # dns_change 日志仍在（只记日志不推送）
    assert any(e == "dns_change" for e, _, _ in plan.notifications)


def test_threshold_eviction_pushes_group_change():
    cfg = Config(exclusion_action="stop_charging")
    # A1 190/200=95% 已达阈值，当前指向 A1；应切到 A2 并推一条分组动态
    plan = plan_reconcile([acc(1, 190), acc(2, 20)], [dom(1, current=1)],
                          cfg, noon(), groups=[GROUP])
    assert any(a.kind == "dns" and a.reason == "threshold_eviction"
               for a in plan.actions)
    gc = _group_changes(plan)
    assert len(gc) == 1
    assert gc[0] == "【Premium】A1 已替换为 A2（负载均衡：阈值踢出）"
    assert any(e == "traffic_protection" for e, _, _ in plan.notifications)


def test_initial_assign_does_not_push_group_change():
    cfg = Config()
    # 首次分配：没有当前服务实例，不是 A→B，不发分组动态
    plan = plan_reconcile([acc(1, 20)], [dom(1, current=None)],
                          cfg, noon(), groups=[GROUP])
    assert _group_changes(plan) == []


def test_no_switch_below_diff_pushes_nothing():
    cfg = Config()
    # 差值 25-20=2.5% < 10% → 不切，也不发分组动态
    plan = plan_reconcile([acc(1, 50), acc(2, 40)], [dom(1, current=1)],
                          cfg, noon(), groups=[GROUP])
    assert not [a for a in plan.actions if a.kind == "dns"]
    assert _group_changes(plan) == []
