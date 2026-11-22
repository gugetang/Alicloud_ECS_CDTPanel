"""本次优化的回归测试：批量 flag 读取、数据清理、并行刷新、regions POST。

只用 tmpdb 隔离库 + monkeypatch 假客户端，不碰真实阿里云接口。
"""
from pathlib import Path

import pytest


@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


def test_get_flags_batch(tmpdb):
    tmpdb.set_flag("night_done:1", "2026-11-22")
    tmpdb.set_flag("fixed_done:1", "2026-11-22")
    tmpdb.set_flag("other", "x")
    got = tmpdb.get_flags(["night_done:1", "fixed_done:1", "missing"])
    assert got == {"night_done:1": "2026-11-22", "fixed_done:1": "2026-11-22"}
    assert tmpdb.get_flags([]) == {}


def test_list_evicted_account_ids(tmpdb):
    tmpdb.set_flag("evicted:2", "2026-11-22T10:00:00")
    tmpdb.set_flag("evicted:5", "2026-11-22T10:00:00")
    tmpdb.set_flag("evicted:notanid", "x")
    tmpdb.set_flag("night_done:1", "2026-11-22")
    assert tmpdb.list_evicted_account_ids() == {2, 5}


def test_prune_old_data(tmpdb):
    import time
    now = int(time.time())
    old = now - 100 * 86400
    conn = tmpdb.connect()
    for i in range(3):
        conn.execute(
            "INSERT INTO events(kind, title, created_at) VALUES('info', ?, ?)",
            (f"old-{i}", old))
    for i in range(2):
        conn.execute(
            "INSERT INTO events(kind, title, created_at) VALUES('info', ?, ?)",
            (f"new-{i}", now))
    conn.execute(
        "INSERT INTO traffic_samples(account_id, used_gb, month, created_at)"
        " VALUES(1, 10.0, '2026-08', ?)", (old,))
    conn.execute(
        "INSERT INTO traffic_samples(account_id, used_gb, month, created_at)"
        " VALUES(1, 20.0, '2026-11', ?)", (now,))
    conn.commit()
    out = tmpdb.prune_old_data(now=now)
    assert out["events_deleted"] == 3
    assert out["traffic_samples_deleted"] == 1
    rows = conn.execute("SELECT title FROM events ORDER BY id").fetchall()
    assert [r["title"] for r in rows] == ["new-0", "new-1"]
    rows = conn.execute("SELECT used_gb FROM traffic_samples").fetchall()
    assert [r["used_gb"] for r in rows] == [20.0]


def test_prune_events_cap(tmpdb, monkeypatch):
    import time
    now = int(time.time())
    monkeypatch.setattr(tmpdb, "EVENTS_MAX_ROWS", 3)
    conn = tmpdb.connect()
    for i in range(5):
        conn.execute(
            "INSERT INTO events(kind, title, created_at) VALUES('info', ?, ?)",
            (f"e{i}", now))
    conn.commit()
    out = tmpdb.prune_old_data(now=now)
    assert out["events_deleted"] == 2
    rows = conn.execute("SELECT title FROM events ORDER BY id").fetchall()
    assert [r["title"] for r in rows] == ["e2", "e3", "e4"]


def test_public_account_batched_matches_default(tmpdb):
    from app.api import _public_account
    row = tmpdb.create_account({"name": "A1"})
    tmpdb.update_account(row["id"], {"traffic_used_gb": 37.77,
                                     "instance_status": "Running"})
    settings = tmpdb.get_settings()
    evicted = tmpdb.list_evicted_account_ids()
    a = _public_account(tmpdb.get_account(row["id"]))
    b = _public_account(tmpdb.get_account(row["id"]), settings=settings,
                        evicted_ids=evicted)
    for k in ("usage_rate", "evicted", "status_dot", "status_label",
              "exclusion_action_effective", "protection_threshold_pct"):
        assert a[k] == b[k], k
    # 已踢出账户：批量传入的 evicted_ids 同样生效
    tmpdb.set_flag(f"evicted:{row['id']}", "2026-11-22T10:00:00")
    tmpdb.update_account(row["id"], {"traffic_used_gb": 190.0})
    evicted = tmpdb.list_evicted_account_ids()
    c = _public_account(tmpdb.get_account(row["id"]), settings=settings,
                        evicted_ids=evicted)
    assert c["evicted"] is True
    assert c["status_dot"] == "gray"


def test_refresh_cloud_status_parallel(tmpdb, monkeypatch):
    import app.scheduler as sched
    seen = []
    for name in ("A1", "A2", "A3"):
        tmpdb.create_account({"name": name})
    accounts = tmpdb.list_accounts()
    assert len(accounts) == 3

    def fake_refresh(a, settings):
        seen.append(a["id"])

    monkeypatch.setattr(sched, "_refresh_one", fake_refresh)
    out = sched.refresh_cloud_status(tmpdb.get_settings())
    assert sorted(seen) == sorted(a["id"] for a in accounts)
    assert len(out) == 3


def test_get_billing_sends_billing_cycle(monkeypatch):
    """DescribeInstanceBill 必须带 BillingCycle（YYYY-MM），否则 400
    MissingBillingCycle，费用永远读不到（2026-11-22 线上日志证实）。"""
    import json
    from app.aliyun import AliyunClient
    c = object.__new__(AliyunClient)
    c.account = {"access_key_id": "LTAI5t-demo-0001", "region": "cn-hongkong",
                 "site": "global", "instance_id": "i-demo000000000000001"}
    c.secret = "fake-secret"
    c.region = "cn-hongkong"
    calls = []

    def fake_common(domain, version, action, region, params=None):
        # 记调用顺序：旧写法只记最后一次调用，断言会误判为余额调用。
        calls.append({"domain": domain, "action": action, "params": params})
        if action == "DescribeInstanceBill":
            return json.dumps({"Data": {"BillingCycle": "2026-11", "Items": {"Item": [
                {"PretaxAmount": 1.5}, {"PretaxAmount": 2.25}]}}})
        return json.dumps({"Data": {"AvailableAmount": 10.0, "Currency": "USD"}})

    monkeypatch.setattr(c, "_common", fake_common)
    out = c.get_billing()
    assert calls[0]["action"] == "DescribeInstanceBill"
    assert calls[0]["params"] and calls[0]["params"].get("BillingCycle")
    assert len(calls[0]["params"]["BillingCycle"]) == 7  # YYYY-MM
    assert out["cost"] == 3.75
    assert out["currency"] == "USD"
    assert out["balance"] == 10.0


class _FakeAcs:
    """假的 AcsClient：do_action_with_exception 直接返回给定 JSON。"""

    def __init__(self, payload):
        self.payload = payload

    def do_action_with_exception(self, req):
        import json
        return json.dumps(self.payload).encode()


class _DummyDescribeInstancesRequest:
    """SDK 未安装时 describe_instance 仍需一个请求类（仅 set_InstanceIds）。"""

    def set_InstanceIds(self, ids):
        self.ids = ids


def _describe_client(monkeypatch, instance):
    """绕过 __init__ 的 SDK 检查，造一个 describe_instance 用的空壳客户端。"""
    import app.aliyun as alimod
    from app.aliyun import AliyunClient
    c = object.__new__(AliyunClient)
    c.account = {"access_key_id": "LTAI5t-demo-0001", "region": "cn-hongkong",
                 "site": "global", "instance_id": "i-demo000000000000001"}
    c.secret = "fake-secret"
    c.region = "cn-hongkong"
    payload = {"Instances": {"Instance": [instance]}}
    monkeypatch.setattr(c, "_client", lambda *a, **k: _FakeAcs(payload))
    # 本机无阿里云 SDK 时模块里根本没有 DescribeInstancesRequest，
    # raising=False 让它在两种环境下都能补上。
    monkeypatch.setattr(alimod, "DescribeInstancesRequest",
                        _DummyDescribeInstancesRequest, raising=False)
    return c


def test_describe_instance_prefers_eip(monkeypatch):
    """公网 IP 来自弹性 IP 时，DescribeInstances 把 IP 放在 EipAddress 里，
    PublicIpAddress 是空的——必须先读 EipAddress（参考项目做法），
    否则两台实例的 IP 永远读不到（2026-11-22 线上证实）。"""
    c = _describe_client(monkeypatch, {
        "InstanceId": "i-demo000000000000001", "Status": "Running",
        "EipAddress": {"IpAddress": "47.242.211.195"},
        "PublicIpAddress": {"IpAddress": []}})
    out = c.describe_instance()
    assert out["public_ip"] == "47.242.211.195"
    assert out["status"] == "Running"


def test_describe_instance_falls_back_to_public_ip(monkeypatch):
    """没绑 EIP 时退回 PublicIpAddress[0]。"""
    c = _describe_client(monkeypatch, {
        "InstanceId": "i-demo000000000000001", "Status": "Running",
        "EipAddress": {"IpAddress": ""},
        "PublicIpAddress": {"IpAddress": ["8.210.104.131"]}})
    assert c.describe_instance()["public_ip"] == "8.210.104.131"


def test_describe_instance_no_ip(monkeypatch):
    """两个字段都空时 public_ip 为空串，不编造。"""
    c = _describe_client(monkeypatch, {
        "InstanceId": "i-demo000000000000001", "Status": "Running",
        "EipAddress": {"IpAddress": ""},
        "PublicIpAddress": {"IpAddress": []}})
    assert c.describe_instance()["public_ip"] == ""


def _billing_client(site):
    from app.aliyun import AliyunClient
    c = object.__new__(AliyunClient)
    c.account = {"access_key_id": "LTAI5t-demo-0001", "region": "cn-hongkong",
                 "site": site, "instance_id": "i-demo000000000000001"}
    c.secret = "fake-secret"
    c.region = "cn-hongkong"
    return c


def test_get_billing_falls_back_to_overview(monkeypatch):
    """DescribeInstanceBill 在两站端点都失败时，回退 QueryBillOverview
    再查一次（参考项目做法），费用不直接归零。"""
    import json
    c = _billing_client("global")
    calls = []

    def fake_common(domain, version, action, region, params=None):
        calls.append((domain, action))
        if action == "DescribeInstanceBill":
            raise RuntimeError("400 MissingBillingCycle")
        if action == "QueryBillOverview":
            return json.dumps({"Data": {"BillingCycle": "2026-11",
                                        "Items": {"Item": [
                                            {"PretaxAmount": 2.0},
                                            {"PretaxAmount": 1.0}]}}})
        return json.dumps({"Data": {"AvailableAmount": 5.0, "Currency": "USD"}})

    monkeypatch.setattr(c, "_common", fake_common)
    out = c.get_billing()
    assert out["cost"] == 3.0
    assert out["balance"] == 5.0
    assert ("business.ap-southeast-1.aliyuncs.com",
            "QueryBillOverview") in calls


def test_get_billing_domain_order_by_site(monkeypatch):
    """BSS 端点按站点排序：国际站先 ap-southeast-1，国内站先.aliyuncs.com。"""
    import json

    def run(site):
        c = _billing_client(site)
        first = []

        def fake_common(domain, version, action, region, params=None):
            if action == "DescribeInstanceBill" and not first:
                first.append(domain)
            if action == "DescribeInstanceBill":
                return json.dumps({"Data": {"BillingCycle": "2026-11",
                                            "Items": {"Item": [
                                                {"PretaxAmount": 1.0}]}}})
            return json.dumps({"Data": {"AvailableAmount": 1.0,
                                        "Currency": "USD"}})

        monkeypatch.setattr(c, "_common", fake_common)
        out = c.get_billing()
        return first[0], out["currency"]

    domain, currency = run("global")
    assert domain == "business.ap-southeast-1.aliyuncs.com"
    assert currency == "USD"
    domain, currency = run("cn")
    assert domain == "business.aliyuncs.com"
    assert currency == "CNY"


def test_get_billing_items_list_shape(monkeypatch):
    """账单条目兼容 Items 直接是列表的形状（参考项目 ecs_bot 的写法）。"""
    import json
    c = _billing_client("global")

    def fake_common(domain, version, action, region, params=None):
        if action == "DescribeInstanceBill":
            return json.dumps({"Data": {"BillingCycle": "2026-11", "Items": [
                {"PretaxAmount": 1.0}, {"PretaxAmount": 0.5}]}})
        return json.dumps({"Data": {"AvailableAmount": 1.0, "Currency": "USD"}})

    monkeypatch.setattr(c, "_common", fake_common)
    assert c.get_billing()["cost"] == 1.5


def test_regions_post_not_get(tmpdb):
    from fastapi.testclient import TestClient
    from app.main import create_app
    client = TestClient(create_app())
    # POST + JSON body：无凭据时应报 400（路由存在、body 正常解析）
    r = client.post("/api/aliyun/regions", json={"site": "global"})
    assert r.status_code == 400
    assert "请先选择账户" in r.json()["detail"]
    # 旧的 GET 已关闭：密钥不再能走 URL 查询参数。注意这里是 404 不是 405——
    # main.py 的 SPA 兜底路由 @app.get("/{path:path}") 会接住所有 /api/* 的
    # GET 并返回 404“接口不存在”，所以 GET 关掉后表现为 404。
    r = client.get("/api/aliyun/regions?site=global")
    assert r.status_code == 404


def test_notify_proxy_default_is_empty():
    from app.notify import Notifier
    from app.settings import DEFAULT_SETTINGS
    # 默认不走代理：旧默认值 http://127.0.0.1:7070 会让没有跑该代理的
    # 部署上 Telegram 通知全部失败（代理不存在，连接被拒）。
    assert DEFAULT_SETTINGS["notify_proxy"] == ""
    assert Notifier(DEFAULT_SETTINGS)._proxies() == {}


def test_refresh_zero_means_off_and_accounts_follow_global():
    from app.scheduler import _eff_refresh_minutes, _refresh_minutes
    # 0=不刷新，相当于关闭调度。
    assert _refresh_minutes({"api_refresh_minutes": "0"}) == 0
    assert _refresh_minutes({"api_refresh_minutes": "5"}) == 5
    # 实例级刷新时间已取消：即使账户存了自定义值，也强制跟随全局。
    assert _eff_refresh_minutes(
        {"api_refresh_minutes": "5"}, {"api_refresh_minutes": "10"}) == 10
    assert _eff_refresh_minutes(
        {"api_refresh_minutes": "5"}, {"api_refresh_minutes": "0"}) == 0
    assert _eff_refresh_minutes({}, {"api_refresh_minutes": "10"}) == 10


def test_validate_settings_allows_zero_refresh():
    from app.api import _validate_settings
    # 0=不刷新，不应报错；正常分钟数也不应报错。
    _validate_settings({"api_refresh_minutes": "0"})
    _validate_settings({"api_refresh_minutes": "5"})
    _validate_settings({"check_interval_minutes": "0"})
    with pytest.raises(Exception):
        _validate_settings({"api_refresh_minutes": "-1"})
    with pytest.raises(Exception):
        _validate_settings({"api_refresh_minutes": "1441"})
    with pytest.raises(Exception):
        _validate_settings({"api_refresh_minutes": "abc"})


def test_put_settings_accepts_zero_refresh(tmpdb):
    from fastapi.testclient import TestClient
    from app.main import create_app
    client = TestClient(create_app())
    # 选“不刷新”应能保存为 0（旧校验只允许 1–1440，会 400）。
    r = client.put("/api/settings", json={"api_refresh_minutes": "0"})
    assert r.status_code == 200
    assert r.json()["api_refresh_minutes"] == "0"
    # 同一项同步：check_interval_minutes 也应为 0。
    assert r.json()["check_interval_minutes"] == "0"
    assert tmpdb.get_settings()["api_refresh_minutes"] == "0"


def test_status_paused_when_refresh_zero(tmpdb):
    from fastapi.testclient import TestClient
    from app.main import create_app
    client = TestClient(create_app())
    # 刷新时间 0=不刷新：即使 scheduler_paused=0，状态也应显示已暂停。
    tmpdb.set_settings({"api_refresh_minutes": "0", "scheduler_paused": "0"})
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["scheduler"]["paused"] is True
    assert r.json()["scheduler"]["api_refresh_minutes"] == 0
    # 选分钟数=开始调度：状态恢复为运行中。
    tmpdb.set_settings({"api_refresh_minutes": "5", "scheduler_paused": "0"})
    r = client.get("/api/status")
    assert r.json()["scheduler"]["paused"] is False
    assert r.json()["scheduler"]["api_refresh_minutes"] == 5


def test_to_views_resolves_threshold_from_group(tmpdb):
    """实例的阈值动作/恢复取所属分组的值，不再取实例自身的字段。"""
    from app.scheduler import _to_views
    tmpdb.update_group(1, {"exclusion_action": "stop_charging",
                           "recovery_action": "keep_stopped"})
    a = tmpdb.create_account({"name": "IP1", "group_id": 1})
    b = tmpdb.create_account({"name": "IP2", "group_id": 1})
    views = _to_views(tmpdb.list_accounts(), tmpdb.get_settings())
    by_id = {v.id: v for v in views}
    assert by_id[a["id"]].exclusion_action == "stop_charging"
    assert by_id[a["id"]].recovery_action == "keep_stopped"
    assert by_id[b["id"]].exclusion_action == "stop_charging"
    # 分组改回跟随全局：视图里为空，引擎再按全局走。
    tmpdb.update_group(1, {"exclusion_action": "", "recovery_action": ""})
    views = _to_views(tmpdb.list_accounts(), tmpdb.get_settings())
    by_id = {v.id: v for v in views}
    assert by_id[a["id"]].exclusion_action in (None, "")
    assert by_id[a["id"]].recovery_action in (None, "")


def test_group_threshold_drives_effective_values(tmpdb):
    """公开账户行的有效阈值动作/恢复跟随所属分组；分组为空时跟随全局。"""
    from app.api import _public_account
    row = tmpdb.create_account({"name": "IP1", "group_id": 1})
    # 分组自定义：有效值跟分组走（全局默认 none/start）
    tmpdb.update_group(1, {"exclusion_action": "stop",
                           "recovery_action": "keep_stopped"})
    body = _public_account(tmpdb.get_account(row["id"]))
    assert body["exclusion_action_effective"] == "stop"
    assert body["recovery_action_effective"] == "keep_stopped"
    # 分组改回跟随全局：有效值跟全局走
    tmpdb.update_group(1, {"exclusion_action": "", "recovery_action": ""})
    tmpdb.set_settings({"exclusion_action": "stop_charging",
                        "threshold_recovery_action": "keep_stopped"})
    body = _public_account(tmpdb.get_account(row["id"]))
    assert body["exclusion_action_effective"] == "stop_charging"
    assert body["recovery_action_effective"] == "keep_stopped"
