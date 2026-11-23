"""已批准优化（optimize/safe-fixes-20261124）的新行为测试：
P0-01 调度器暂停/恢复竞态、P1-01 BSS 账单缓存、P1-02 DDNS 缓存
Zone/Record ID、B-07 停机只看 stop_charging、B-11 切换时刻除法计算、
B-17 Telegram 429 重试一次。只测恢复预期行为的改动，不测被跳过的项。"""
import datetime as dt
import json
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.ddns import CloudflareClient
from app.engine import (AccountView, Action, Plan, _latest_scheduled_moment,
                        execute_plan)

TZ = ZoneInfo("Asia/Shanghai")


# ---------- P0-01：调度器 start/stop 竞态 ----------

def test_scheduler_stop_then_start_again(monkeypatch):
    """stop() 等线程退出后再 start()：旧死线程被回收，新线程能正常启动，
    不会“UI 显示运行、实际已永久静默”。"""
    import app.scheduler as sched
    calls = []

    def fake_loop():
        calls.append("loop")
        time.sleep(0.05)

    monkeypatch.setattr(sched, "_loop", fake_loop)
    try:
        sched.start()
        time.sleep(0.15)  # 等 fake_loop 跑完、线程自然死亡
        assert calls == ["loop"]
        sched.stop(timeout=5)
        sched.start()  # 死线程路径：join 回收后建新线程
        time.sleep(0.15)
        assert calls == ["loop", "loop"]
    finally:
        sched.stop(timeout=5)


def test_scheduler_stop_joins_running_thread(monkeypatch):
    """stop() 会等正在跑的轮次退出（带超时 join），而不是只置位就返回。"""
    import app.scheduler as sched
    started = []

    def slow_loop():
        started.append(True)
        time.sleep(0.2)

    monkeypatch.setattr(sched, "_loop", slow_loop)
    try:
        sched.start()
        time.sleep(0.05)
        t0 = time.monotonic()
        sched.stop(timeout=5)
        assert time.monotonic() - t0 >= 0.1  # 确实等了轮次跑完
        assert sched._thread is None or not sched._thread.is_alive()
    finally:
        sched.stop(timeout=5)


# ---------- P1-01：BSS 账单缓存 6 小时 ----------

def _billing_client(monkeypatch, calls, fail_first=0):
    from app.aliyun import AliyunClient
    c = object.__new__(AliyunClient)
    c.account = {"access_key_id": "LTAI5t-demo-0001", "region": "cn-hongkong",
                 "site": "global", "instance_id": "i-demo000000000000001"}
    c.secret = "fake-secret"
    c.region = "cn-hongkong"
    state = {"n": 0}

    def fake_common(domain, version, action, region, params=None):
        calls.append(action)
        # fail_first=N：前 N 次调用都失败（一次 get_billing 会调 2 个域名）
        if state["n"] < fail_first:
            state["n"] += 1
            raise RuntimeError("boom")
        if action == "DescribeInstanceBill":
            return json.dumps({"Data": {"BillingCycle": "2026-11",
                                        "Items": {"Item": [{"PretaxAmount": 1.5}]}}})
        return json.dumps({"Data": {"AvailableAmount": 10.0, "Currency": "USD"}})

    monkeypatch.setattr(c, "_common", fake_common)
    return c


def test_billing_cached_within_6h(monkeypatch):
    calls = []
    c = _billing_client(monkeypatch, calls)
    out1 = c.get_billing()
    out2 = c.get_billing()
    assert out1 == out2 and out1["cost"] == 1.5
    # 第二次命中缓存：不再调 BSS（1 次账单 + 1 次余额 = 2 次调用）
    assert len(calls) == 2
    out3 = c.get_billing(force_refresh=True)
    assert out3["cost"] == 1.5
    assert len(calls) == 4  # 强制刷新重新调


def test_billing_failure_not_cached(monkeypatch):
    calls = []
    c = _billing_client(monkeypatch, calls, fail_first=4)  # 2 动作 x 2 端点全失败
    assert c.get_billing() is None  # 第一次失败
    out = c.get_billing()  # 失败不缓存：重试应成功
    assert out is not None and out["cost"] == 1.5


def test_billing_timezone_param(monkeypatch):
    """B-08：周期按传入时区算，不再硬编码 Asia/Shanghai。"""
    import datetime as _dt
    calls = []
    c = _billing_client(monkeypatch, calls)
    out = c.get_billing(timezone="America/New_York")
    assert out["cycle"] == _dt.datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m")
    out_bad = c.get_billing(timezone="Not/AZone", force_refresh=True)
    assert out_bad["cycle"] == _dt.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m")


# ---------- P1-02：DDNS 缓存 Zone/Record ID ----------

class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _FakeSession:
    """Cloudflare 假会话：直接按 ID 取记录、按名字发现、PUT 回读都支持。"""
    def __init__(self, records):
        self.records = records  # name -> (record_id, content)
        self.gets = []
        self.puts = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.gets.append(url)
        if url.endswith("/zones"):
            return _FakeResp({"success": True, "result": [{"id": "zone1", "name": "example.xyz"}]})
        if "/dns_records/rec1" in url:
            name, content = next((n, c) for n, (_, c) in self.records.items())
            return _FakeResp({"success": True, "result": {"id": "rec1", "name": name, "content": content}})
        if "/dns_records/" in url:  # 未知的 record_id：按 ID 取不到
            return _FakeResp({"success": True, "result": None})
        name = (params or {}).get("name")
        hit = self.records.get(name)
        return _FakeResp({"success": True, "result": [{"id": hit[0], "name": name, "content": hit[1]}] if hit else []})

    def put(self, url, headers=None, json=None, timeout=None):
        self.puts.append(json)
        self.records[json["name"]] = ("rec1", json["content"])
        return _FakeResp({"success": True, "result": json})


def test_ddns_cached_record_skips_discovery():
    s = _FakeSession({"www.example.xyz": ("rec1", "203.0.113.10")})
    cf = CloudflareClient("tok", session=s)
    res = cf.sync("www.example.xyz", "example.xyz", "203.0.113.99",
                  cached={"zone_id": "zone1", "record_id": "rec1"})
    assert res.status == "synced" and res.changed is True
    assert not any(u.endswith("/zones") for u in s.gets)  # 没做 Zone 发现
    assert len(s.puts) == 1


def test_ddns_cached_record_miss_falls_back_to_discovery():
    s = _FakeSession({"www.example.xyz": ("rec1", "203.0.113.10")})
    cf = CloudflareClient("tok", session=s)
    res = cf.sync("www.example.xyz", "example.xyz", "203.0.113.99",
                  cached={"zone_id": "zone1", "record_id": "recX"})
    assert res.status == "synced" and res.changed is True
    assert any(u.endswith("/zones") for u in s.gets)  # 缓存失效时回退发现流程


# ---------- B-07：停机只看 stop_charging，不看 detail 文案 ----------

def test_stop_ignores_detail_text():
    class FakeCloud:
        def __init__(self):
            self.calls = []

        def start(self, account):
            self.calls.append(("start", account.id))

        def stop(self, account, stop_charging):
            self.calls.append(("stop", account.id, stop_charging))

    class FakeDns:
        def set_record(self, domain, ip):
            return True

    a = AccountView(id=1, name="A1", quota_gb=200, used_gb=10, status="Running",
                    public_ip="203.0.113.1")
    # detail 里写着“节省停机”，但 stop_charging=False：必须是普通停机
    plan = Plan(actions=[Action(kind="stop", account_id=1, stop_charging=False,
                                detail="节省停机")])
    cloud = FakeCloud()
    execute_plan(plan, [a], [], cloud, FakeDns())
    assert cloud.calls == [("stop", 1, False)]


# ---------- B-11：切换时刻用除法算，与逐分钟推进结果一致 ----------

def _brute(now, start, minutes):
    h, m, s = start
    base = now.replace(hour=h, minute=m, second=s, microsecond=0)
    if base > now:
        base -= dt.timedelta(days=1)
    step = dt.timedelta(minutes=minutes)
    while base + step <= now:
        base += step
    return base


def test_latest_scheduled_moment_matches_brute_force():
    now = dt.datetime(2026, 11, 24, 12, 0, tzinfo=TZ)
    # 锚点很久以前（今天 00:00）、间隔 1 分钟：旧循环要跑 720 次
    assert _latest_scheduled_moment(now, (0, 0, 0), 1) == _brute(now, (0, 0, 0), 1) == now
    assert _latest_scheduled_moment(now, (0, 0, 0), 60) == _brute(now, (0, 0, 0), 60) == now
    assert _latest_scheduled_moment(now, (6, 30, 0), 90) == _brute(now, (6, 30, 0), 90)
    early = dt.datetime(2026, 11, 24, 0, 0, 30, tzinfo=TZ)
    assert _latest_scheduled_moment(early, (0, 0, 0), 60) == dt.datetime(2026, 11, 24, 0, 0, tzinfo=TZ)


# ---------- B-17：Telegram 429 按 retry_after 重试一次 ----------

def test_telegram_429_retries_once(monkeypatch):
    from app.notify import Notifier
    calls = []
    sleeps = []
    monkeypatch.setattr("app.notify.time.sleep", lambda s: sleeps.append(s))

    class Resp:
        def __init__(self, status, payload=None):
            self.status_code = status
            self._payload = payload or {}

        def json(self):
            return self._payload

        @property
        def text(self):
            return json.dumps(self._payload)

    def fake_post(url, json=None, proxies=None, timeout=None):
        calls.append(url)
        if len(calls) == 1:
            return Resp(429, {"parameters": {"retry_after": 5}})
        return Resp(200, {"ok": True})

    monkeypatch.setattr("app.notify.requests.post", fake_post)
    n = Notifier({"telegram_enabled": "1", "telegram_token": "t",
                  "telegram_chat_id": "c", "notify_enabled": "1"})
    ok, detail = n._telegram("hi")
    assert ok is True and detail == ""
    assert len(calls) == 2  # 429 后重试了一次
    assert sleeps == [5]  # 按 Telegram 返回的 retry_after 退避


def test_telegram_429_retry_after_capped(monkeypatch):
    """retry_after 很大时上限 60 秒，不会等几个小时。"""
    from app.notify import Notifier
    sleeps = []
    monkeypatch.setattr("app.notify.time.sleep", lambda s: sleeps.append(s))

    class Resp:
        def __init__(self, status, payload=None):
            self.status_code = status
            self._payload = payload or {}

        def json(self):
            return self._payload

        @property
        def text(self):
            return json.dumps(self._payload)

    def fake_post(url, json=None, proxies=None, timeout=None):
        return Resp(429, {"parameters": {"retry_after": 3600}})

    monkeypatch.setattr("app.notify.requests.post", fake_post)
    n = Notifier({"telegram_enabled": "1", "telegram_token": "t",
                  "telegram_chat_id": "c", "notify_enabled": "1"})
    ok, detail = n._telegram("hi")
    assert ok is False
    assert sleeps == [60]
