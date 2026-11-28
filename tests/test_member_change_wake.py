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


def test_execute_failure_does_not_block_dns_switch(tmpdb, monkeypatch):
    """保活拉起失败（如实例已过期）不卡住 DNS 切换，且失败只记一次事件。

    线上问题（2026-11-29）：保活拉起已过期实例抛 InstanceExpired，
    execute_plan 无 try/except，整轮以“调度异常”收场，DNS 切换从未执行，
    域名一直指向已移出分组的旧实例。
    """
    from app import scheduler
    _FakeDns.calls = []
    g, a1, a2, d = _setup_pool(tmpdb)
    # A3：已停机、无停机原因，保活每轮都会尝试拉起
    a3 = tmpdb.create_account({"name": "A3"})
    tmpdb.update_account(a3["id"], {"traffic_used_gb": 50.0,
                                    "instance_status": "Stopped",
                                    "public_ip": "203.0.113.3"})
    # A2 入池且更优（20G vs 160G），DNS 应切到 A2
    client = TestClient(create_app())
    assert client.put(f"/api/accounts/{a2['id']}",
                      json={"group_ids": [g["id"]]}).status_code == 200

    class _FailingCloud(_FakeCloud):
        def start(self, account):
            raise RuntimeError(
                "HTTP Status: 403 Error:InstanceExpired The prePaid instance "
                "has been expired. RequestID: 01A222EC")

    monkeypatch.setattr("app.scheduler._CloudOps", _FailingCloud)
    monkeypatch.setattr("app.scheduler._DnsOps", _FakeDns)
    scheduler.run_once(refresh=False, quiet=True)
    # DNS 切换照常执行（不被失败的拉起卡住）
    assert ("premium.example.xyz", "203.0.113.2") in _FakeDns.calls
    assert tmpdb.get_domain(d["id"])["current_account_id"] == a2["id"]
    # 失败记一条 error 事件
    evs = [e for e in tmpdb.list_events() if e.get("title") == "执行失败"]
    assert len(evs) == 1
    assert "A3" in evs[0]["detail"] and "InstanceExpired" in evs[0]["detail"]
    # 再跑一轮：同一失败不重复记事件
    scheduler.run_once(refresh=False, quiet=True)
    evs = [e for e in tmpdb.list_events() if e.get("title") == "执行失败"]
    assert len(evs) == 1
    # 拉起恢复成功后清标记：下次再失败可再记
    monkeypatch.setattr("app.scheduler._CloudOps", _FakeCloud)
    scheduler.run_once(refresh=False, quiet=True)
    assert not tmpdb.get_flag(f"action_failed_logged:start:{a3['id']}")
    monkeypatch.setattr("app.scheduler._CloudOps", _FailingCloud)
    scheduler.run_once(refresh=False, quiet=True)
    evs = [e for e in tmpdb.list_events() if e.get("title") == "执行失败"]
    assert len(evs) == 2


# ---------------------------------------------------------------------------
# 触发面扩展：改运行模式 / 切换模式 / 动态间隔、实例停机 / 拉起后也立即对比
# ---------------------------------------------------------------------------

def test_group_mode_change_requests_immediate_compare(tmpdb, monkeypatch):
    from app import scheduler
    g, a1, a2, d = _setup_pool(tmpdb)
    calls = _request_recorder(monkeypatch)
    client = TestClient(create_app())
    # 运行模式 load_balance → dynamic：触发一次
    r = client.put(f"/api/groups/{g['id']}", json={"switch_mode": "dynamic"})
    assert r.status_code == 200, r.text
    assert len(calls) == 1


def test_group_rename_does_not_request(tmpdb, monkeypatch):
    g, a1, a2, d = _setup_pool(tmpdb)
    calls = _request_recorder(monkeypatch)
    client = TestClient(create_app())
    # 只改名：不触发
    r = client.put(f"/api/groups/{g['id']}", json={"name": "Premium-new"})
    assert r.status_code == 200, r.text
    assert len(calls) == 0


def test_group_diff_mode_and_interval_change_requests(tmpdb, monkeypatch):
    g, a1, a2, d = _setup_pool(tmpdb)
    calls = _request_recorder(monkeypatch)
    client = TestClient(create_app())
    # 切换模式 percent → gb：触发一次
    r = client.put(f"/api/groups/{g['id']}", json={"switch_diff_mode": "gb"})
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    # 动态切换间隔 30 → 60：再触发一次
    r = client.put(f"/api/groups/{g['id']}",
                   json={"dynamic_interval_minutes": 60})
    assert r.status_code == 200, r.text
    assert len(calls) == 2


def test_global_settings_mode_change_requests(tmpdb, monkeypatch):
    g, a1, a2, d = _setup_pool(tmpdb)
    calls = _request_recorder(monkeypatch)
    client = TestClient(create_app())
    # 全局运行模式改了：触发一次
    r = client.put("/api/settings", json={"switch_mode": "time"})
    assert r.status_code == 200, r.text
    assert len(calls) == 1


def test_global_settings_other_change_does_not_request(tmpdb, monkeypatch):
    g, a1, a2, d = _setup_pool(tmpdb)
    calls = _request_recorder(monkeypatch)
    client = TestClient(create_app())
    # 改一个与切换规则无关的设置：不触发
    r = client.put("/api/settings", json={"api_refresh_minutes": "2"})
    assert r.status_code == 200, r.text
    assert len(calls) == 0


class _FakeAliyunForPower:
    """开关机测试用假阿里云：start/stop 成功，刷新返回 Running。"""

    def __init__(self, account, secret):
        self.account = account

    def describe_instance(self):
        return {"status": "Running", "public_ip": "203.0.113.10",
                "instance_type": "ecs.t6-c1m1.large"}

    def get_traffic_gb(self):
        return 42.0

    def get_billing(self, timezone="Asia/Shanghai", force_refresh=False):
        return None

    def start_instance(self):
        pass

    def stop_instance(self, stop_charging=False):
        pass

    def reboot_instance(self):
        pass


def _power_account(tmpdb, client, name, group_id=None):
    """经 API 建一个带 AK 的账户（可开关机），可选加入分组。
    分组关系直接写库（不走 PUT /api/accounts，避免成员变更触发立即对比，
    干扰开关机触发的计数）。"""
    a = client.post("/api/accounts", json={
        "name": name, "site": "global", "region": "cn-hongkong",
        "access_key_id": "LTAI-test", "access_key_secret": "secret-test",
        "instance_id": f"i-{name.lower()}", "quota_gb": 200}).json()
    if group_id is not None:
        tmpdb.set_account_groups(a["id"], [group_id])
    return a


def _patch_aliyun(monkeypatch):
    monkeypatch.setattr("app.aliyun.AliyunClient", _FakeAliyunForPower)
    monkeypatch.setattr("app.scheduler.AliyunClient", _FakeAliyunForPower)


def test_start_member_requests_immediate_compare(tmpdb, monkeypatch):
    g, a1, a2, d = _setup_pool(tmpdb)
    calls = _request_recorder(monkeypatch)
    _patch_aliyun(monkeypatch)
    client = TestClient(create_app())
    member = _power_account(tmpdb, client, "P-Member", group_id=g["id"])
    r = client.post(f"/api/accounts/{member['id']}/start")
    assert r.status_code == 200, r.text
    assert len(calls) == 1


def test_start_non_member_does_not_request(tmpdb, monkeypatch):
    g, a1, a2, d = _setup_pool(tmpdb)
    calls = _request_recorder(monkeypatch)
    _patch_aliyun(monkeypatch)
    client = TestClient(create_app())
    # 不在任何分组里：拉起也不判断。新建账户默认进了默认分组，先清掉，
    # 还原成“不在选中实例内”的状态。
    outsider = _power_account(tmpdb, client, "P-Outsider")
    tmpdb.set_account_groups(outsider["id"], [])
    r = client.post(f"/api/accounts/{outsider['id']}/start")
    assert r.status_code == 200, r.text
    assert len(calls) == 0


def test_stop_member_requests_immediate_compare(tmpdb, monkeypatch):
    g, a1, a2, d = _setup_pool(tmpdb)
    calls = _request_recorder(monkeypatch)
    _patch_aliyun(monkeypatch)
    client = TestClient(create_app())
    member = _power_account(tmpdb, client, "P-Member2", group_id=g["id"])
    r = client.post(f"/api/accounts/{member['id']}/stop",
                    json={"stop_charging": False})
    assert r.status_code == 200, r.text
    assert len(calls) == 1


def test_stop_non_member_does_not_request(tmpdb, monkeypatch):
    g, a1, a2, d = _setup_pool(tmpdb)
    calls = _request_recorder(monkeypatch)
    _patch_aliyun(monkeypatch)
    client = TestClient(create_app())
    # 不在任何分组里：停机也不判断。新建账户默认进了默认分组，先清掉。
    outsider = _power_account(tmpdb, client, "P-Outsider2")
    tmpdb.set_account_groups(outsider["id"], [])
    r = client.post(f"/api/accounts/{outsider['id']}/stop",
                    json={"stop_charging": False})
    assert r.status_code == 200, r.text
    assert len(calls) == 0
