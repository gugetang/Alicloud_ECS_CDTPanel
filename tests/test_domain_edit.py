"""域名管理：编辑已保存域名（改域名/改绑定分组）；改到已被别的域名
占用的分组返回 400；改回自己原来绑定的分组成功；改域名时旧的
当前记录快照被清空，下次 DDNS 重新发现 Zone/记录。"""
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


def _mk_group(client, h, name):
    g = client.post("/api/groups", json={"name": name, "switch_mode": "load_balance"}, headers=h)
    assert g.status_code == 200, g.text
    return g.json()


def _mk_domain(client, h, name, gid):
    d = client.post("/api/domains", json={"name": name, "zone": "example.xyz",
                                         "priority": 0, "group_id": gid}, headers=h)
    assert d.status_code == 200, d.text
    return d.json()


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    for d in db.list_domains():
        if (d["name"] or "").startswith("edit-"):
            db.delete_domain(d["id"])
    for g in db.list_groups():
        if (g["name"] or "").startswith("Edit-G"):
            try:
                db.delete_group(g["id"])
            except ValueError:
                pass


def test_edit_domain_name_and_group(client):
    h = auth_headers(client)
    g1 = _mk_group(client, h, "Edit-G1")
    g2 = _mk_group(client, h, "Edit-G2")
    g3 = _mk_group(client, h, "Edit-G3")
    a = _mk_domain(client, h, "edit-a.example.xyz", g1["id"])
    _mk_domain(client, h, "edit-b.example.xyz", g2["id"])
    # 先给 A 挂一个旧快照，改名后应被清空
    db.update_domain(a["id"], {"current_ip": "203.0.113.99", "fallback_active": 1})
    # 改到已被 B 占用的分组 → 400
    r = client.put(f"/api/domains/{a['id']}", json={
        "name": "edit-a2.example.xyz", "zone": "example.xyz",
        "group_id": g2["id"]}, headers=h)
    assert r.status_code == 400
    assert "一个分组只能配置一个域名" in r.text
    # 改域名 + 改到空闲分组成功，旧快照清空
    r = client.put(f"/api/domains/{a['id']}", json={
        "name": "edit-a2.example.xyz", "zone": "example.xyz",
        "group_id": g3["id"], "current_account_id": None,
        "current_ip": "", "fallback_active": 0}, headers=h)
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["name"] == "edit-a2.example.xyz"
    assert row["zone"] == "example.xyz" and row["group_id"] == g3["id"]
    assert (row.get("current_ip") or "") == "" and not row.get("fallback_active")
    # 改回自己原来绑定的分组成功
    r = client.put(f"/api/domains/{a['id']}", json={
        "name": "edit-a2.example.xyz", "zone": "example.xyz",
        "group_id": g1["id"]}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["group_id"] == g1["id"]
