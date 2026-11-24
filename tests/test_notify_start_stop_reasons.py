"""停机/拉起推送补齐：原因要写清楚。

审计清单（全部启动/停机路径）：
- 手动启动/停机（API）：已推，原因=手动操作。
- 保活拉起（当前服务/切换目标/全量备用）：execute_plan 推 instance_start，
  原因码原先是英文（keepalive / keepalive_before_switch），现推中文。
- 阈值踢出停机（exclusion=stop/stop_charging）：execute_plan 推
  instance_stop，但原先缺用量与阈值，现补上。
- 定时开机/关机：已推，原因=定时开机/定时关机。
- 阈值恢复拉起：已推，原因=阈值恢复（用量 < 恢复线）。
- 流量更新后重新加入（_refresh_one 清除 manual/threshold 标记）：
  原先本轮随即拉起的推送只写“保活拉起”，丢了重新加入的上下文，
  现 run_once 把该轮 start 动作原因改成 keepalive_rejoin。
- 分组暂停/调度暂停：只改 DNS/不调云上开关机，无实例启停推送，正确。
- 手动重启：只调 reboot，不启停实例，不推 instance_start/stop。
"""
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.engine import AccountView, Config, DomainView, execute_plan, plan_reconcile
from app.notify import Notifier

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


def acc(i, used, status="Running", **kw):
    return AccountView(id=i, name=f"A{i}", quota_gb=200, used_gb=used,
                       status=status, public_ip=f"203.0.113.{i}", **kw)


def dom(i, current=None, ip="", gid=1):
    return DomainView(id=i, name=f"www{i}.example.xyz", priority=i - 1,
                      current_account_id=current, current_ip=ip, group_id=gid)


def noon():
    return dt.datetime(2026, 11, 22, 12, 0, tzinfo=TZ)


class FakeCloud:
    def start(self, account):
        pass

    def stop(self, account, stop_charging=False):
        pass


class FakeDns:
    def set_record(self, domain, ip):
        return True


def _run(plan, accounts, domains):
    got = []
    execute_plan(plan, accounts, domains, FakeCloud(), FakeDns(),
                 notify_fn=lambda e, t, b: got.append((e, t, b)), dry_run=False)
    return got


# ---------- 阈值停机：推送带用量、阈值、停机方式 ----------

def test_threshold_stop_push_has_usage_threshold_and_mode():
    cfg = Config(exclusion_action="stop_charging")
    accounts = [acc(1, 190), acc(2, 20)]
    plan = plan_reconcile(accounts, [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    assert any(a.kind == "stop" for a in plan.actions)
    got = _run(plan, accounts, [dom(1, current=1, ip="203.0.113.1")])
    stops = [g for g in got if g[0] == "instance_stop"]
    assert len(stops) == 1
    body = stops[0][2]
    assert "A1" in body and "节省停机" in body
    assert "95.00%" in body and "190.00" in body and "阈值 95%" in body
    assert "原因：阈值踢出" in body
    assert "exclusion" not in body  # 不推英文原因码


def test_threshold_stop_push_ordinary_mode():
    cfg = Config(exclusion_action="stop")
    accounts = [acc(1, 190), acc(2, 20)]
    plan = plan_reconcile(accounts, [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    got = _run(plan, accounts, [dom(1, current=1, ip="203.0.113.1")])
    stops = [g for g in got if g[0] == "instance_stop"]
    assert len(stops) == 1
    assert "普通停机" in stops[0][2] and "95.00%" in stops[0][2]


# ---------- 保活拉起：推送原因写中文 ----------

def test_keepalive_start_push_reason_in_chinese():
    cfg = Config()
    accounts = [acc(1, 20, status="Stopped"), acc(2, 100)]
    plan = plan_reconcile(accounts, [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    got = _run(plan, accounts, [dom(1, current=1, ip="203.0.113.1")])
    starts = [g for g in got if g[0] == "instance_start"]
    assert len(starts) == 1
    assert "原因：保活拉起" in starts[0][2]
    assert "keepalive" not in starts[0][2]


def test_switch_target_start_push_reason_in_chinese():
    cfg = Config()
    # A1 停机、用量最低（切换目标），A2 在服务、用量高：切过去前先拉起 A1
    accounts = [acc(1, 20, status="Stopped"), acc(2, 100)]
    plan = plan_reconcile(accounts, [dom(1, current=2, ip="203.0.113.2")], cfg, noon())
    starts = [a for a in plan.actions if a.kind == "start" and a.account_id == 1]
    assert starts and starts[0].reason == "keepalive_before_switch"
    got = _run(plan, accounts, [dom(1, current=2, ip="203.0.113.2")])
    starts = [g for g in got if g[0] == "instance_start"]
    assert len(starts) == 1
    assert "原因：切换前保活拉起" in starts[0][2]


# ---------- 流量更新后重新加入 ----------

def test_keepalive_rejoined_helper():
    from app import scheduler
    settings = {"quota_gb": 200, "protection_threshold_pct": 95}
    before = [{"id": 1, "name": "A1", "stop_reason": "manual",
               "instance_status": "Stopped", "quota_gb": 200,
               "protection_threshold_pct": None}]
    after = [{"id": 1, "name": "A1", "stop_reason": "",
             "instance_status": "Stopped", "traffic_used_gb": 20.0}]
    got = scheduler._keepalive_rejoined(before, after, settings)
    assert got[1] == "用量 10.00% 在阈值内（恢复线 93%）"
    # 用量仍在阈值外：不算重新加入
    after[0]["traffic_used_gb"] = 190.0
    assert scheduler._keepalive_rejoined(before, after, settings) == {}
    # 刷新前已 Running（阿里云控制台手动拉起）：不算重新加入
    before[0]["instance_status"] = "Running"
    after[0]["traffic_used_gb"] = 20.0
    assert scheduler._keepalive_rejoined(before, after, settings) == {}


def test_run_once_rejoin_start_push_says_rejoin(tmpdb, monkeypatch):
    from app import scheduler

    class FakeOps:
        def __init__(self, settings=None):
            pass

        def start(self, account):
            pass

        def stop(self, account, stop_charging=False):
            pass

        def set_record(self, domain, ip):
            return True

    class FakeAliyun:
        def __init__(self, account, secret):
            pass

        def describe_instance(self):
            return {"status": "Stopped", "public_ip": "",
                    "instance_type": "ecs.t6-c1m1.large"}

        def get_traffic_gb(self):
            return 20.0  # 10%，在恢复线以下：刷新解除 manual 标记

        def get_billing(self, timezone="Asia/Shanghai", force_refresh=False):
            return None

    monkeypatch.setattr(scheduler, "AliyunClient", FakeAliyun)
    monkeypatch.setattr(scheduler, "decrypt_secret", lambda enc: "secret")
    monkeypatch.setattr("app.scheduler._CloudOps", FakeOps)
    monkeypatch.setattr("app.scheduler._DnsOps", FakeOps)
    a1 = tmpdb.create_account({"name": "A1", "group_id": 1,
                               "access_key_id": "LTAI-x",
                               "access_key_secret_enc": "enc",
                               "instance_id": "i-a1", "quota_gb": 200})
    tmpdb.update_account(a1["id"], {"traffic_used_gb": 100.0,
                                    "instance_status": "Stopped",
                                    "public_ip": "203.0.113.1",
                                    "last_seen_at": 0})
    tmpdb.set_stop_reason(a1["id"], "manual")
    a2 = tmpdb.create_account({"name": "A2", "group_id": 1})
    tmpdb.update_account(a2["id"], {"traffic_used_gb": 100.0,
                                    "instance_status": "Running",
                                    "public_ip": "203.0.113.2"})
    d = tmpdb.create_domain({"name": "www1.example.xyz", "group_id": 1})
    tmpdb.update_domain(d["id"], {"current_account_id": a2["id"],
                                  "current_ip": "203.0.113.2"})
    scheduler.run_once(refresh=True, quiet=True)
    events = tmpdb.list_events(100)
    starts = [e for e in events if e["kind"] == "instance_start"]
    assert starts, "保活拉起应推 instance_start"
    assert any("流量更新后重新加入" in e["detail"] and "A1" in e["detail"]
               for e in starts)
    assert any("用量 10.00% 在阈值内" in e["detail"] for e in starts)


# ---------- 定时关机：推送带原因 ----------

def test_daily_off_push_has_reason(tmpdb, monkeypatch):
    from app import scheduler

    class FakeResp:
        status_code = 200

        def json(self):
            return {"ok": True}

    posted = []

    def fake_post(url, json=None, proxies=None, timeout=None):
        posted.append(json)
        return FakeResp()

    monkeypatch.setattr("app.notify.requests.post", fake_post)
    tmpdb.set_settings({"telegram_enabled": "1", "telegram_token": "tok",
                        "telegram_chat_id": "1", "notify_instance_stop": "1"})
    a = tmpdb.create_account({"name": "A1", "group_id": 1,
                              "access_key_id": "LTAI-x",
                              "access_key_secret_enc": "enc",
                              "instance_id": "i-a1", "quota_gb": 200})
    tmpdb.update_account(a["id"], {"traffic_used_gb": 40.0,
                                   "instance_status": "Running",
                                   "public_ip": "203.0.113.1",
                                   "daily_schedule_enabled": 1,
                                   "daily_on_time": "08:00",
                                   "daily_off_time": "20:00"})

    class FakeAliyun:
        def __init__(self, account, secret):
            pass

        def start_instance(self):
            pass

        def stop_instance(self, stop_charging=False):
            pass

    now = dt.datetime(2026, 11, 22, 20, 0, tzinfo=TZ)
    scheduler._apply_daily_schedule(
        tmpdb.get_settings(), now,
        cloud_factory=lambda a, s: FakeAliyun(a, s))
    assert posted, "定时关机应推送"
    assert any("定时关机" in (p or {}).get("text", "") for p in posted)


# ---------- 开关关闭时不推 ----------

def test_instance_stop_toggle_off_no_push(monkeypatch):
    class FakeResp:
        status_code = 200

        def json(self):
            return {"ok": True}

    posted = []

    def fake_post(url, json=None, proxies=None, timeout=None):
        posted.append(url)
        return FakeResp()

    monkeypatch.setattr("app.notify.requests.post", fake_post)
    settings = {"telegram_enabled": "1", "telegram_token": "tok",
                "telegram_chat_id": "1", "notify_instance_stop": "0"}
    assert Notifier(settings).send("instance_stop", "实例停机",
                                   "A1 已停机，原因：手动操作") == []
    assert posted == []


def test_instance_start_toggle_off_no_push(monkeypatch):
    class FakeResp:
        status_code = 200

        def json(self):
            return {"ok": True}

    posted = []

    def fake_post(url, json=None, proxies=None, timeout=None):
        posted.append(url)
        return FakeResp()

    monkeypatch.setattr("app.notify.requests.post", fake_post)
    settings = {"telegram_enabled": "1", "telegram_token": "tok",
                "telegram_chat_id": "1", "notify_instance_start": "0"}
    assert Notifier(settings).send("instance_start", "实例启动",
                                   "A1 已启动，原因：保活拉起") == []
    assert posted == []
