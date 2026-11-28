"""负载均衡模式：选/移成员后立即对比一次。

- 实例加入或移出分组（PUT /api/accounts/{id} 改 group_ids / group_id）
  后，5 秒防抖到期调度线程立即重算分配并同步 DNS（只用库里已有数据，
  不等 API 刷新），动态模式本轮视同间隔到点，池内有效 IP 立即按各自
  规则生效。
- 只在成员真的变了时触发：同值重保存、改名等不触发。
"""
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.engine import AccountView, Config, DomainView, GroupView, plan_reconcile
from app.main import create_app

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


class _FakeDns:
    def __init__(self, settings=None):
        pass

    def set_record(self, domain, ip):
        _FakeDns.calls.append((domain.name, ip))
        return True


class _FakeCloud:
    def __init__(self, settings=None):
        pass

    def start(self, account):
        pass

    def stop(self, account, stop_charging=False):
        pass


def _setup_pool(tmpdb, switch_mode="load_balance"):
    """分组 + 域名：当前指向 A1（已用 160/200），A2 未入池。"""
    g = tmpdb.create_group({"name": "Premium", "switch_mode": switch_mode})
    a1 = tmpdb.create_account({"name": "A1", "group_id": g["id"]})
    tmpdb.update_account(a1["id"], {"traffic_used_gb": 160.0,
                                    "instance_status": "Running",
                                    "public_ip": "203.0.113.1"})
    a2 = tmpdb.create_account({"name": "A2"})
    tmpdb.update_account(a2["id"], {"traffic_used_gb": 20.0,
                                    "instance_status": "Running",
                                    "public_ip": "203.0.113.2"})
    d = tmpdb.create_domain({"name": "premium.example.xyz",
                             "group_id": g["id"]})
    tmpdb.update_domain(d["id"], {"current_account_id": a1["id"],
                                  "current_ip": "203.0.113.1"})
    return g, a1, a2, d


def _request_recorder(monkeypatch):
    calls = []
    monkeypatch.setattr("app.scheduler.request_immediate_compare",
                        lambda: calls.append(1))
    return calls


def test_member_add_requests_immediate_compare(tmpdb, monkeypatch):
    from app import scheduler
    _FakeDns.calls = []
    g, a1, a2, d = _setup_pool(tmpdb)
    # A2 新建时默认进了默认分组（id=1），但不在本负载均衡分组里
    assert g["id"] not in tmpdb.get_account_group_ids(a2["id"])
    calls = _request_recorder(monkeypatch)
    monkeypatch.setattr("app.scheduler._CloudOps", _FakeCloud)
    monkeypatch.setattr("app.scheduler._DnsOps", _FakeDns)

    client = TestClient(create_app())
    r = client.put(f"/api/accounts/{a2['id']}",
                   json={"group_ids": [g["id"]]})
    assert r.status_code == 200, r.text
    assert tmpdb.get_account_group_ids(a2["id"]) == [g["id"]]
    # 成员变了：请求一次立即对比
    assert len(calls) == 1
    # 立即对比一次：A2 剩余流量更多（180G vs 40G），DNS 应切到 A2
    scheduler.run_once(refresh=False, quiet=True)
    assert ("premium.example.xyz", "203.0.113.2") in _FakeDns.calls
    assert tmpdb.get_domain(d["id"])["current_account_id"] == a2["id"]


def test_member_remove_requests_immediate_compare(tmpdb, monkeypatch):
    from app import scheduler
    _FakeDns.calls = []
    g, a1, a2, d = _setup_pool(tmpdb)
    # A2 也入池（已用 160/200），当前仍指向 A1（已用 160/200 → A1 先入池）
    tmpdb.update_account(a1["id"], {"traffic_used_gb": 20.0})
    tmpdb.update_account(a2["id"], {"traffic_used_gb": 160.0})
    client = TestClient(create_app())
    assert client.put(f"/api/accounts/{a2['id']}",
                      json={"group_ids": [g["id"]]}).status_code == 200
    calls = _request_recorder(monkeypatch)
    monkeypatch.setattr("app.scheduler._CloudOps", _FakeCloud)
    monkeypatch.setattr("app.scheduler._DnsOps", _FakeDns)

    # 把当前服务实例 A1 移出分组
    r = client.put(f"/api/accounts/{a1['id']}", json={"group_ids": []})
    assert r.status_code == 200, r.text
    assert tmpdb.get_account_group_ids(a1["id"]) == []
    assert len(calls) == 1
    # 立即对比一次：池里只剩 A2，DNS 应切到 A2
    scheduler.run_once(refresh=False, quiet=True)
    assert ("premium.example.xyz", "203.0.113.2") in _FakeDns.calls
    assert tmpdb.get_domain(d["id"])["current_account_id"] == a2["id"]


def test_no_membership_change_does_not_request(tmpdb, monkeypatch):
    g, a1, a2, d = _setup_pool(tmpdb)
    calls = _request_recorder(monkeypatch)
    client = TestClient(create_app())
    # 同值重保存成员：不触发
    assert client.put(f"/api/accounts/{a1['id']}",
                      json={"group_ids": [g["id"]]}).status_code == 200
    assert len(calls) == 0
    # 改名：不触发
    assert client.put(f"/api/accounts/{a1['id']}",
                      json={"name": "A1-new"}).status_code == 200
    assert len(calls) == 0
    # 主分组改成同组：成员不变，不触发
    assert client.put(f"/api/accounts/{a1['id']}",
                      json={"group_id": g["id"]}).status_code == 200
    assert len(calls) == 0
    # 主分组改到另一组：成员多了新组，触发一次
    g2 = tmpdb.create_group({"name": "Second", "switch_mode": "load_balance"})
    assert client.put(f"/api/accounts/{a1['id']}",
                      json={"group_id": g2["id"]}).status_code == 200
    assert len(calls) == 1


def _dynamic_plan(force_dynamic):
    """动态模式：锚点就是现在（间隔 30 分钟未到），当前指向 A1。"""
    now = dt.datetime(2026, 11, 22, 12, 0, tzinfo=TZ)
    group = GroupView(id=2, name="Premium", switch_mode="dynamic")
    accs = [AccountView(id=1, name="A1", quota_gb=200, used_gb=160,
                        status="Running", public_ip="203.0.113.1",
                        group_id=2, group_ids=[2]),
            AccountView(id=2, name="A2", quota_gb=200, used_gb=20,
                        status="Running", public_ip="203.0.113.2",
                        group_id=2, group_ids=[2])]
    doms = [DomainView(id=1, name="premium.example.xyz", group_id=2,
                       current_account_id=1, current_ip="203.0.113.1")]
    return plan_reconcile(accs, doms, Config(), now, groups=[group],
                          last_switch_by_group={2: now},
                          force_dynamic=force_dynamic)


def test_dynamic_mode_without_force_waits_for_interval():
    # 间隔未到：不切，等设定时间到才切（用户反馈的旧行为）
    plan = _dynamic_plan(force_dynamic=False)
    assert not any(a.kind == "dns" for a in plan.actions)


def test_dynamic_mode_force_compares_immediately():
    # 成员变更触发的立即对比：视同间隔到点，直接切到剩余流量最多的 A2
    plan = _dynamic_plan(force_dynamic=True)
    assert any(a.kind == "dns" and a.reason == "dynamic_switch"
               and a.account_id == 2 for a in plan.actions)
    assert any(e == "group_change" and "A1 已替换为 A2" in b
               for e, _, b in plan.notifications)


def test_dynamic_run_once_without_force_does_not_switch(tmpdb, monkeypatch):
    """动态分组、间隔未到：不 force 的一轮不切 DNS。
    锚点显式设为现在（间隔 30 分钟肯定未到），不依赖运行时刻。"""
    from app import scheduler
    _FakeDns.calls = []
    g, a1, a2, d = _setup_pool(tmpdb, switch_mode="dynamic")
    tmpdb.set_account_groups(a2["id"], [g["id"]])
    tmpdb.set_flag(f"last_time_switch:{g['id']}", scheduler._now().isoformat())
    monkeypatch.setattr("app.scheduler._CloudOps", _FakeCloud)
    monkeypatch.setattr("app.scheduler._DnsOps", _FakeDns)

    scheduler.run_once(refresh=False, quiet=True)
    assert ("premium.example.xyz", "203.0.113.2") not in _FakeDns.calls


def test_dynamic_run_once_force_switches_without_api_refresh(tmpdb, monkeypatch):
    """调度器集成：动态分组、间隔未到、库里已有数据时，force 的一轮
    直接切到 A2（不等 API 刷新、不等动态间隔）。"""
    from app import scheduler
    _FakeDns.calls = []
    g, a1, a2, d = _setup_pool(tmpdb, switch_mode="dynamic")
    # A2 入池（直接改库，不走 API，避免触发真实的 5 秒防抖定时器）
    tmpdb.set_account_groups(a2["id"], [g["id"]])
    # 锚点设为现在：间隔肯定未到，证明 force 是绕过间隔直接切的
    tmpdb.set_flag(f"last_time_switch:{g['id']}", scheduler._now().isoformat())
    monkeypatch.setattr("app.scheduler._CloudOps", _FakeCloud)
    monkeypatch.setattr("app.scheduler._DnsOps", _FakeDns)

    scheduler.run_once(refresh=False, quiet=True, force_dynamic=True)
    assert ("premium.example.xyz", "203.0.113.2") in _FakeDns.calls
    assert tmpdb.get_domain(d["id"])["current_account_id"] == a2["id"]


def test_immediate_compare_debounces_rapid_changes(monkeypatch):
    """5 秒防抖：连续两次请求只保留后一次的定时器，到期只唤醒一次。"""
    from app import scheduler
    created = []

    class FakeTimer:
        def __init__(self, delay, fn):
            self.delay = delay
            self.fn = fn
            self.daemon = False
            self.cancelled = False
            created.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr("threading.Timer", FakeTimer)
    wakes = []
    monkeypatch.setattr("app.scheduler.wake", lambda: wakes.append(1))

    scheduler.request_immediate_compare()
    scheduler.request_immediate_compare()
    assert len(created) == 2
    assert created[0].delay == 5
    assert created[0].cancelled is True  # 第一次的定时器被第二次取消
    assert created[1].cancelled is False
    assert wakes == []  # 5 秒未到，什么都没触发
    created[1].fn()  # 5 秒后，第二次的定时器到期
    assert len(wakes) == 1
    assert scheduler._take_immediate_compare() is True
    assert scheduler._take_immediate_compare() is False  # 已消费
    # 清掉模块里的定时器引用，避免影响其它测试
    scheduler._immediate_compare_timer = None
