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


def test_daily_report_account_field_ignored_and_follows_global(tmpdb):
    """实例级“参与日报推送”已取消：全部跟随全局开关；PUT /api/accounts 传
    daily_report 不再生效。"""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.notify import filter_daily_report_accounts
    client = TestClient(create_app())
    row = tmpdb.create_account({"name": "IP1", "group_id": 1})
    r = client.put(f"/api/accounts/{row['id']}", json={"daily_report": "off"})
    assert r.status_code == 200
    accts = tmpdb.list_accounts()
    assert filter_daily_report_accounts(accts, {"daily_report_enabled": "0"}) == []
    assert [a["id"] for a in filter_daily_report_accounts(
        accts, {"daily_report_enabled": "1"})] == [row["id"]]
    assert [a["id"] for a in filter_daily_report_accounts(
        accts, {"daily_report_enabled": "0"}, force=True)] == [row["id"]]


# ---- 轻量应用服务器（SWAS）支持 ----
# 轻量服务器不在 ECS 里（实例 ID 为 UUID，不是 i-…），ECS DescribeInstances
# 永远返回空；必须走 SWAS-OPEN ListInstances（地域端点 swas.<region>.aliyuncs.com）。

def _swas_client(monkeypatch, swas_instance=None, swas_error=None):
    """ECS 返回空、SWAS 由假 _common 接管的客户端。swas_instance 为 SWAS
    ListInstances 返回的单台实例 dict；swas_error 为 _common 要抛的异常。"""
    import json
    import app.aliyun as alimod
    from app.aliyun import AliyunClient
    c = object.__new__(AliyunClient)
    c.account = {"access_key_id": "LTAI5t-demo-0001", "region": "cn-hongkong",
                 "site": "global",
                 "instance_id": "3eeeb2fb03b743c78617168126bf6c6f"}
    c.secret = "fake-secret"
    c.region = "cn-hongkong"
    c._swas_denied = False
    empty = {"Instances": {"Instance": []}}
    monkeypatch.setattr(c, "_client", lambda *a, **k: _FakeAcs(empty))
    monkeypatch.setattr(alimod, "DescribeInstancesRequest",
                        _DummyDescribeInstancesRequest, raising=False)

    def fake_common(domain, version, action, region, params=None):
        if swas_error is not None:
            raise swas_error
        assert domain == "swas.cn-hongkong.aliyuncs.com", domain
        assert version == "2020-06-01", version
        assert action == "ListInstances", action
        assert params and params.get("RegionId") == "cn-hongkong"
        return json.dumps({"Instances": [swas_instance] if swas_instance else [],
                           "TotalCount": 1 if swas_instance else 0}).encode()
    monkeypatch.setattr(c, "_common", fake_common)
    return c


def test_swas_domain_is_region_specific():
    """SWAS 必须用地域端点：中心端点 swas.aliyuncs.com 不解析（线上证实）。"""
    from app.aliyun import _swas_domain
    assert _swas_domain("cn-hongkong") == "swas.cn-hongkong.aliyuncs.com"
    assert _swas_domain("cn-hangzhou") == "swas.cn-hangzhou.aliyuncs.com"


def test_describe_instance_falls_back_to_swas(monkeypatch):
    """ECS 查不到轻量服务器时走 SWAS：状态、公网 IP（字符串）、套餐名都读到。"""
    c = _swas_client(monkeypatch, {
        "InstanceId": "3eeeb2fb03b743c78617168126bf6c6f",
        "InstanceName": "alicloud--hk2", "Status": "Running",
        "PublicIpAddress": "47.242.211.200", "PlanName": "swas.s2.c2m2s40b40t",
        "RegionId": "cn-hongkong"})
    out = c.describe_instance()
    assert out["status"] == "Running"
    assert out["public_ip"] == "47.242.211.200"
    assert out["instance_type"] == "swas.s2.c2m2s40b40t"


def test_describe_instance_swas_denied_hint(monkeypatch):
    """ECS 空 + SWAS 报 403 + 实例 ID 是 UUID：报错要明说缺 SWAS 权限
    （kind=auth），而不是一句“查不到”让用户干瞪眼。"""
    from app.aliyun import AliyunError
    c = _swas_client(monkeypatch, swas_error=RuntimeError(
        "HTTP Status: 403 Error:NoPermission User is not authorized"))
    try:
        c.describe_instance()
    except AliyunError as exc:
        assert exc.kind == "auth"
        assert "AliyunSWASOpenFullAccess" in str(exc)
    else:
        raise AssertionError("应抛 AliyunError")


def test_describe_instance_swas_empty_still_not_found(monkeypatch):
    """ECS 和 SWAS 都查不到：仍报“查不到”，不编造。"""
    from app.aliyun import AliyunError
    c = _swas_client(monkeypatch, swas_instance=None)
    try:
        c.describe_instance()
    except AliyunError as exc:
        assert "查不到" in str(exc)
    else:
        raise AssertionError("应抛 AliyunError")


def test_start_instance_falls_back_to_swas_on_not_found(monkeypatch):
    """ECS 开机报实例不存在时改走 SWAS StartInstance（轻量服务器不在 ECS 里）。"""
    import app.aliyun as alimod
    from app.aliyun import AliyunClient
    c = object.__new__(AliyunClient)
    c.account = {"access_key_id": "LTAI5t-demo-0001", "region": "cn-hongkong",
                 "site": "global",
                 "instance_id": "3eeeb2fb03b743c78617168126bf6c6f"}
    c.secret = "fake-secret"
    c.region = "cn-hongkong"
    c._swas_denied = False
    calls = []

    class _FakeStartReq:
        def set_InstanceId(self, i):
            self.i = i

    class _BoomAcs:
        def do_action_with_exception(self, req):
            raise RuntimeError("InvalidInstanceId.NotFound: instance not found")

    monkeypatch.setattr(c, "_client", lambda *a, **k: _BoomAcs())
    monkeypatch.setattr(alimod, "StartInstanceRequest", _FakeStartReq, raising=False)

    def fake_common(domain, version, action, region, params=None):
        calls.append((domain, action, params))
        return b"{}"
    monkeypatch.setattr(c, "_common", fake_common)
    c.start_instance()
    assert calls and calls[0][0] == "swas.cn-hongkong.aliyuncs.com"
    assert calls[0][1] == "StartInstance"


def test_create_account_falls_back_to_first_group_when_group_missing(tmpdb):
    """新建账户发了一个不存在的分组 id 时不再直接 400，改落到第一个已建分组。

    复现线上 bug：面板只剩 Premium(id=2)，添加表单默认发 group_id=1，
    后端报“分组 1 不存在”，前端自动保存还静默吞错，表单看着像没反应。
    """
    # 只剩 Premium(id=2)：先建 Premium，再删默认分组 1
    g = tmpdb.create_group({"name": "Premium", "switch_mode": "load_balance"})
    assert g["id"] == 2
    tmpdb.delete_group(1)
    assert [x["id"] for x in tmpdb.list_groups()] == [2]
    # 前端旧默认值 group_id=1：不再 400，落到 Premium
    row = tmpdb.create_account({"name": "SWAS-KR", "group_id": 1,
                                "instance_id": "3eeeb2fb03b743c78617168126bf6c6f"})
    assert row["group_id"] == 2
    # 不传 group_id：同样落到第一个已建分组
    row2 = tmpdb.create_account({"name": "ECS-A", "instance_id": "i-abc"})
    assert row2["group_id"] == 2
    # 存在的分组照常用，不被改写
    row3 = tmpdb.create_account({"name": "ECS-B", "group_id": 2,
                                 "instance_id": "i-def"})
    assert row3["group_id"] == 2


def test_create_account_no_groups_ok(tmpdb):
    """一个分组都没有时，新建账户不绑分组也不报错（group_id 存 NULL）。"""
    tmpdb.delete_group(1)
    assert tmpdb.list_groups() == []
    row = tmpdb.create_account({"name": "Lone", "instance_id": "i-xyz"})
    assert row["group_id"] is None


# ---------------------------------------------------------------------------
# 动态切换（dynamic）：按间隔切到剩余流量最多的服务器，不看差值阈值
# ---------------------------------------------------------------------------

def _dyn_acc(i, used, gid=1):
    from app.engine import AccountView
    return AccountView(id=i, name=f"IP{i}", quota_gb=200, used_gb=used,
                       status="Running", public_ip=f"203.0.113.{i}", group_id=gid)


def _dyn_dom(i, current=None, gid=1):
    from app.engine import DomainView
    return DomainView(id=i, name=f"www{i}.example.xyz", priority=i - 1,
                      current_account_id=current, current_ip="", group_id=gid)


def _dyn_group(interval=30):
    from app.engine import GroupView
    return GroupView(id=1, name="动态组", switch_mode="dynamic",
                     dynamic_interval_minutes=interval)


def _dyn_noon():
    import datetime as dt
    from zoneinfo import ZoneInfo
    return dt.datetime(2026, 11, 23, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_dynamic_interval_minimum_30():
    """动态间隔默认 30 分钟，最少 30 分钟：小于 30 的按 30 算。"""
    from app.engine import Config, effective_dynamic_interval_minutes
    assert effective_dynamic_interval_minutes(Config()) == 30
    assert effective_dynamic_interval_minutes(Config(dynamic_interval_minutes=15)) == 30
    assert effective_dynamic_interval_minutes(Config(dynamic_interval_minutes=29)) == 30
    assert effective_dynamic_interval_minutes(Config(dynamic_interval_minutes=30)) == 30
    assert effective_dynamic_interval_minutes(Config(dynamic_interval_minutes=60)) == 60
    assert effective_dynamic_interval_minutes(Config(dynamic_interval_minutes=90)) == 90
    assert effective_dynamic_interval_minutes(Config(dynamic_interval_minutes=120)) == 120


def test_dynamic_switch_mode_accepted():
    """dynamic 是合法的切换模式：Config、GroupView、db 校验都接受。"""
    import app.db as dbmod
    from app.engine import Config, GroupView
    assert Config.from_settings({"switch_mode": "dynamic"}).switch_mode == "dynamic"
    assert GroupView(id=1, name="D", switch_mode="dynamic").switch_mode == "dynamic"
    assert dbmod._check_switch_mode("dynamic") == "dynamic"
    with pytest.raises(ValueError):
        dbmod._check_switch_mode("bogus")


def test_dynamic_switches_to_most_remaining_at_interval():
    """dynamic：间隔到点即切到剩余流量最多（用量最低）的服务器，不看差值。"""
    import datetime as dt
    from app.engine import Config, plan_reconcile
    now = _dyn_noon()
    cfg = Config(night_optimize_enabled=False)
    accts = [_dyn_acc(1, 100), _dyn_acc(2, 20)]  # A1 用 50%，A2 用 10%
    d = [_dyn_dom(1, current=1)]
    # 锚点 31 分钟前：已超过 30 分钟间隔，应切到 A2
    plan = plan_reconcile(accts, d, cfg, now, groups=[_dyn_group(30)],
                          last_switch_by_group={1: now - dt.timedelta(minutes=31)})
    assert any(a.kind == "dns" and a.account_id == 2
               and a.reason == "dynamic_switch" for a in plan.actions)
    assert plan.time_switch_by_group.get(1) == now  # 锚点更新为本轮


def test_dynamic_no_switch_before_interval():
    """dynamic：间隔未到不切，即使另一台剩余流量多得多。"""
    import datetime as dt
    from app.engine import Config, plan_reconcile
    now = _dyn_noon()
    cfg = Config(night_optimize_enabled=False)
    accts = [_dyn_acc(1, 100), _dyn_acc(2, 20)]
    d = [_dyn_dom(1, current=1)]
    # 锚点 10 分钟前：未到 30 分钟间隔，不切
    plan = plan_reconcile(accts, d, cfg, now, groups=[_dyn_group(30)],
                          last_switch_by_group={1: now - dt.timedelta(minutes=10)})
    assert not [a for a in plan.actions if a.kind == "dns"]


def test_dynamic_custom_interval_60():
    """dynamic 间隔可配：60 分钟时 31 分钟前不切，61 分钟前才切。"""
    import datetime as dt
    from app.engine import Config, plan_reconcile
    now = _dyn_noon()
    cfg = Config(night_optimize_enabled=False)
    accts = [_dyn_acc(1, 100), _dyn_acc(2, 20)]
    d = [_dyn_dom(1, current=1)]
    p1 = plan_reconcile(accts, d, cfg, now, groups=[_dyn_group(60)],
                        last_switch_by_group={1: now - dt.timedelta(minutes=31)})
    assert not [a for a in p1.actions if a.kind == "dns"]
    p2 = plan_reconcile(accts, d, cfg, now, groups=[_dyn_group(60)],
                        last_switch_by_group={1: now - dt.timedelta(minutes=61)})
    assert any(a.kind == "dns" and a.reason == "dynamic_switch"
               for a in p2.actions)


def test_dynamic_group_db_roundtrip(tmpdb):
    """db：新建/改分组可用 dynamic 模式；动态间隔小于 30 钳到 30。"""
    g = tmpdb.create_group({"name": "Dyn", "switch_mode": "dynamic",
                            "dynamic_interval_minutes": 15})
    assert g["switch_mode"] == "dynamic"
    assert g["dynamic_interval_minutes"] == 30  # 15 钳到 30
    g2 = tmpdb.update_group(g["id"], {"dynamic_interval_minutes": 90})
    assert g2["dynamic_interval_minutes"] == 90
    g3 = tmpdb.update_group(g["id"], {"switch_mode": "load_balance"})
    assert g3["switch_mode"] == "load_balance"


def test_dynamic_mode_migration_recreates_old_groups_table(tmp_path):
    """旧库迁移：groups 表 CHECK 不含 dynamic 时，init_db 重建表并保留数据。"""
    import sqlite3
    from pathlib import Path
    import app.db as dbmod
    p = Path(tmp_path) / "old.db"
    con = sqlite3.connect(p)
    con.execute("""CREATE TABLE groups(
      id INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      switch_mode TEXT NOT NULL DEFAULT 'load_balance'
        CHECK (switch_mode IN ('time', 'load_balance')),
      interval_hours INTEGER,
      custom_interval_minutes INTEGER,
      interval_start_time TEXT,
      fixed_switch_time TEXT,
      night_window_start TEXT,
      night_window_end TEXT,
      night_optimize_enabled INTEGER,
      switch_diff_mode TEXT,
      switch_diff_gb REAL,
      switch_diff_pct REAL,
      time_switch_mode TEXT,
      exclusion_action TEXT,
      recovery_action TEXT,
      paused INTEGER NOT NULL DEFAULT 0,
      priority INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL
    )""")
    con.execute("INSERT INTO groups(id, name, switch_mode, created_at) "
                "VALUES(2, 'Premium', 'load_balance', 1700000000)")
    con.commit()
    con.close()
    dbmod.close()
    dbmod.init_db(db_path=p)
    try:
        rows = dbmod.list_groups()
        assert [r["id"] for r in rows] == [2]
        assert rows[0]["name"] == "Premium"
        g = dbmod.create_group({"name": "Dyn", "switch_mode": "dynamic",
                                "dynamic_interval_minutes": 60})
        assert g["switch_mode"] == "dynamic"
        assert g["dynamic_interval_minutes"] == 60
    finally:
        dbmod.close()


# ---------------------------------------------------------------------------
# 云端备份（WebDAV）：PUT 上传 / GET 下载 / PROPFIND 查状态
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status_code, content=b""):
        self.status_code = status_code
        self.content = content


def _fake_dav(monkeypatch, mapping):
    """把 requests.request 换成按 (method, url) 查表的假实现。

    mapping: {(method, url): _FakeResp}，未命中返回 404。
    返回一个记录调用的列表。
    """
    import app.webdav as wv
    calls = []

    def fake(method, url, auth=None, headers=None, data=None, timeout=None):
        calls.append({"method": method, "url": url, "data": data,
                      "auth": auth, "headers": headers})
        return mapping.get((method, url), _FakeResp(404))

    monkeypatch.setattr(wv.requests, "request", fake)
    return calls


def test_webdav_url_validation():
    """WebDAV 地址必须是 http(s):// 完整地址，空地址报错。"""
    import app.webdav as wv
    with pytest.raises(wv.WebDAVError):
        wv.WebDAVClient("")
    with pytest.raises(wv.WebDAVError):
        wv.WebDAVClient("ftp://example.com/backup.json")
    with pytest.raises(wv.WebDAVError):
        wv.WebDAVClient("not-a-url")
    c = wv.WebDAVClient("https://dav.example.com/backups/cdt-backup.json",
                        "ali", "secret")
    assert c.url == "https://dav.example.com/backups/cdt-backup.json"
    assert c.auth is not None
    c2 = wv.WebDAVClient("https://dav.example.com/backups/cdt-backup.json")
    assert c2.auth is None  # 无用户名时不发认证头


def test_webdav_propfind_exists(monkeypatch):
    """PROPFIND 207：解析出文件存在、大小与修改时间。"""
    import app.webdav as wv
    xml = (b'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
           b'<d:response><d:propstat><d:prop>'
           b'<d:getcontentlength>12345</d:getcontentlength>'
           b'<d:getlastmodified>Wed, 22 Nov 2026 12:00:00 GMT</d:getlastmodified>'
           b'</d:prop></d:propstat></d:response></d:multistatus>')
    url = "https://dav.example.com/backups/cdt-backup.json"
    _fake_dav(monkeypatch, {("PROPFIND", url): _FakeResp(207, xml)})
    info = wv.WebDAVClient(url).propfind()
    assert info["exists"] is True
    assert info["size"] == 12345
    assert info["modified"] == "Wed, 22 Nov 2026 12:00:00 GMT"


def test_webdav_propfind_not_found(monkeypatch):
    """PROPFIND 404：服务器通、认证通过，只是文件还没传过。"""
    import app.webdav as wv
    url = "https://dav.example.com/backups/cdt-backup.json"
    _fake_dav(monkeypatch, {("PROPFIND", url): _FakeResp(404)})
    info = wv.WebDAVClient(url).propfind()
    assert info == {"exists": False, "size": None, "modified": None}


def test_webdav_propfind_auth_error(monkeypatch):
    """PROPFIND 401：报认证失败（用户名或密码不对）。"""
    import app.webdav as wv
    url = "https://dav.example.com/backups/cdt-backup.json"
    _fake_dav(monkeypatch, {("PROPFIND", url): _FakeResp(401)})
    with pytest.raises(wv.WebDAVError, match="认证失败"):
        wv.WebDAVClient(url, "ali", "wrong").propfind()


def test_webdav_put_and_get(monkeypatch):
    """PUT 上传成功返回字节数；GET 下载返回内容；GET 404 报云端无备份。"""
    import app.webdav as wv
    url = "https://dav.example.com/backups/cdt-backup.json"
    payload = b'{"version": 1}'
    calls = _fake_dav(monkeypatch, {
        ("PUT", url): _FakeResp(201),
        ("GET", url): _FakeResp(200, payload),
    })
    client = wv.WebDAVClient(url, "ali", "secret")
    assert client.put(payload) == len(payload)
    assert calls[0]["method"] == "PUT"
    assert calls[0]["data"] == payload
    assert client.get() == payload
    # 换一个 URL 模拟 GET 404
    calls2 = _fake_dav(monkeypatch, {("GET", url): _FakeResp(404)})
    with pytest.raises(wv.WebDAVError, match="云端没有备份"):
        wv.WebDAVClient(url).get()
    assert calls2[0]["method"] == "GET"


def test_webdav_put_forbidden(monkeypatch):
    """PUT 403：报认证失败。"""
    import app.webdav as wv
    url = "https://dav.example.com/backups/cdt-backup.json"
    _fake_dav(monkeypatch, {("PUT", url): _FakeResp(403)})
    with pytest.raises(wv.WebDAVError, match="认证失败"):
        wv.WebDAVClient(url, "ali", "wrong").put(b"{}")


def test_webdav_client_from_settings_missing_url():
    """未配置地址时建客户端报错（可读提示）。"""
    import app.webdav as wv
    with pytest.raises(wv.WebDAVError, match="未配置 WebDAV 地址"):
        wv.client_from_settings({})
    with pytest.raises(wv.WebDAVError, match="未配置 WebDAV 地址"):
        wv.client_from_settings({"webdav_url": ""})


def test_webdav_settings_defaults():
    """DEFAULT_SETTINGS 有 5 个 WebDAV 键，默认关闭、地址与目录为空。"""
    from app.settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["webdav_enabled"] == "0"
    assert DEFAULT_SETTINGS["webdav_url"] == ""
    assert DEFAULT_SETTINGS["webdav_backup_dir"] == ""
    assert DEFAULT_SETTINGS["webdav_username"] == ""
    assert DEFAULT_SETTINGS["webdav_password"] == ""


def test_webdav_file_url_address_plus_dir():
    """备份目录非空时：地址 + 目录 + cdt-backup.json；斜杠归一化。"""
    import app.webdav as wv
    assert wv.file_url("https://oplist.alicloud.ru/dav",
                       "/GoogleDrive/Backup/1panel") == \
        "https://oplist.alicloud.ru/dav/GoogleDrive/Backup/1panel/cdt-backup.json"
    # 地址末尾斜杠、目录首尾斜杠都归一化，不产生双斜杠
    assert wv.file_url("https://oplist.alicloud.ru/dav/",
                       "GoogleDrive/Backup/1panel/") == \
        "https://oplist.alicloud.ru/dav/GoogleDrive/Backup/1panel/cdt-backup.json"
    # 目录为空时：地址本身就是完整文件地址（旧配置，兼容）
    assert wv.file_url("https://dav.example.com/backups/cdt-backup.json", "") == \
        "https://dav.example.com/backups/cdt-backup.json"


def test_webdav_client_from_settings_uses_backup_dir():
    """配了备份目录时，客户端用的就是拼出来的完整文件地址。"""
    import app.webdav as wv
    c = wv.client_from_settings({
        "webdav_url": "https://oplist.alicloud.ru/dav",
        "webdav_backup_dir": "/GoogleDrive/Backup/1panel",
        "webdav_username": "ali", "webdav_password": "secret"})
    assert c.url == "https://oplist.alicloud.ru/dav/GoogleDrive/Backup/1panel/cdt-backup.json"
    # 目录为空时沿用旧行为：地址即完整文件地址
    c2 = wv.client_from_settings({"webdav_url": "https://dav.example.com/backups/cdt-backup.json"})
    assert c2.url == "https://dav.example.com/backups/cdt-backup.json"


def test_webdav_password_masked():
    """_mask 不回显 WebDAV 密码，只给 webdav_configured 标记。"""
    from app.api import _mask
    masked = _mask({"webdav_password": "secret", "webdav_url": "https://x/y.json",
                    "email_password": "p"})
    assert "webdav_password" not in masked
    assert masked["webdav_configured"] is True
    assert masked["webdav_url"] == "https://x/y.json"
    assert _mask({})["webdav_configured"] is False


# ---------------------------------------------------------------------------
# 全局动态模式：全局 dynamic_interval_minutes 默认 30，分组未填时继承
# ---------------------------------------------------------------------------

def test_global_dynamic_interval_default():
    """全局动态间隔默认 30 分钟（DEFAULT_SETTINGS）。"""
    from app.settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["dynamic_interval_minutes"] == "30"


def test_global_dynamic_interval_from_settings():
    """Config.from_settings 读全局动态间隔，小于 30 钳到 30。"""
    from app.engine import Config
    assert Config.from_settings({}).dynamic_interval_minutes == 30
    assert Config.from_settings({"dynamic_interval_minutes": "15"}).dynamic_interval_minutes == 30
    assert Config.from_settings({"dynamic_interval_minutes": "60"}).dynamic_interval_minutes == 60
    assert Config.from_settings({"dynamic_interval_minutes": "90"}).dynamic_interval_minutes == 90
    assert Config.from_settings(
        {"switch_mode": "dynamic", "dynamic_interval_minutes": "120"}).dynamic_interval_minutes == 120


def test_group_inherits_global_dynamic_interval():
    """分组未填动态间隔时继承全局；填了则用分组自己的。"""
    from app.engine import Config, GroupView, _group_config
    cfg = Config(dynamic_interval_minutes=60)
    g_none = GroupView(id=1, name="G", switch_mode="dynamic",
                       dynamic_interval_minutes=None)
    assert _group_config(g_none, cfg).dynamic_interval_minutes == 60
    g_own = GroupView(id=1, name="G", switch_mode="dynamic",
                      dynamic_interval_minutes=90)
    assert _group_config(g_own, cfg).dynamic_interval_minutes == 90


def test_group_follow_global_lb_nulls_diff_fields(tmpdb):
    """负载均衡跟随全局：分组的差值参数置空后，引擎用全局值。"""
    from app.engine import Config, GroupView, _group_config
    g = tmpdb.create_group({"name": "G", "switch_mode": "load_balance",
                            "switch_diff_mode": "gb", "switch_diff_pct": 5,
                            "switch_diff_gb": 3, "dynamic_interval_minutes": 90})
    assert g["switch_diff_pct"] == 5
    g2 = tmpdb.update_group(g["id"], {"switch_diff_mode": None,
                                       "switch_diff_pct": None,
                                       "switch_diff_gb": None,
                                       "dynamic_interval_minutes": None})
    assert g2["switch_diff_mode"] is None
    assert g2["switch_diff_pct"] is None
    assert g2["switch_diff_gb"] is None
    assert g2["dynamic_interval_minutes"] is None
    cfg = Config(switch_diff_mode="percent", switch_diff_pct=20,
                 switch_diff_gb=20, dynamic_interval_minutes=60)
    gv = GroupView(id=g["id"], name="G", switch_mode="load_balance",
                   switch_diff_mode=None, switch_diff_pct=None,
                   switch_diff_gb=None, dynamic_interval_minutes=None)
    gc = _group_config(gv, cfg)
    assert gc.switch_diff_mode == "percent"
    assert gc.switch_diff_pct == 20
    assert gc.switch_diff_gb == 20
    assert gc.dynamic_interval_minutes == 60


def test_group_follow_global_time_nulls_interval_fields(tmpdb):
    """定时轮巡跟随全局：分组的间隔参数置空后，引擎用全局值。"""
    from app.engine import Config, GroupView, _group_config
    g = tmpdb.create_group({"name": "G", "switch_mode": "time",
                            "interval_hours": 2, "custom_interval_minutes": 0,
                            "interval_start_time": "01:00:00"})
    g2 = tmpdb.update_group(g["id"], {"interval_hours": None,
                                       "custom_interval_minutes": None,
                                       "interval_start_time": None})
    assert g2["interval_hours"] is None
    assert g2["custom_interval_minutes"] is None
    assert g2["interval_start_time"] is None
    cfg = Config(interval_hours=6, custom_interval_minutes=0,
                 interval_start_time="00:00:00")
    gv = GroupView(id=g["id"], name="G", switch_mode="time",
                   interval_hours=None, custom_interval_minutes=None,
                   interval_start_time=None)
    gc = _group_config(gv, cfg)
    assert gc.interval_hours == 6
    assert gc.custom_interval_minutes == 0
    assert gc.interval_start_time == "00:00:00"


def test_site_name_default():
    """站点名称默认 CDT 面板；登录页标题跟随它。"""
    from app.settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["site_name"] == "CDT 面板"
