"""实例配置（表单后端）+ 每日定时开关机 + 日报推送过滤：

- Secret 留空不清空、填了才更新。
- 每日定时开关机：开机时间触发开机、关机时间触发关机、非定时时间不触发、
  错过窗口补偿一次不重复；被阈值踢出的实例到点也不拉起。
- 日报推送过滤：follow/on/off × 全局开/关。
- 字段校验：时间 HH:MM、开机≠关机、额度>0、daily_report 取值。
"""
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

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


def _mk(tmpdb, name="IP1", used=40.0, status="Running", **kw):
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
# Secret：留空不清空、填了才更新
# ---------------------------------------------------------------------------

def test_secret_blank_keeps_and_filled_updates(tmpdb):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.security import decrypt_secret
    client = TestClient(create_app())
    r = client.post("/api/accounts", json={
        "name": "IP1", "group_id": 1, "instance_id": "i-demo000000000000001",
        "access_key_id": "LTAI5t-demo-0001", "access_key_secret": "first-secret"})
    assert r.status_code == 200
    aid = r.json()["id"]
    assert r.json()["has_secret"] is True
    assert decrypt_secret(tmpdb.get_account(aid)["access_key_secret_enc"]) == "first-secret"
    # 留空更新别名：Secret 不清空
    r = client.put(f"/api/accounts/{aid}", json={"name": "IP1-renamed"})
    assert r.status_code == 200
    assert decrypt_secret(tmpdb.get_account(aid)["access_key_secret_enc"]) == "first-secret"
    r = client.put(f"/api/accounts/{aid}", json={"access_key_secret": ""})
    assert r.status_code == 200
    assert decrypt_secret(tmpdb.get_account(aid)["access_key_secret_enc"]) == "first-secret"
    # 填了新值：才更新
    r = client.put(f"/api/accounts/{aid}",
                   json={"access_key_secret": "second-secret"})
    assert r.status_code == 200
    assert decrypt_secret(tmpdb.get_account(aid)["access_key_secret_enc"]) == "second-secret"


# ---------------------------------------------------------------------------
# 每日定时开关机
# ---------------------------------------------------------------------------

def _apply(tmpdb, now, fake):
    from app import scheduler
    return scheduler._apply_daily_schedule(
        tmpdb.get_settings(), now,
        cloud_factory=lambda a, s: fake)


def test_daily_schedule_on_at_on_time(tmpdb):
    a = _mk(tmpdb, used=40.0, status="Stopped",
            daily_schedule_enabled=1, daily_on_time="08:00",
            daily_off_time="20:00")
    fake = FakeAliyun()
    done = _apply(tmpdb, _at(0, 8, 0), fake)
    assert fake.starts == [1] and fake.stops == []
    assert any("定时开机" in d for d in done)
    # 同一天内再次检查：不重复执行
    done = _apply(tmpdb, _at(0, 8, 10), fake)
    assert fake.starts == [1]


def test_daily_schedule_off_at_off_time(tmpdb):
    a = _mk(tmpdb, used=40.0, status="Running",
            daily_schedule_enabled=1, daily_on_time="08:00",
            daily_off_time="20:00")
    fake = FakeAliyun()
    done = _apply(tmpdb, _at(0, 20, 0), fake)
    assert fake.stops == [False] and fake.starts == []  # 普通停机
    assert any("定时关机" in d for d in done)
    done = _apply(tmpdb, _at(0, 20, 10), fake)
    assert fake.stops == [False]


def test_daily_schedule_no_trigger_at_other_times(tmpdb):
    _mk(tmpdb, used=40.0, status="Stopped",
        daily_schedule_enabled=1, daily_on_time="08:00",
        daily_off_time="20:00")
    fake = FakeAliyun()
    assert _apply(tmpdb, _at(0, 12, 0), fake) == []
    assert fake.starts == [] and fake.stops == []


def test_daily_schedule_missed_window_compensates_once(tmpdb):
    # 进程重启/延迟：08:25 才检查（窗口 = max(30, API 刷新间隔 5)=30 分钟），补偿一次
    _mk(tmpdb, used=40.0, status="Stopped",
        daily_schedule_enabled=1, daily_on_time="08:00",
        daily_off_time="20:00")
    fake = FakeAliyun()
    done = _apply(tmpdb, _at(0, 8, 25), fake)
    assert fake.starts == [1]
    assert any("定时开机" in d for d in done)
    # 09:30 已过窗口：不再补偿
    assert _apply(tmpdb, _at(0, 9, 30), fake) == []
    assert fake.starts == [1]


def test_daily_schedule_evicted_not_started(tmpdb):
    # 阈值踢出优先于定时开机：已被踢出的实例到点也不拉起
    _mk(tmpdb, used=192.0, status="Stopped",
        daily_schedule_enabled=1, daily_on_time="08:00",
        daily_off_time="20:00")
    fake = FakeAliyun()
    done = _apply(tmpdb, _at(0, 8, 0), fake)
    assert fake.starts == []
    assert any("定时开机已跳过" in d for d in done)


def test_daily_schedule_disabled_noop(tmpdb):
    _mk(tmpdb, used=40.0, status="Stopped")  # 未启用定时开关机
    fake = FakeAliyun()
    assert _apply(tmpdb, _at(0, 8, 0), fake) == []
    assert fake.starts == []


def test_daily_schedule_cross_midnight(tmpdb):
    # 跨午夜窗口：20:00 开机、次日 08:00 关机
    _mk(tmpdb, used=40.0, status="Running",
        daily_schedule_enabled=1, daily_on_time="20:00",
        daily_off_time="08:00")
    fake = FakeAliyun()
    done = _apply(tmpdb, _at(1, 8, 0), fake)  # 次日 08:00 关机
    assert fake.stops == [False]
    assert any("定时关机" in d for d in done)


# ---------------------------------------------------------------------------
# 字段校验
# ---------------------------------------------------------------------------

def test_account_field_validation(tmpdb):
    a = _mk(tmpdb)
    with pytest.raises(ValueError, match="HH:MM"):
        tmpdb.update_account(a["id"], {"daily_schedule_enabled": 1,
                                       "daily_on_time": "8:00",
                                       "daily_off_time": "20:00"})
    with pytest.raises(ValueError, match="不能相同"):
        tmpdb.update_account(a["id"], {"daily_schedule_enabled": 1,
                                       "daily_on_time": "08:00",
                                       "daily_off_time": "08:00"})
    with pytest.raises(ValueError, match="大于 0"):
        tmpdb.update_account(a["id"], {"quota_gb": 0})
    with pytest.raises(ValueError, match="站点"):
        tmpdb.update_account(a["id"], {"site": "eu"})
    # 合法值通过
    row = tmpdb.update_account(a["id"], {
        "daily_schedule_enabled": 1, "daily_on_time": "08:00",
        "daily_off_time": "20:00", "quota_gb": 300})
    assert row["quota_gb"] == 300


# ---------------------------------------------------------------------------
# 日报推送过滤：实例级“参与日报推送”已取消，全部跟随全局开关
# ---------------------------------------------------------------------------

def test_daily_report_filter_combinations():
    from app.notify import filter_daily_report_accounts
    # 实例自身的 daily_report 字段不再读取（即使存了旧值也忽略）
    accts = [{"id": 1, "name": "A", "daily_report": "follow"},
             {"id": 2, "name": "B", "daily_report": "on"},
             {"id": 3, "name": "C", "daily_report": "off"},
             {"id": 4, "name": "D"}]  # 未填
    on = {"daily_report_enabled": "1"}
    off = {"daily_report_enabled": "0"}
    assert [a["id"] for a in filter_daily_report_accounts(accts, on)] == [1, 2, 3, 4]
    assert [a["id"] for a in filter_daily_report_accounts(accts, off)] == []
    # force（发送测试财报）：全局关也全部包含
    assert [a["id"] for a in filter_daily_report_accounts(accts, off, force=True)] == [1, 2, 3, 4]


def test_daily_report_follows_global_switch(tmpdb, monkeypatch):
    # 全局关了就不推；全局开了就推；实例级字段不再起作用
    from app import scheduler
    a = _mk(tmpdb, used=37.77)
    tmpdb.set_settings({"daily_report_enabled": "0"})
    calls = []

    class FakeNotifier:
        def __init__(self, settings):
            self.s = settings
            self.last_errors = {}

        def enabled(self, event):
            return True

        def send_daily_report(self, accounts, date_str, force=False,
                              skip_global_switch=False, channel=None):
            calls.append([x["id"] for x in accounts])
            return ["telegram"]

    monkeypatch.setattr(scheduler, "Notifier", FakeNotifier)
    s = tmpdb.get_settings()
    tmpdb.set_flag("daily_report_last_check", _at(-1, 23, 0).isoformat())
    assert scheduler._maybe_send_daily_report(s, _at(0, 0, 0)) is False
    assert calls == []
    # 全局开了就推（_maybe_send_daily_report 以传入的 settings 为准，
    # 改库后要重取，不能用开之前的旧字典）
    tmpdb.set_settings({"daily_report_enabled": "1"})
    s = tmpdb.get_settings()
    tmpdb.set_flag("daily_report_last_check", _at(-1, 23, 0).isoformat())
    assert scheduler._maybe_send_daily_report(s, _at(0, 0, 0)) is True
    assert calls == [[a["id"]]]
