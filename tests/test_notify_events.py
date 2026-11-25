"""新增推送事件（实例启动 / 实例停机 / 阈值报警）与带原因的日志：

- 三个新事件默认开启，可在通知设置页逐项关闭；关闭时不推送，
  但调度日志里仍记一条事件（开关只管推送，不管记日志）。
- 启动/停机在执行动作后推送一次，原因用 Action 已有的 reason/detail。
- 阈值报警固定 80% 警告线：首次跨越时推一次，恢复到 80% 以下后
  可再报；已到保护阈值的不发报警（走 traffic_protection）。
- 一次带动作的调度：日志里要有带原因的事件（启动/停机/踢出/恢复），
  “调度完成”事件的 detail 里要列出实际执行的动作，不再只有“动作 N 条”。
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


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or ""

    def json(self):
        return self._payload


def _settings(**over):
    s = {"telegram_enabled": "1", "telegram_token": "tok123",
         "telegram_chat_id": "123456789", "notify_proxy": "",
         "notify_instance_status": "1",
         "notify_threshold_alarm": "1",
         "notify_group_change": "1"}
    s.update(over)
    return s


def _fake_post(monkeypatch, posted):
    def fake_post(url, json=None, proxies=None, timeout=None):
        posted.append(url)
        return FakeResp(200, {"ok": True})
    monkeypatch.setattr("app.notify.requests.post", fake_post)


# ---------- 默认值与开关 ----------

def test_new_notify_defaults_on():
    from app.settings import DEFAULT_SETTINGS, EVENT_TOGGLE_KEYS, LOG_ONLY_EVENTS
    # 实例状态一个开关管四种事件；旧的分开关已删
    assert DEFAULT_SETTINGS["notify_instance_status"] == "1"
    assert DEFAULT_SETTINGS["notify_threshold_alarm"] == "1"
    assert DEFAULT_SETTINGS["notify_group_change"] == "1"
    assert "notify_instance_start" not in DEFAULT_SETTINGS
    assert "notify_instance_stop" not in DEFAULT_SETTINGS
    assert "notify_eip_replace" not in DEFAULT_SETTINGS
    for event in ("instance_start", "instance_stop", "instance_delete",
                  "eip_replace"):
        assert EVENT_TOGGLE_KEYS[event] == "notify_instance_status"
    assert EVENT_TOGGLE_KEYS["threshold_alarm"] == "notify_threshold_alarm"
    assert EVENT_TOGGLE_KEYS["group_change"] == "notify_group_change"
    # 六个只记日志的事件不在开关表里，也没有默认开关
    assert LOG_ONLY_EVENTS == {"traffic_protection", "dns_change", "fallback",
                               "exclusion", "error", "recovery"}
    for event in LOG_ONLY_EVENTS:
        assert event not in EVENT_TOGGLE_KEYS
        assert f"notify_{event}" not in DEFAULT_SETTINGS


def test_new_events_toggle_off_blocks_push(monkeypatch):
    posted = []
    _fake_post(monkeypatch, posted)
    for event in ("instance_start", "instance_stop", "instance_delete",
                  "eip_replace", "threshold_alarm", "group_change"):
        n = Notifier(_settings(notify_instance_status="0",
                               notify_threshold_alarm="0",
                               notify_group_change="0"))
        assert n.send(event, "t", "b") == []
        assert posted == []


def test_new_events_toggle_on_pushes(monkeypatch):
    posted = []
    _fake_post(monkeypatch, posted)
    for event in ("instance_start", "instance_stop", "instance_delete",
                  "eip_replace", "threshold_alarm", "group_change"):
        n = Notifier(_settings())
        assert n.send(event, "t", "b") == ["telegram"]
    assert len(posted) == 6


def test_log_only_events_never_push(monkeypatch):
    # 只记日志的六个事件：开关全开也不推送；但手动测试（force=True）仍可发
    posted = []
    _fake_post(monkeypatch, posted)
    for event in ("traffic_protection", "dns_change", "fallback",
                  "exclusion", "error", "recovery"):
        n = Notifier(_settings())
        assert n.send(event, "t", "b") == []
        assert n.send(event, "t", "b", force=True) == ["telegram"]
    assert len(posted) == 6


def test_get_notify_lists_new_events(tmpdb):
    from fastapi.testclient import TestClient
    from app.main import create_app
    client = TestClient(create_app())
    r = client.get("/api/notify")
    assert r.status_code == 200
    events = r.json()["events"]
    assert events["instance_status"] is True
    assert events["threshold_alarm"] is True
    assert events["group_change"] is True
    assert events["update_available"] is True
    assert "instance_start" not in events
    assert "instance_stop" not in events
    assert "eip_replace" not in events


def test_delete_account_notifies_instance_delete(tmpdb, monkeypatch):
    # 删除实例：记一条“删除实例”日志，并走实例状态开关推送一次
    from fastapi.testclient import TestClient
    from app.main import create_app
    posted = []
    _fake_post(monkeypatch, posted)
    tmpdb.set_settings({"telegram_enabled": "1", "telegram_token": "tok",
                        "telegram_chat_id": "1"})
    client = TestClient(create_app())
    a = tmpdb.create_account({"name": "DEL-1", "group_id": 1,
                              "access_key_id": "LTAI-x",
                              "access_key_secret_enc": "enc",
                              "instance_id": "i-del1", "quota_gb": 200})
    r = client.delete(f"/api/accounts/{a['id']}")
    assert r.status_code == 200, r.text
    assert tmpdb.get_account(a["id"]) is None
    assert any(e["kind"] == "info" and e["title"] == "删除实例"
               for e in tmpdb.list_events())
    assert len(posted) == 1, "实例状态开关开着，删除应推送一次"
    # 实例状态开关关了：只记日志，不推送
    b = tmpdb.create_account({"name": "DEL-2", "group_id": 1,
                              "access_key_id": "LTAI-x",
                              "access_key_secret_enc": "enc",
                              "instance_id": "i-del2", "quota_gb": 200})
    tmpdb.set_settings({"notify_instance_status": "0"})
    posted.clear()
    r = client.delete(f"/api/accounts/{b['id']}")
    assert r.status_code == 200, r.text
    assert any(e["kind"] == "info" and e["title"] == "删除实例"
               for e in tmpdb.list_events())
    assert posted == []


# ---------- 执行 start/stop 后推送 ----------

def acc(i, used, status="Running", ip=None):
    return AccountView(id=i, name=f"A{i}", quota_gb=200, used_gb=used,
                       status=status, public_ip=ip if ip is not None else f"203.0.113.{i}")


def dom(i, current=None, ip="", gid=1):
    return DomainView(id=i, name=f"www{i}.example.xyz", priority=i - 1,
                      current_account_id=current, current_ip=ip, group_id=gid)


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


def test_execute_plan_start_notifies_instance_start():
    cfg = Config()
    accounts = [acc(1, 20, status="Stopped"), acc(2, 100)]
    plan = plan_reconcile(accounts, [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    assert any(a.kind == "start" for a in plan.actions)
    got = []
    execute_plan(plan, accounts, [dom(1, current=1, ip="203.0.113.1")],
                 FakeCloud(), FakeDns(), notify_fn=lambda e, t, b: got.append((e, t, b)),
                 dry_run=False)
    starts = [g for g in got if g[0] == "instance_start"]
    assert len(starts) == 1
    assert "A1" in starts[0][2] and "保活拉起" in starts[0][2]
    assert "keepalive" not in starts[0][2]  # 推送原因写中文，不推英文原因码


def test_execute_plan_stop_notifies_instance_stop():
    cfg = Config(exclusion_action="stop_charging")
    accounts = [acc(1, 190, ip="203.0.113.1"), acc(2, 20)]
    plan = plan_reconcile(accounts, [dom(1, current=1, ip="203.0.113.1")], cfg, noon())
    assert any(a.kind == "stop" for a in plan.actions)
    got = []
    execute_plan(plan, accounts, [dom(1, current=1, ip="203.0.113.1")],
                 FakeCloud(), FakeDns(), notify_fn=lambda e, t, b: got.append((e, t, b)),
                 dry_run=False)
    stops = [g for g in got if g[0] == "instance_stop"]
    assert len(stops) == 1
    assert "A1" in stops[0][2] and "节省停机" in stops[0][2]


# ---------- 阈值报警：首次跨越 80% 只报一次，恢复后可再报 ----------

def _alarm_accounts(tmpdb, used):
    a = tmpdb.create_account({"name": "A1", "group_id": 1})
    tmpdb.update_account(a["id"], {"traffic_used_gb": used,
                                   "instance_status": "Running",
                                   "public_ip": "203.0.113.1"})
    return tmpdb.get_account(a["id"])


def test_threshold_alarm_first_crossing_only(tmpdb, monkeypatch):
    from app import scheduler
    posted = []
    _fake_post(monkeypatch, posted)
    settings = tmpdb.get_settings()
    settings.update(_settings())
    a = _alarm_accounts(tmpdb, 170.0)  # 85%，首次跨 80%
    scheduler._check_threshold_alarms(settings, [a], Notifier(settings))
    events = [e for e in tmpdb.list_events(100) if e["kind"] == "threshold_alarm"]
    assert len(events) == 1 and "85.00%" in events[0]["detail"] and "A1" in events[0]["detail"]
    assert len(posted) == 1
    # 同一轮再来一次：已报过，不重复
    scheduler._check_threshold_alarms(settings, [a], Notifier(settings))
    assert len([e for e in tmpdb.list_events(100) if e["kind"] == "threshold_alarm"]) == 1
    assert len(posted) == 1
    # 回落到 80% 以下：清旗标，可再报
    a = tmpdb.update_account(a["id"], {"traffic_used_gb": 100.0})
    scheduler._check_threshold_alarms(settings, [tmpdb.get_account(a["id"])], Notifier(settings))
    assert tmpdb.get_flag(f"threshold_alarm:{a['id']}") == ""
    a = tmpdb.update_account(a["id"], {"traffic_used_gb": 170.0})
    scheduler._check_threshold_alarms(settings, [tmpdb.get_account(a["id"])], Notifier(settings))
    assert len([e for e in tmpdb.list_events(100) if e["kind"] == "threshold_alarm"]) == 2
    assert len(posted) == 2


def test_threshold_alarm_not_above_protection_threshold(tmpdb, monkeypatch):
    from app import scheduler
    posted = []
    _fake_post(monkeypatch, posted)
    settings = tmpdb.get_settings()
    settings.update(_settings())
    a = _alarm_accounts(tmpdb, 190.0)  # 95%，已到保护阈值：不发报警
    scheduler._check_threshold_alarms(settings, [a], Notifier(settings))
    assert [e for e in tmpdb.list_events(100) if e["kind"] == "threshold_alarm"] == []
    assert posted == []
    # 从阈值回落到 85%：旗标已置，不补报；回落到 80% 以下后才可再报
    a = tmpdb.update_account(a["id"], {"traffic_used_gb": 170.0})
    scheduler._check_threshold_alarms(settings, [tmpdb.get_account(a["id"])], Notifier(settings))
    assert [e for e in tmpdb.list_events(100) if e["kind"] == "threshold_alarm"] == []


def test_threshold_alarm_toggle_off_no_push_but_logs(tmpdb, monkeypatch):
    from app import scheduler
    posted = []
    _fake_post(monkeypatch, posted)
    settings = tmpdb.get_settings()
    settings.update(_settings(notify_threshold_alarm="0"))
    a = _alarm_accounts(tmpdb, 170.0)
    scheduler._check_threshold_alarms(settings, [a], Notifier(settings))
    assert posted == []  # 开关关了不推送
    assert len([e for e in tmpdb.list_events(100) if e["kind"] == "threshold_alarm"]) == 1


# ---------- 一次带动作的调度：日志带原因 ----------

def test_run_once_with_actions_logs_reasons(tmpdb, monkeypatch):
    from app import scheduler

    # 用调度器自己的 _CloudOps/_DnsOps 接口做假实现，避免调真实阿里云
    class FakeOps:
        def __init__(self, settings=None):
            self.calls = []

        def start(self, account):
            self.calls.append(("start", account.id))

        def stop(self, account, stop_charging=False):
            self.calls.append(("stop", account.id, stop_charging))

        def set_record(self, domain, ip):
            self.calls.append((domain.name, ip))
            return True

    tmpdb.set_settings({"exclusion_action": "stop_charging"})
    a1 = tmpdb.create_account({"name": "A1", "group_id": 1})
    tmpdb.update_account(a1["id"], {"traffic_used_gb": 190.0,
                                    "instance_status": "Running",
                                    "public_ip": "203.0.113.1"})
    a2 = tmpdb.create_account({"name": "A2", "group_id": 1})
    tmpdb.update_account(a2["id"], {"traffic_used_gb": 20.0,
                                    "instance_status": "Running",
                                    "public_ip": "203.0.113.2"})
    d = tmpdb.create_domain({"name": "www1.example.xyz", "group_id": 1})
    tmpdb.update_domain(d["id"], {"current_account_id": a1["id"],
                                  "current_ip": "203.0.113.1"})
    monkeypatch.setattr("app.scheduler._CloudOps", FakeOps)
    monkeypatch.setattr("app.scheduler._DnsOps", FakeOps)
    res = scheduler.run_once(refresh=False, quiet=False)
    assert res["executed"]  # 确实执行了动作
    events = tmpdb.list_events(100)
    kinds = [e["kind"] for e in events]
    # 踢出（traffic_protection，带阈值原因）+ 停机（instance_stop，带节省停机）
    assert "traffic_protection" in kinds
    assert "instance_stop" in kinds
    stop_ev = next(e for e in events if e["kind"] == "instance_stop")
    assert "A1" in stop_ev["detail"] and "节省停机" in stop_ev["detail"]
    tp_ev = next(e for e in events if e["kind"] == "traffic_protection")
    assert "A1" in tp_ev["detail"] and "95" in tp_ev["detail"]
    # “调度完成”不再只有计数：detail 里列出实际执行的动作
    done = next(e for e in events if e["kind"] == "schedule" and e["title"] == "调度完成")
    assert "stop:A1" in done["detail"]


def test_run_once_start_logs_reason(tmpdb, monkeypatch):
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

    a1 = tmpdb.create_account({"name": "A1", "group_id": 1})
    tmpdb.update_account(a1["id"], {"traffic_used_gb": 20.0,
                                    "instance_status": "Stopped",
                                    "public_ip": "203.0.113.1"})
    a2 = tmpdb.create_account({"name": "A2", "group_id": 1})
    tmpdb.update_account(a2["id"], {"traffic_used_gb": 100.0,
                                    "instance_status": "Running",
                                    "public_ip": "203.0.113.2"})
    d = tmpdb.create_domain({"name": "www1.example.xyz", "group_id": 1})
    tmpdb.update_domain(d["id"], {"current_account_id": a1["id"],
                                  "current_ip": "203.0.113.1"})
    monkeypatch.setattr("app.scheduler._CloudOps", FakeOps)
    monkeypatch.setattr("app.scheduler._DnsOps", FakeOps)
    scheduler.run_once(refresh=False, quiet=False)
    events = tmpdb.list_events(100)
    assert any(e["kind"] == "instance_start" and "A1" in e["detail"]
               for e in events)


def test_threshold_recovery_logs_reason(tmpdb):
    from app import scheduler
    from app.engine import AccountView

    a = tmpdb.create_account({"name": "A1", "group_id": 1})
    tmpdb.update_account(a["id"], {"traffic_used_gb": 180.0,  # 90% < 恢复线 93%
                                   "instance_status": "Running",
                                   "public_ip": "203.0.113.1"})
    tmpdb.set_flag(f"evicted:{a['id']}", "2026-11-22T00:00:00+08:00")
    settings = tmpdb.get_settings()
    accounts = tmpdb.list_accounts()
    views = scheduler._to_views(accounts, settings)
    recovered = scheduler._evaluate_threshold_recovery(
        settings, accounts, dt.datetime(2026, 11, 23, 12, 0, tzinfo=TZ))
    assert recovered  # 确实评估为已恢复
    plan = plan_reconcile(views, [], Config(), dt.datetime(2026, 11, 23, 12, 0, tzinfo=TZ),
                          groups=[])
    done = scheduler._persist_threshold_recovery(
        settings, accounts, plan, recovered, set(),
        dt.datetime(2026, 11, 23, 12, 0, tzinfo=TZ))
    assert done
    events = tmpdb.list_events(100)
    rec = [e for e in events if e["kind"] == "recovery"]
    assert rec and "A1" in rec[0]["detail"] and "93" in rec[0]["detail"]
