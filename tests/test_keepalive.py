"""保活默认拉起全部、排除手动/阈值停机、流量刷新后重新加入。

规则：
- 每轮对所有符合条件的已停机实例都下发启动，不只限于当前服务/切换目标；
- stop_reason=manual（面板手动停机）/threshold（阈值动作停机）的在排除
  期内不拉起，只记跳过；无标记的停机（阿里云侧停机）默认拉起；
- 排除只到下一次 API 流量刷新为止：刷新显示用量回到恢复线（保护阈值-2%）
  以下时清除标记、重新加入默认拉起（_stop_exclusion_lifted，沿用现有恢复
  线）；用量仍在恢复线以上时继续排除；流量未知时不解除。
"""
import datetime as dt
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

os.environ["CDT_NO_SCHEDULER"] = "1"

from app import db as dbmod  # noqa: E402
from app import scheduler  # noqa: E402
from app.engine import AccountView, Config, DomainView, plan_reconcile  # noqa: E402
from app.main import create_app  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture()
def tmpdb(tmp_path):
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


def acc(i, used, status="Running", stop_reason="", **kw):
    return AccountView(id=i, name=f"A{i}", quota_gb=200, used_gb=used,
                       status=status, public_ip=f"203.0.113.{i}",
                       stop_reason=stop_reason, **kw)


def dom(i, current=None, ip="", gid=1):
    return DomainView(id=i, name=f"www{i}.example.xyz", priority=i - 1,
                      current_account_id=current, current_ip=ip, group_id=gid)


def noon():
    return dt.datetime(2026, 11, 22, 12, 0, tzinfo=TZ)


def _starts(plan, aid=None):
    return [a for a in plan.actions if a.kind == "start"
            and (aid is None or a.account_id == aid)]


# ---------------------------------------------------------------------------
# 引擎：默认拉起全部（备用停机实例也拉）
# ---------------------------------------------------------------------------

def test_keepalive_starts_unmarked_stopped_standby():
    # A1 停机待命（不是当前服务、不是切换目标）、无标记 → 保活拉起
    cfg = Config()
    plan = plan_reconcile(
        [acc(1, 100, status="Stopped"), acc(2, 20), acc(3, 60)],
        [dom(1, current=2, ip="203.0.113.2")], cfg, noon())
    starts = _starts(plan, 1)
    assert len(starts) == 1
    assert starts[0].reason == "keepalive"
    assert starts[0].domain_id is None  # 不为哪个域名拉起，是全量保活


def test_keepalive_skips_manual_stop():
    cfg = Config()
    plan = plan_reconcile(
        [acc(1, 100, status="Stopped", stop_reason="manual"),
         acc(2, 20), acc(3, 60)],
        [dom(1, current=2, ip="203.0.113.2")], cfg, noon())
    assert _starts(plan, 1) == []
    assert (1, "manual") in plan.keepalive_skipped


def test_keepalive_skips_threshold_stop():
    cfg = Config()
    plan = plan_reconcile(
        [acc(1, 100, status="Stopped", stop_reason="threshold"),
         acc(2, 20), acc(3, 60)],
        [dom(1, current=2, ip="203.0.113.2")], cfg, noon())
    assert _starts(plan, 1) == []
    assert (1, "threshold") in plan.keepalive_skipped


def test_keepalive_skips_manual_current_server():
    # 当前正在服务的实例手动停了：保活也不拉（排除期内）
    cfg = Config()
    plan = plan_reconcile(
        [acc(1, 20, status="Stopped", stop_reason="manual"), acc(2, 100)],
        [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    assert _starts(plan, 1) == []
    assert (1, "manual") in plan.keepalive_skipped


def test_keepalive_skips_manual_switch_target_and_no_dns_to_it():
    # 切换目标是手动停机：不拉起，也不把 DNS 切给一台停着的实例
    cfg = Config()
    plan = plan_reconcile(
        [acc(1, 20, status="Stopped", stop_reason="manual"), acc(2, 100)],
        [dom(1, current=2, ip="203.0.113.2")], cfg, noon())
    assert _starts(plan, 1) == []
    assert not [a for a in plan.actions if a.kind == "dns"]
    assert (1, "manual") in plan.keepalive_skipped


def test_keepalive_skips_out_of_daily_window():
    # 窗口外仍不拉（22:00，每日 08:00–20:00）
    cfg = Config()
    late = dt.datetime(2026, 11, 22, 22, 0, tzinfo=TZ)
    plan = plan_reconcile(
        [acc(1, 100, status="Stopped", daily_schedule_enabled=True,
             daily_on_time="08:00", daily_off_time="20:00"), acc(2, 20)],
        [dom(1, current=2, ip="203.0.113.2")], cfg, late)
    assert _starts(plan, 1) == []


def test_keepalive_skips_evicted():
    # 被踢出（190/200=95%）仍不拉，且不算“保活跳过”（不是排除标记）
    cfg = Config()
    plan = plan_reconcile(
        [acc(1, 190, status="Stopped"), acc(2, 20)],
        [dom(1, current=2, ip="203.0.113.2")], cfg, noon())
    assert _starts(plan, 1) == []
    assert plan.keepalive_skipped == []


def test_keepalive_skips_keep_stopped_recovery():
    # 阈值恢复动作=keep_stopped 时不拉
    cfg = Config()
    plan = plan_reconcile(
        [acc(1, 100, status="Stopped", recovery_action="keep_stopped"),
         acc(2, 20)],
        [dom(1, current=2, ip="203.0.113.2")], cfg, noon())
    assert _starts(plan, 1) == []


def test_keepalive_starts_schedule_marked_in_window():
    # 定时关机标记本身不排除：窗口内照常拉起（窗口才是定时关机的门控）
    cfg = Config()
    plan = plan_reconcile(
        [acc(1, 100, status="Stopped", stop_reason="schedule"),
         acc(2, 20)],
        [dom(1, current=2, ip="203.0.113.2")], cfg, noon())
    assert len(_starts(plan, 1)) == 1


# ---------------------------------------------------------------------------
# DB：停机原因标记
# ---------------------------------------------------------------------------

def test_stop_reason_roundtrip(tmpdb):
    a = tmpdb.create_account({"name": "K1", "group_id": 1})
    tmpdb.set_stop_reason(a["id"], "manual")
    row = tmpdb.get_account(a["id"])
    assert row["stop_reason"] == "manual"
    assert row["stopped_at"] > 0
    tmpdb.clear_stop_reason(a["id"])
    row = tmpdb.get_account(a["id"])
    assert row["stop_reason"] == ""
    assert row["stopped_at"] == 0
    with pytest.raises(ValueError):
        tmpdb.set_stop_reason(a["id"], "bogus")


# ---------------------------------------------------------------------------
# 调度器：流量刷新后重新加入（沿用恢复线 阈值-2%）
# ---------------------------------------------------------------------------

_SETTINGS = {"quota_gb": 200, "protection_threshold_pct": 95}


def _row(reason, quota=200, thr=None):
    return {"stop_reason": reason, "quota_gb": quota,
            "protection_threshold_pct": thr}


def test_exclusion_lifted_when_usage_within_threshold():
    # 200 配额，刷新后 20 GiB=10% < 恢复线 93% → 解除
    assert scheduler._stop_exclusion_lifted(_row("manual"), 20.0, _SETTINGS) != ""


def test_exclusion_kept_when_usage_over_threshold():
    # 190 GiB=95% ≥ 阈值 → 继续排除
    assert scheduler._stop_exclusion_lifted(_row("manual"), 190.0, _SETTINGS) == ""


def test_exclusion_kept_between_recovery_line_and_threshold():
    # 188 GiB=94%：在阈值内、但仍在恢复线（93%）以上 → 继续排除（防抖动，
    # 与现有阈值恢复逻辑同一条线，不另起一套）
    assert scheduler._stop_exclusion_lifted(_row("manual"), 188.0, _SETTINGS) == ""
    # 184 GiB=92% < 93% → 解除
    assert scheduler._stop_exclusion_lifted(_row("manual"), 184.0, _SETTINGS) != ""


def test_exclusion_lifted_for_threshold_stop_too():
    assert scheduler._stop_exclusion_lifted(_row("threshold"), 20.0, _SETTINGS) != ""
    assert scheduler._stop_exclusion_lifted(_row("threshold"), 190.0, _SETTINGS) == ""


def test_exclusion_not_lifted_for_schedule_or_unknown():
    # 定时关机标记不走刷新解除；流量未知时不解除
    assert scheduler._stop_exclusion_lifted(_row("schedule"), 20.0, _SETTINGS) == ""
    assert scheduler._stop_exclusion_lifted(_row("manual"), None, _SETTINGS) == ""
    assert scheduler._stop_exclusion_lifted(_row(""), 20.0, _SETTINGS) == ""


def _fake_aliyun(traffic, status):
    class Fake:
        def __init__(self, account, secret):
            pass

        def describe_instance(self):
            return {"status": status, "public_ip": "",
                    "instance_type": "ecs.t6-c1m1.large"}

        def get_traffic_gb(self):
            return traffic

        def get_billing(self, timezone="Asia/Shanghai", force_refresh=False):
            return None
    return Fake


def _mk_stopped(tmpdb, monkeypatch, traffic, status="Stopped"):
    monkeypatch.setattr(scheduler, "AliyunClient", _fake_aliyun(traffic, status))
    monkeypatch.setattr(scheduler, "decrypt_secret", lambda enc: "secret")
    a = tmpdb.create_account({
        "name": "K1", "group_id": 1, "access_key_id": "LTAI-x",
        "access_key_secret_enc": "enc", "instance_id": "i-k1",
        "quota_gb": 200})
    tmpdb.update_account(a["id"], {"traffic_used_gb": 100.0,
                                   "instance_status": "Stopped"})
    tmpdb.set_stop_reason(a["id"], "manual")
    return tmpdb.get_account(a["id"])


def test_refresh_lifts_manual_mark_when_usage_within_threshold(tmpdb, monkeypatch):
    a = _mk_stopped(tmpdb, monkeypatch, traffic=20.0)
    scheduler._refresh_one(a, tmpdb.get_settings())
    row = tmpdb.get_account(a["id"])
    assert row["stop_reason"] == ""
    events = [e for e in tmpdb.list_events(20) if e["kind"] == "keepalive_rejoin"]
    assert len(events) == 1
    assert "用量 10.00% 在阈值内" in events[0]["detail"]
    assert "重新加入保活拉起" in events[0]["detail"]


def test_refresh_keeps_manual_mark_when_usage_over_threshold(tmpdb, monkeypatch):
    a = _mk_stopped(tmpdb, monkeypatch, traffic=190.0)
    scheduler._refresh_one(a, tmpdb.get_settings())
    row = tmpdb.get_account(a["id"])
    assert row["stop_reason"] == "manual"
    assert not [e for e in tmpdb.list_events(20)
                if e["kind"] == "keepalive_rejoin"]


# ---------------------------------------------------------------------------
# API：手动停机记 manual，手动启动清标记
# ---------------------------------------------------------------------------

class _FakeActionAliyun:
    calls: list = []

    def __init__(self, account, secret):
        pass

    def describe_instance(self):
        return {"status": "Stopped", "public_ip": "",
                "instance_type": "ecs.t6-c1m1.large"}

    def get_traffic_gb(self):
        return 190.0  # 95%：刷新路径不解除标记，隔离“手动启动清标记”本身

    def get_billing(self, timezone="Asia/Shanghai", force_refresh=False):
        return None

    def start_instance(self):
        _FakeActionAliyun.calls.append("start")

    def reboot_instance(self):
        _FakeActionAliyun.calls.append("reboot")

    def stop_instance(self, stop_charging=False):
        _FakeActionAliyun.calls.append("stop")


def _mk_api_account(tmpdb, client):
    r = client.post("/api/accounts", json={
        "name": "K-API-1", "site": "global", "region": "cn-hongkong",
        "access_key_id": "LTAI-test", "access_key_secret": "secret-test",
        "instance_id": "i-kapi1", "quota_gb": 200})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_api_manual_stop_marks_and_manual_start_clears(tmpdb, monkeypatch):
    import app.aliyun as aliyun_mod
    monkeypatch.setattr(aliyun_mod, "AliyunClient", _FakeActionAliyun)
    monkeypatch.setattr(scheduler, "AliyunClient", _FakeActionAliyun)
    client = TestClient(create_app())
    aid = _mk_api_account(tmpdb, client)
    _FakeActionAliyun.calls.clear()
    r = client.post(f"/api/accounts/{aid}/stop")
    assert r.status_code == 200, r.text
    assert r.json()["stop_reason"] == "manual"
    assert tmpdb.get_account(aid)["stop_reason"] == "manual"
    r = client.post(f"/api/accounts/{aid}/start")
    assert r.status_code == 200, r.text
    assert r.json()["stop_reason"] == ""
    assert tmpdb.get_account(aid)["stop_reason"] == ""
    assert _FakeActionAliyun.calls == ["stop", "start"]
