"""拖动排序 + 分组改名：账户/分组按拖拽顺序重排 priority 后列表顺序
跟着变；新建分组排最后；分组名可在设置里改（PUT name）。"""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

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
        r = client.post("/api/auth/login", json={"password": pw})
        if r.status_code == 200:
            return {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.post("/api/auth/setup", json={"admin_name": "管理员", "password": "secret123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_account(client, h, name):
    r = client.post("/api/accounts", json={
        "name": name, "instance_id": f"i-reorder-{name}", "access_key_id": "LTAI5t-demo-0001",
        "quota_gb": 200, "region": "cn-hongkong", "site": "global"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _mk_group(client, h, name):
    g = client.post("/api/groups", json={"name": name, "switch_mode": "load_balance"}, headers=h)
    assert g.status_code == 200, g.text
    return g.json()


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    for a in db.list_accounts():
        if (a["name"] or "").startswith("Reorder-"):
            db.delete_account(a["id"])
    for g in db.list_groups():
        if (g["name"] or "").startswith("Reorder-G"):
            try:
                db.delete_group(g["id"])
            except ValueError:
                pass


def test_reorder_accounts(client):
    h = auth_headers(client)
    a1 = _mk_account(client, h, "Reorder-A")
    a2 = _mk_account(client, h, "Reorder-B")
    ids = [a["id"] for a in db.list_accounts() if (a["name"] or "").startswith("Reorder-")]
    assert ids == [a1["id"], a2["id"]]
    r = client.post("/api/accounts/reorder", json={"order": [a2["id"], a1["id"]]}, headers=h)
    assert r.status_code == 200, r.text
    ids = [a["id"] for a in db.list_accounts() if (a["name"] or "").startswith("Reorder-")]
    assert ids == [a2["id"], a1["id"]]


def test_reorder_groups_and_new_group_goes_last(client):
    h = auth_headers(client)
    g1 = _mk_group(client, h, "Reorder-G1")
    g2 = _mk_group(client, h, "Reorder-G2")
    # 新建分组排最后（priority = max+1）
    assert db.get_group(g2["id"])["priority"] > db.get_group(g1["id"])["priority"]
    r = client.post("/api/groups/reorder", json={"order": [g2["id"], g1["id"]]}, headers=h)
    assert r.status_code == 200, r.text
    ids = [g["id"] for g in db.list_groups() if (g["name"] or "").startswith("Reorder-G")]
    assert ids == [g2["id"], g1["id"]]
    # 重排后新建的分组仍排最后
    g3 = _mk_group(client, h, "Reorder-G3")
    ids = [g["id"] for g in db.list_groups() if (g["name"] or "").startswith("Reorder-G")]
    assert ids == [g2["id"], g1["id"], g3["id"]]


def test_rename_group(client):
    h = auth_headers(client)
    g = _mk_group(client, h, "Reorder-G-rename")
    r = client.put(f"/api/groups/{g['id']}", json={"name": "Reorder-G-renamed"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Reorder-G-renamed"
    assert db.get_group(g["id"])["name"] == "Reorder-G-renamed"
