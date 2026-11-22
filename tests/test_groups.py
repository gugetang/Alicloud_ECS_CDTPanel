"""分组与切换模式：一个分组只选一种模式（time | load_balance），
1 分组 = 1 域名；不同分组互相独立；legacy 无分组调用仍走全局 switch_mode。
"""
import datetime as dt
import os
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.engine import (AccountView, Config, DomainView, GroupView,
                        execute_plan, plan_reconcile)

TZ = ZoneInfo("Asia/Shanghai")


def acc(i, used, gid=1, status="Running"):
    return AccountView(id=i, name=f"IP{i}", quota_gb=200, used_gb=used,
                       status=status, public_ip=f"203.0.113.{i}", group_id=gid)


def dom(i, current=None, gid=1):
    return DomainView(id=i, name=f"www{i}.example.xyz", priority=i - 1,
                      current_account_id=current, current_ip="", group_id=gid)


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


GROUP_A_TIME = GroupView(id=1, name="分组A", switch_mode="time",
                         time_switch_mode="interval", interval_hours=2)
GROUP_B_LB = GroupView(id=2, name="分组B", switch_mode="load_balance",
                       switch_diff_mode="gb", switch_diff_gb=10)


# ---------------------------------------------------------------------------
# 分组A（time）：差值再大也不切，间隔到点才切；IP1 达阈值被踢出
# ---------------------------------------------------------------------------

def test_group_a_time_gb_gap_no_switch_until_interval_due():
    cfg = Config(night_optimize_enabled=False)
    accts = [acc(1, 100), acc(2, 88), acc(3, 40), acc(4, 60)]  # 差 12 GB
    d = [dom(1, current=1)]
    base = noon()
    p1 = plan_reconcile(accts, d, cfg, base, groups=[GROUP_A_TIME],
                        last_switch_by_group={1: base - dt.timedelta(hours=1)})
    assert not [a for a in p1.actions if a.kind == "dns"]
    p2 = plan_reconcile(accts, d, cfg, base, groups=[GROUP_A_TIME],
                        last_switch_by_group={1: base - dt.timedelta(hours=2)})
    assert any(a.kind == "dns" and a.reason == "interval_switch"
               for a in p2.actions)
    assert p2.time_switch_by_group.get(1) == base  # 旗标按组命名空间


def test_group_a_four_ip_ip1_evicted_to_lowest():
    cfg = Config(exclusion_action="stop_charging", night_optimize_enabled=False)
    accounts = [acc(1, 192), acc(2, 20), acc(3, 40), acc(4, 60)]
    plan = plan_reconcile(accounts, [dom(1, current=1)], cfg, noon(),
                          groups=[GROUP_A_TIME])
    assert 1 in plan.evicted_account_ids
    assert plan.assignments == {1: 2}  # 切到 IP2，不再参与 DDNS 的是 IP1
    cloud, dns = FakeCloud(), FakeDns()
    res = execute_plan(plan, accounts, [dom(1, current=1)], cloud, dns,
                       dry_run=False)
    assert cloud.calls == [("stop", 1, True)]  # 节省停机恰好一次
    assert dns.calls == [("www1.example.xyz", "203.0.113.2")]
    dns_i = next(i for i, e in enumerate(res["executed"]) if e.startswith("dns:"))
    stop_i = next(i for i, e in enumerate(res["executed"]) if e.startswith("stop:"))
    assert dns_i < stop_i  # 先切 DNS，后停机


# ---------------------------------------------------------------------------
# 分组B（load_balance）：时间到点不切，GB 差值到点才切
# ---------------------------------------------------------------------------

def test_group_b_load_balance_time_triggers_do_not_switch():
    cfg = Config(night_optimize_enabled=True)
    accts = [acc(5, 50, gid=2), acc(6, 45, gid=2)]
    d = [dom(2, current=5, gid=2)]
    p1 = plan_reconcile(accts, d, cfg, noon(), groups=[GROUP_B_LB],
                        last_switch_by_group={2: noon() - dt.timedelta(hours=2)})
    assert not [a for a in p1.actions if a.kind == "dns"]
    night = dt.datetime(2026, 11, 22, 1, 30, tzinfo=TZ)
    p2 = plan_reconcile(accts, d, cfg, night, groups=[GROUP_B_LB])
    assert not [a for a in p2.actions if a.kind == "dns"]


def test_group_b_load_balance_gb_gap_switches():
    cfg = Config()
    plan = plan_reconcile([acc(5, 100, gid=2), acc(6, 88, gid=2)],
                          [dom(2, current=5, gid=2)], cfg, noon(),
                          groups=[GROUP_B_LB])
    assert any(a.kind == "dns" and a.reason == "traffic_diff_gb"
               for a in plan.actions)
    plan2 = plan_reconcile([acc(5, 100, gid=2), acc(6, 95, gid=2)],
                           [dom(2, current=5, gid=2)], cfg, noon(),
                           groups=[GROUP_B_LB])
    assert not [a for a in plan2.actions if a.kind == "dns"]


# ---------------------------------------------------------------------------
# 两组同一次调用：互相独立，无跨组分配
# ---------------------------------------------------------------------------

def test_two_groups_independent_in_one_call():
    cfg = Config(night_optimize_enabled=False)
    # A 组：IP2 88 GB 最低；B 组：IP6 88 GB 最低
    accounts = [acc(1, 100), acc(2, 88), acc(3, 120), acc(4, 130),
                acc(5, 100, gid=2), acc(6, 88, gid=2)]
    domains = [dom(1, current=1), dom(2, current=5, gid=2)]
    base = noon()
    plan = plan_reconcile(accounts, domains, cfg, base,
                          groups=[GROUP_A_TIME, GROUP_B_LB],
                          last_switch_by_group={1: base - dt.timedelta(hours=2)})
    # A 组间隔到点切自己的域名；B 组 GB 差 12 GB 切自己的域名
    assert plan.assignments == {1: 2, 2: 6}
    reasons = {(a.domain_id, a.reason) for a in plan.actions if a.kind == "dns"}
    assert (1, "interval_switch") in reasons
    assert (2, "traffic_diff_gb") in reasons


def test_group_mode_overrides_global_both():
    # 全局默认 both，但分组 A 只按时间：GB 差值在间隔未到时不切
    cfg = Config()  # switch_mode=both（全局）
    plan = plan_reconcile([acc(1, 100), acc(2, 88)],
                          [dom(1, current=1)], cfg, noon(),
                          groups=[GROUP_A_TIME],
                          last_switch_by_group={1: noon() - dt.timedelta(hours=1)})
    assert not [a for a in plan.actions if a.kind == "dns"]


# ---------------------------------------------------------------------------
# legacy 无分组调用：仍按全局 switch_mode（旧 39 个测试的保障）
# ---------------------------------------------------------------------------

def test_legacy_no_group_still_honours_global_switch_mode():
    cfg = Config(switch_mode="time", time_switch_mode="interval",
                 interval_hours=2, night_optimize_enabled=False)
    base = noon()
    p = plan_reconcile([acc(1, 50, gid=None), acc(2, 45, gid=None)],
                       [dom(1, current=1, gid=None)], cfg, base,
                       last_time_switch_at=base - dt.timedelta(hours=2))
    assert any(a.kind == "dns" and a.reason == "interval_switch"
               for a in p.actions)


def test_legacy_multi_domain_cycle_preserved():
    # 多域名循环分配只保留在 legacy 路径：4 个域名分给 3 个账户，第 4 个循环
    cfg = Config()
    plan = plan_reconcile([acc(1, 100, gid=None), acc(2, 20, gid=None),
                           acc(3, 60, gid=None)],
                          [dom(1, gid=None), dom(2, gid=None),
                           dom(3, gid=None), dom(4, gid=None)], cfg, noon())
    assert plan.assignments == {1: 2, 2: 3, 3: 1, 4: 2}


# ---------------------------------------------------------------------------
# DB / API 层：1 分组=1 域名、账户只属于一个分组、删非空分组被拒
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


def test_one_group_one_domain_enforced(tmpdb):
    g = tmpdb.create_group({"name": "分组A", "switch_mode": "time"})
    d1 = tmpdb.create_domain({"name": "a.example.xyz", "group_id": g["id"]})
    assert d1["group_id"] == g["id"]
    with pytest.raises(ValueError, match="一个分组只能配置一个域名"):
        tmpdb.create_domain({"name": "b.example.xyz", "group_id": g["id"]})
    # 删掉域名后可再绑
    tmpdb.delete_domain(d1["id"])
    d2 = tmpdb.create_domain({"name": "b.example.xyz", "group_id": g["id"]})
    assert d2["group_id"] == g["id"]
    # 移到已占用分组也被拒
    g2 = tmpdb.create_group({"name": "分组B", "switch_mode": "load_balance"})
    tmpdb.create_domain({"name": "c.example.xyz", "group_id": g2["id"]})
    with pytest.raises(ValueError, match="一个分组只能配置一个域名"):
        tmpdb.update_domain(d2["id"], {"group_id": g2["id"]})


def test_account_belongs_to_exactly_one_group(tmpdb):
    ga = tmpdb.create_group({"name": "分组A", "switch_mode": "time"})
    gb = tmpdb.create_group({"name": "分组B", "switch_mode": "load_balance"})
    a = tmpdb.create_account({"name": "IP1", "group_id": ga["id"]})
    assert a["group_id"] == ga["id"]
    assert len([x for x in tmpdb.list_accounts() if x["id"] == a["id"]]) == 1
    # 移组：改 group_id 即移动，列表里只出现一次
    tmpdb.update_account(a["id"], {"group_id": gb["id"]})
    rows = [x for x in tmpdb.list_accounts() if x["id"] == a["id"]]
    assert len(rows) == 1 and rows[0]["group_id"] == gb["id"]
    with pytest.raises(ValueError, match="分组 .* 不存在"):
        tmpdb.create_account({"name": "IPX", "group_id": 9999})


def test_delete_nonempty_or_default_group_rejected(tmpdb):
    ga = tmpdb.create_group({"name": "分组A", "switch_mode": "time"})
    tmpdb.create_account({"name": "IP1", "group_id": ga["id"]})
    with pytest.raises(ValueError, match="请先删除或移走"):
        tmpdb.delete_group(ga["id"])
    with pytest.raises(ValueError, match="默认分组不能删除"):
        tmpdb.delete_group(1)
    empty = tmpdb.create_group({"name": "空分组", "switch_mode": "time"})
    tmpdb.delete_group(empty["id"])  # 空分组可删
    assert tmpdb.get_group(empty["id"]) is None


def test_default_group_created_and_invalid_mode_rejected(tmpdb):
    assert tmpdb.get_group(1)["name"] == "默认分组"
    assert tmpdb.get_group(1)["switch_mode"] == "load_balance"
    with pytest.raises(ValueError, match="只能是 time"):
        tmpdb.create_group({"name": "坏分组", "switch_mode": "both"})


def test_status_dot_derivation(tmpdb):
    """状态点（新映射；旧期望“已踢出=黄点”已改为“已跳出=灰点”，见下）：
    正在服务+Running=绿（在线）；排队+Running=黄（排队中，带排队位次）；
    已跳出+Running=灰（已跳出）；Stopped=红（已停机）；
    已跳出+已停机=红但 detail 同时写已跳出；未知=红（离线）；
    Starting/Pending=高亮闪烁绿 green_pulse（启动中）；
    Stopping=闪烁红 red_pulse（停止中）；Running 但流量未知=灰（未知）。
    优先级：启动中 > 停机/离线 > 已跳出 > 在线 > 排队 > 未知。"""
    from app.api import _public_account

    def row(used, status, aid=1):
        return {"id": aid, "name": f"IP{aid}", "quota_gb": 200,
                "traffic_used_gb": used, "instance_status": status,
                "protection_threshold_pct": None, "exclusion_action": None,
                "switch_diff_mode": None, "switch_diff_gb": None,
                "switch_diff_pct": None, "access_key_secret_enc": "",
                "public_ip": f"203.0.113.{aid}", "group_id": 1,
                "billing_month_cost": None, "billing_currency": None}

    # 绿：正被域名指向（正在服务）+ Running + 未踢出
    serving = _public_account(row(37.77, "Running"), serving_map={1: "www1.example.xyz"},
                              queue_ranks={})
    assert serving["status_dot"] == "green" and serving["status_label"] == "在线"
    assert serving["serving"] is True and serving["serving_domain"] == "www1.example.xyz"
    # 黄：Running + 未踢出 + 可分配 + 未被域名指向（排队待命，第 2 位）
    queued = _public_account(row(37.77, "Running"), serving_map={}, queue_ranks={1: 2})
    assert queued["status_dot"] == "yellow" and queued["status_label"] == "排队中"
    assert "第 2 位" in queued["status_detail"] and "可随时接替" in queued["status_detail"]
    # 灰：已跳出 + Running（旧映射这里是黄点“已排除”，现改为灰点“已跳出”）
    ev = _public_account(row(192.0, "Running"), serving_map={}, queue_ranks={})
    assert ev["status_dot"] == "gray" and ev["status_label"] == "已跳出"
    assert "96.0%" in ev["status_detail"] and "自动恢复" in ev["status_detail"]
    # 红：已停机；已跳出+已停机仍是红点，但 detail 不能丢跳出信息
    assert _public_account(row(37.77, "Stopped"))["status_dot"] == "red"
    assert _public_account(row(37.77, "Stopped"))["status_label"] == "已停机"
    ev_stop = _public_account(row(192.0, "Stopped"), serving_map={}, queue_ranks={})
    assert ev_stop["status_dot"] == "red"
    assert "已跳出" in ev_stop["status_detail"] and "已停机" in ev_stop["status_detail"]
    assert _public_account(row(None, ""))["status_dot"] == "red"
    assert _public_account(row(None, ""))["status_label"] == "离线"
    # 闪烁绿：启动中（Starting/Pending）；闪烁红：停止中
    assert _public_account(row(37.77, "Starting"))["status_dot"] == "green_pulse"
    assert _public_account(row(37.77, "Starting"))["status_label"] == "启动中"
    assert _public_account(row(37.77, "Pending"))["status_dot"] == "green_pulse"
    assert _public_account(row(37.77, "Stopping"))["status_dot"] == "red_pulse"
    assert _public_account(row(37.77, "Stopping"))["status_label"] == "停止中"
    # 灰：Running 但流量未知
    assert _public_account(row(None, "Running"))["status_dot"] == "gray"
    assert _public_account(row(None, "Running"))["status_label"] == "未知"


def test_status_queue_ranks_from_db(tmpdb):
    """排队位次按组内使用率升序：同组 3 台，未被指向的两台排第 1/2 位，
    被指向的那台是在线（绿），不占排队位次。"""
    from app.api import _public_account, _status_context
    g = tmpdb.create_group({"name": "分组Q", "switch_mode": "load_balance"})
    a1 = tmpdb.create_account({"name": "IP1", "group_id": g["id"]})
    a2 = tmpdb.create_account({"name": "IP2", "group_id": g["id"]})
    a3 = tmpdb.create_account({"name": "IP3", "group_id": g["id"]})
    for a, used in ((a1, 100), (a2, 20), (a3, 60)):
        tmpdb.update_account(a["id"], {"traffic_used_gb": used,
                                       "instance_status": "Running",
                                       "public_ip": f"203.0.113.{a['id']}"})
    d = tmpdb.create_domain({"name": "q.example.xyz", "group_id": g["id"]})
    tmpdb.update_domain(d["id"], {"current_account_id": a2["id"]})
    serving_map, ranks = _status_context(tmpdb.list_accounts())
    assert serving_map == {a2["id"]: "q.example.xyz"}
    assert ranks == {a3["id"]: 1, a1["id"]: 2}  # 60 GB 第 1 位，100 GB 第 2 位
    assert _public_account(tmpdb.get_account(a2["id"]), serving_map, ranks)["status_dot"] == "green"
    q3 = _public_account(tmpdb.get_account(a3["id"]), serving_map, ranks)
    assert q3["status_dot"] == "yellow" and q3["queue_rank"] == 1
    assert _public_account(tmpdb.get_account(a1["id"]), serving_map, ranks)["queue_rank"] == 2


def test_rename_regression(tmpdb):
    """改名回归确认：上一轮已实现的 Ali 账户页行内改名仍通过（无改动）。"""
    ga = tmpdb.create_group({"name": "分组A", "switch_mode": "time"})
    a = tmpdb.create_account({"name": "IP1", "group_id": ga["id"],
                              "instance_id": "i-1"})
    row = tmpdb.update_account(a["id"], {"name": "IP1-renamed"})
    assert row["name"] == "IP1-renamed" and row["instance_id"] == "i-1"


def test_rename_validation(tmpdb):
    """改名：空名/超长/同组重名被拒；跨组同名可以；成功改名不影响其他字段。"""
    ga = tmpdb.create_group({"name": "分组A", "switch_mode": "time"})
    gb = tmpdb.create_group({"name": "分组B", "switch_mode": "load_balance"})
    a = tmpdb.create_account({"name": "IP1", "group_id": ga["id"],
                              "instance_id": "i-1"})
    tmpdb.create_account({"name": "IP2", "group_id": ga["id"],
                          "instance_id": "i-2"})
    with pytest.raises(ValueError, match="不能为空"):
        tmpdb.update_account(a["id"], {"name": "   "})
    with pytest.raises(ValueError, match="最多 64"):
        tmpdb.update_account(a["id"], {"name": "x" * 65})
    with pytest.raises(ValueError, match="同名服务器"):
        tmpdb.update_account(a["id"], {"name": "IP2"})
    # 跨组同名允许
    tmpdb.create_account({"name": "IP2", "group_id": gb["id"],
                          "instance_id": "i-3"})
    row = tmpdb.update_account(a["id"], {"name": "IP1-new"})
    assert row["name"] == "IP1-new" and row["instance_id"] == "i-1"
    assert row["group_id"] == ga["id"]


def test_api_rename_and_status_shows_new_name(tmpdb):
    """API 改名：成功后 /api/status 里显示新名，并记一条改名事件。"""
    from fastapi.testclient import TestClient
    from app.main import create_app
    client = TestClient(create_app())
    a = client.post("/api/accounts", json={
        "name": "Old-Name", "site": "global", "region": "cn-hongkong",
        "access_key_id": "LTAI-test", "instance_id": "i-rename1"}).json()
    r = client.put(f"/api/accounts/{a['id']}", json={"name": "New-Name"})
    assert r.status_code == 200 and r.json()["name"] == "New-Name"
    assert r.json()["instance_id"] == "i-rename1"
    st = client.get("/api/status").json()
    assert any(x["name"] == "New-Name" for x in st["accounts"])
    assert not any(x["name"] == "Old-Name" for x in st["accounts"])
    assert any(e["title"] == "服务器改名" and "Old-Name → New-Name" in e["detail"]
               for e in st["events"])
    # 空名 / 同组重名经 API 也返回 400
    assert client.put(f"/api/accounts/{a['id']}", json={"name": ""}).status_code == 400
    b = client.post("/api/accounts", json={
        "name": "Other", "site": "global", "region": "cn-hongkong",
        "access_key_id": "LTAI-test", "instance_id": "i-rename2"}).json()
    assert client.put(f"/api/accounts/{a['id']}",
                      json={"name": "Other"}).status_code == 400


def test_api_duplicate_domain_in_group_returns_400(tmpdb):
    # API 层：同一分组绑第二个域名返回 400 并给出明确错误
    # （tmpdb fixture 已把 db._conn 连到隔离库，create_app 复用同一连接）
    from fastapi.testclient import TestClient
    from app.main import create_app
    client = TestClient(create_app())
    g = client.post("/api/groups", json={"name": "API分组",
                                         "switch_mode": "load_balance"}).json()
    r1 = client.post("/api/domains", json={"name": "api1.example.xyz",
                                           "group_id": g["id"]})
    assert r1.status_code == 200, r1.text
    r2 = client.post("/api/domains", json={"name": "api2.example.xyz",
                                           "group_id": g["id"]})
    assert r2.status_code == 400
    assert "一个分组只能配置一个域名" in r2.text
    assert "api1.example.xyz" in r2.text
    # 删分组前必须先处理成员
    r3 = client.delete(f"/api/groups/{g['id']}")
    assert r3.status_code == 400
