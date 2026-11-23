"""流量更新后自动重新加入 DDNS（保证性要求）+ 阈值恢复默认拉起：

- 被踢出后只要流量一更新、用量回落到恢复线（阈值-2%）以下，就自动重新加入，
  同一轮调度就重新进入候选池、按用量最低优先参与分配（dry-run 也体现），
  不需要手动点恢复。
- 24 小时兜底：被踢出超 24 小时且用量低于阈值（哪怕没到恢复线）也自动恢复。
- 恢复时按 threshold_recovery_action 执行拉起（默认拉起）；踢出DDNS（三选一
  第 1 档）的实例本来就在跑，不调开机；keep_stopped 入池但保持停机。
- 流量未知时不恢复；恢复后 1 小时内不重复记恢复事件、不重复拉起。
"""
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import engine
from app.engine import AccountView, Config, DomainView, Plan, plan_reconcile

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


def _at(day, hh, mm):
    return dt.datetime(2026, 11, 22 + day, hh, mm, tzinfo=TZ)


def acc(i, used, status="Running"):
    return AccountView(id=i, name=f"A{i}", quota_gb=200, used_gb=used,
                       status=status, public_ip=f"203.0.113.{i}")


def dom(i, current=None, ip=""):
    return DomainView(id=i, name=f"www{i}.example.xyz", priority=i - 1,
                      current_account_id=current, current_ip=ip)


def _mk(tmpdb, name, used, status="Running", **kw):
    a = tmpdb.create_account({"name": name, "group_id": 1, **kw})
    tmpdb.update_account(a["id"], {"traffic_used_gb": used,
                                   "instance_status": status,
                                   "public_ip": f"203.0.113.{10 + a['id']}"})
    return tmpdb.get_account(a["id"])


class FakeAliyun:
    def __init__(self):
        self.starts = []
        self.stops = []

    def start_instance(self):
        self.starts.append(1)

    def stop_instance(self, stop_charging=False):
        self.stops.append(stop_charging)


# ---------------------------------------------------------------------------
# 引擎滞后：恢复线 = 阈值 - 2%
# ---------------------------------------------------------------------------

def test_hysteresis_between_line_and_threshold_stays_evicted():
    cfg = Config()  # 阈值 95%，恢复线 93%
    # 94%：没被踢出过 → 不踢出；之前被踢出过 → 仍视为踢出（未到恢复线）
    assert 1 not in plan_reconcile(
        [acc(1, 188)], [dom(1)], cfg, _at(0, 12, 0)).evicted_account_ids
    plan = plan_reconcile([acc(1, 188)], [dom(1)], cfg, _at(0, 12, 0),
                          previously_evicted_ids={1})
    assert 1 in plan.evicted_account_ids
    assert plan.assignments == {1: None}  # 仍无可分配，备用


def test_hysteresis_below_line_recovers():
    cfg = Config()
    plan = plan_reconcile([acc(1, 184)], [dom(1)], cfg, _at(0, 12, 0),
                          previously_evicted_ids={1})
    assert 1 not in plan.evicted_account_ids  # 92% < 93% 恢复线：恢复
    assert plan.assignments == {1: 1}


# ---------------------------------------------------------------------------
# 调度器恢复评估：_evaluate_threshold_recovery（纯计算）
# ---------------------------------------------------------------------------

def test_evaluate_recovery_below_line(tmpdb):
    from app import scheduler
    a = _mk(tmpdb, "IP1", 192.0)  # 96% 被踢出
    tmpdb.set_flag(f"evicted:{a['id']}", _at(0, 10, 0).isoformat())
    tmpdb.update_account(a["id"], {"traffic_used_gb": 100.0})  # 流量更新到 50%
    rec = scheduler._evaluate_threshold_recovery(
        tmpdb.get_settings(), tmpdb.list_accounts(), _at(0, 12, 0))
    assert [r["account"]["id"] for r in rec] == [a["id"]]
    assert rec[0]["fallback"] is False
    assert rec[0]["line"] == 93.0


def test_evaluate_no_recovery_between_line_and_threshold(tmpdb):
    from app import scheduler
    a = _mk(tmpdb, "IP1", 192.0)
    tmpdb.set_flag(f"evicted:{a['id']}", _at(0, 10, 0).isoformat())
    tmpdb.update_account(a["id"], {"traffic_used_gb": 188.0})  # 94%：恢复线与阈值之间
    rec = scheduler._evaluate_threshold_recovery(
        tmpdb.get_settings(), tmpdb.list_accounts(), _at(0, 12, 0))
    assert rec == []


def test_evaluate_fallback_after_24h_below_threshold(tmpdb):
    from app import scheduler
    a = _mk(tmpdb, "IP1", 192.0)
    # 被踢出超过 24 小时（旗标值=首次踢出时刻）
    tmpdb.set_flag(f"evicted:{a['id']}", _at(-1, 10, 0).isoformat())
    tmpdb.update_account(a["id"], {"traffic_used_gb": 188.0})  # 94%
    rec = scheduler._evaluate_threshold_recovery(
        tmpdb.get_settings(), tmpdb.list_accounts(), _at(0, 12, 0))
    assert len(rec) == 1 and rec[0]["fallback"] is True
    # 未满 24 小时：94% 不兜底
    tmpdb.set_flag(f"evicted:{a['id']}", _at(0, 10, 0).isoformat())
    rec = scheduler._evaluate_threshold_recovery(
        tmpdb.get_settings(), tmpdb.list_accounts(), _at(0, 12, 0))
    assert rec == []


def test_evaluate_unknown_traffic_no_recovery(tmpdb):
    from app import scheduler
    a = _mk(tmpdb, "IP1", 192.0)
    tmpdb.set_flag(f"evicted:{a['id']}", _at(0, 10, 0).isoformat())
    tmpdb.update_account(a["id"], {"traffic_used_gb": None})  # 本次查不到
    rec = scheduler._evaluate_threshold_recovery(
        tmpdb.get_settings(), tmpdb.list_accounts(), _at(0, 12, 0))
    assert rec == []


def test_evaluate_monthly_reset_recovers_all(tmpdb):
    from app import scheduler
    a1 = _mk(tmpdb, "IP1", 192.0)
    a2 = _mk(tmpdb, "IP2", 196.0)
    for a in (a1, a2):
        tmpdb.set_flag(f"evicted:{a['id']}", _at(0, 10, 0).isoformat())
        tmpdb.update_account(a["id"], {"traffic_used_gb": 0.0})  # 月度重置清零
    rec = scheduler._evaluate_threshold_recovery(
        tmpdb.get_settings(), tmpdb.list_accounts(), _at(0, 12, 0))
    assert {r["account"]["id"] for r in rec} == {a1["id"], a2["id"]}


# ---------------------------------------------------------------------------
# dry-run：恢复后同一轮重新参与分配
# ---------------------------------------------------------------------------

def test_dry_run_recovered_rejoins_same_round(tmpdb):
    from app import scheduler
    a1 = _mk(tmpdb, "IP1", 192.0)  # 曾 96% 被踢出
    a2 = _mk(tmpdb, "IP2", 40.0)
    d = tmpdb.create_domain({"name": "www.example.xyz", "group_id": 1})
    tmpdb.update_domain(d["id"], {"current_account_id": a2["id"],
                                  "current_ip": "203.0.113.12"})
    tmpdb.set_flag(f"evicted:{a1['id']}", _at(0, 10, 0).isoformat())
    # 流量更新：IP1 回落到 10 GB（5%），是全池最低
    tmpdb.update_account(a1["id"], {"traffic_used_gb": 10.0})
    res = scheduler.run_once(dry_run=True, refresh=False)
    assert any(r["account_id"] == a1["id"] for r in res["threshold_recovery"])
    # 同一轮 dry-run 就把它重新指回去，不等下一轮
    dom_row = tmpdb.list_domains()[0]
    assert res["assignments"][str(dom_row["id"])] == a1["id"]
    # dry-run 不持久化：旗标还在（真实轮次才清）
    assert tmpdb.get_flag(f"evicted:{a1['id']}") != ""


def test_dry_run_no_recovery_between_line_and_threshold(tmpdb):
    from app import scheduler
    a1 = _mk(tmpdb, "IP1", 192.0)
    a2 = _mk(tmpdb, "IP2", 40.0)
    d = tmpdb.create_domain({"name": "www.example.xyz", "group_id": 1})
    tmpdb.update_domain(d["id"], {"current_account_id": a2["id"],
                                  "current_ip": "203.0.113.12"})
    # 被踢出 1 小时前（未满 24 小时兜底）：旗标时刻必须相对当前时间取，
    # 不能写死 2026-11-22——run_once 用真实 now，写死日期过 24 小时后
    # 兜底恢复会误触发，本用例就不再是“恢复线与阈值之间不恢复”
    tmpdb.set_flag(f"evicted:{a1['id']}",
                   (dt.datetime.now(TZ) - dt.timedelta(hours=1)).isoformat())
    tmpdb.update_account(a1["id"], {"traffic_used_gb": 188.0})  # 94%
    res = scheduler.run_once(dry_run=True, refresh=False)
    assert res["threshold_recovery"] == []
    dom_row = tmpdb.list_domains()[0]
    assert res["assignments"][str(dom_row["id"])] == a2["id"]


# ---------------------------------------------------------------------------
# 恢复持久化：拉起 / keep_stopped / 1 小时防抖 / 定时关机窗口
# ---------------------------------------------------------------------------

def _persist(tmpdb, now, fake, plan=None):
    from app import scheduler
    accounts = tmpdb.list_accounts()
    rec = scheduler._evaluate_threshold_recovery(
        tmpdb.get_settings(), accounts, now)
    plan = plan if plan is not None else Plan(evicted_account_ids=[])
    return scheduler._persist_threshold_recovery(
        tmpdb.get_settings(), accounts, plan, rec, set(), now,
        cloud_factory=lambda a, s: fake), rec


def test_persist_stopped_recovers_and_starts_once(tmpdb):
    a = _mk(tmpdb, "IP1", 192.0, status="Stopped")
    tmpdb.set_flag(f"evicted:{a['id']}", _at(0, 10, 0).isoformat())
    tmpdb.update_account(a["id"], {"traffic_used_gb": 184.0})  # 92%
    fake = FakeAliyun()
    done, _ = _persist(tmpdb, _at(0, 12, 0), fake)
    assert fake.starts == [1]  # 调用开机一次
    assert any("阈值恢复，已拉起" in d for d in done)
    assert tmpdb.get_flag(f"evicted:{a['id']}") == ""  # 旗标已清
    evs = tmpdb.list_events(10)
    assert any(e["title"] == "流量更新，已自动重新加入 DDNS" for e in evs)
    assert any("阈值恢复，已拉起" in (e["detail"] or "") for e in evs)


def test_persist_running_recovers_without_start(tmpdb):
    # 默认踢出DDNS（三选一第 1 档）：实例本来就在跑，恢复时不调开机
    a = _mk(tmpdb, "IP1", 192.0, status="Running")
    tmpdb.set_flag(f"evicted:{a['id']}", _at(0, 10, 0).isoformat())
    tmpdb.update_account(a["id"], {"traffic_used_gb": 184.0})
    fake = FakeAliyun()
    done, _ = _persist(tmpdb, _at(0, 12, 0), fake)
    assert fake.starts == []
    assert any("阈值恢复，已入池" in d for d in done)
    assert tmpdb.get_flag(f"evicted:{a['id']}") == ""


def test_persist_keep_stopped_no_start(tmpdb):
    a = _mk(tmpdb, "IP1", 192.0, status="Stopped")
    # 分组级阈值恢复：恢复入池但保持停机
    tmpdb.update_group(1, {"recovery_action": "keep_stopped"})
    tmpdb.set_flag(f"evicted:{a['id']}", _at(0, 10, 0).isoformat())
    tmpdb.update_account(a["id"], {"traffic_used_gb": 184.0})
    fake = FakeAliyun()
    done, _ = _persist(tmpdb, _at(0, 12, 0), fake)
    assert fake.starts == []
    assert any("阈值恢复，已入池，保持停机" in d for d in done)
    assert any(e["title"] == "阈值恢复，已入池，保持停机"
               for e in tmpdb.list_events(10))


def test_persist_no_repeat_start_within_one_hour(tmpdb):
    a = _mk(tmpdb, "IP1", 192.0, status="Stopped")
    tmpdb.set_flag(f"evicted:{a['id']}", _at(0, 10, 0).isoformat())
    tmpdb.update_account(a["id"], {"traffic_used_gb": 184.0})
    fake = FakeAliyun()
    _persist(tmpdb, _at(0, 12, 0), fake)
    assert fake.starts == [1]
    # 30 分钟后又被踢出又恢复：1 小时内不再重复拉起、也不重复记恢复事件
    tmpdb.set_flag(f"evicted:{a['id']}", _at(0, 12, 10).isoformat())
    n_events_before = len([e for e in tmpdb.list_events(50)
                           if e["kind"] == "recovery"])
    done, _ = _persist(tmpdb, _at(0, 12, 30), fake)
    assert fake.starts == [1]
    n_events_after = len([e for e in tmpdb.list_events(50)
                          if e["kind"] == "recovery"])
    assert n_events_after == n_events_before


def test_persist_daily_off_window_skips_start(tmpdb):
    # 每日定时关机窗口内：恢复拉起不执行（定时关机优先于恢复拉起）
    a = _mk(tmpdb, "IP1", 192.0, status="Stopped",
            daily_schedule_enabled=1, daily_on_time="08:00",
            daily_off_time="20:00")
    tmpdb.set_flag(f"evicted:{a['id']}", _at(0, 10, 0).isoformat())
    tmpdb.update_account(a["id"], {"traffic_used_gb": 184.0})
    fake = FakeAliyun()
    done, _ = _persist(tmpdb, _at(0, 21, 0), fake)  # 21:00 在关机窗口内
    assert fake.starts == []
    assert any("定时关机窗口内暂不拉起" in d for d in done)
    # 定时开机窗口内（次日 12:00，已过 1 小时防抖）：恢复拉起正常执行
    tmpdb.set_flag(f"evicted:{a['id']}", _at(0, 10, 0).isoformat())
    done, _ = _persist(tmpdb, _at(1, 12, 0), fake)
    assert fake.starts == [1]


def test_status_dot_no_longer_gray_after_recovery(tmpdb):
    from app.api import _public_account
    a = _mk(tmpdb, "IP1", 192.0, status="Running")
    tmpdb.set_flag(f"evicted:{a['id']}", _at(0, 10, 0).isoformat())
    pub = _public_account(tmpdb.get_account(a["id"]))
    assert pub["status_dot"] == "gray" and pub["status_label"] == "已跳出"
    # 流量更新回落后恢复：旗标清掉，状态点变回在线/排队，不再是灰点
    tmpdb.update_account(a["id"], {"traffic_used_gb": 100.0})
    tmpdb.delete_flag(f"evicted:{a['id']}")
    pub = _public_account(tmpdb.get_account(a["id"]))
    assert pub["status_dot"] in ("green", "yellow")
    assert pub["status_label"] in ("在线", "排队中")
