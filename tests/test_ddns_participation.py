"""分组管理成员多选参与 DDNS：未选中的实例不参与域名分配、不进排队，
状态点显示灰色“未参与”；选中后恢复参与。"""
import datetime as dt
import os
import tempfile
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.engine import (AccountView, Config, DomainView, GroupView,
                        plan_reconcile)
from app.scheduler import _to_views

TZ = ZoneInfo("Asia/Shanghai")


def acc(i, used, gid=1, ddns=True):
    return AccountView(id=i, name=f"A{i}", quota_gb=200, used_gb=used,
                       status="Running", public_ip=f"203.0.113.{i}",
                       group_id=gid, ddns_enabled=ddns)


def dom(i, current=None, gid=1):
    return DomainView(id=i, name=f"www{i}.example.xyz", priority=i - 1,
                      current_account_id=current, group_id=gid)


def noon():
    return dt.datetime(2026, 11, 22, 12, 0, tzinfo=TZ)


def test_disabled_account_excluded_from_group_allocation():
    cfg = Config()
    groups = [GroupView(id=1, name="默认分组", switch_mode="load_balance")]
    # A1 用量最低但未参与 DDNS；域名应分给 A2
    plan = plan_reconcile([acc(1, 10, ddns=False), acc(2, 40)],
                          [dom(1)], cfg, noon(), groups=groups)
    assert plan.assignments == {1: 2}
    # 全组都未参与 → 切备用地址
    plan = plan_reconcile([acc(1, 10, ddns=False), acc(2, 40, ddns=False)],
                          [dom(1)], cfg, noon(), groups=groups)
    assert plan.assignments == {1: None}
    assert plan.fallback_domain_ids == [1]


def test_to_views_carries_ddns_flag():
    views = _to_views([
        {"id": 1, "name": "A1", "quota_gb": 200, "traffic_used_gb": 10,
         "instance_status": "Running", "ddns_enabled": 0},
        {"id": 2, "name": "A2", "quota_gb": 200, "traffic_used_gb": 10,
         "instance_status": "Running"},
    ], {})
    assert views[0].ddns_enabled is False
    assert views[1].ddns_enabled is True


# ---------------- API：开关 + 状态点 ----------------
tmp = tempfile.mkdtemp()
os.environ["CDT_DATA_DIR"] = tmp
os.environ["CDT_NO_SCHEDULER"] = "1"

from app.main import create_app  # noqa: E402
from app import db  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def auth_headers(client):
    for pw in ("newpass123", "secret123"):
        r = client.post("/api/auth/login", json={"username": "管理员", "password": pw})
        if r.status_code == 200:
            return {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.post("/api/auth/setup", json={"admin_name": "管理员", "password": "secret123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    for a in db.list_accounts():
        if a["name"] == "Part-HK-1":
            db.delete_account(a["id"])


def test_api_toggle_ddns_and_status_dot(client):
    h = auth_headers(client)
    a = client.post("/api/accounts", json={
        "name": "Part-HK-1", "site": "global", "region": "cn-hongkong",
        "access_key_id": "LTAI-part", "access_key_secret": "part-secret",
        "instance_id": "i-part123", "quota_gb": 200, "group_id": 1},
        headers=h).json()
    # 新建默认参与 DDNS
    assert a["ddns_enabled"] == 1
    # 把状态写成 Running 且流量已知（不被域名指向时应为排队中）
    db.update_account(a["id"], {"instance_status": "Running",
                                "traffic_used_gb": 20.0,
                                "public_ip": "203.0.113.91"})
    st = client.get("/api/status", headers=h).json()
    row = next(x for x in st["accounts"] if x["id"] == a["id"])
    assert row["status_label"] == "排队中"
    # 取消参与 → 灰色“未参与”，不进排队
    r = client.put(f"/api/accounts/{a['id']}", json={"ddns_enabled": 0}, headers=h)
    assert r.status_code == 200 and r.json()["ddns_enabled"] == 0
    st = client.get("/api/status", headers=h).json()
    row = next(x for x in st["accounts"] if x["id"] == a["id"])
    assert row["status_dot"] == "gray" and row["status_label"] == "未参与"
    assert row.get("queue_rank") is None
    # 再选回 → 恢复排队中
    r = client.put(f"/api/accounts/{a['id']}", json={"ddns_enabled": 1}, headers=h)
    assert r.json()["ddns_enabled"] == 1
    st = client.get("/api/status", headers=h).json()
    row = next(x for x in st["accounts"] if x["id"] == a["id"])
    assert row["status_label"] == "排队中"
