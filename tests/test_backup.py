"""备份与恢复：导出整库 JSON、恢复后数据与加密凭据都回来、非法备份拒绝。"""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

tmp = tempfile.mkdtemp()
os.environ["CDT_DATA_DIR"] = tmp
os.environ["CDT_NO_SCHEDULER"] = "1"

from app.main import create_app  # noqa: E402
from app import db  # noqa: E402
from app.security import decrypt_secret  # noqa: E402


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
        if a["name"] == "Backup-HK-1":
            db.delete_account(a["id"])
    for d in db.list_domains():
        if d["name"] == "backup.example.xyz":
            db.delete_domain(d["id"])
    for g in db.list_groups():
        if g["name"] == "Backup-G":
            try:
                db.delete_group(g["id"])
            except Exception:
                pass


def test_backup_export_restore_roundtrip(client):
    h = auth_headers(client)
    g = client.post("/api/groups", json={"name": "Backup-G", "switch_mode": "load_balance"}, headers=h).json()
    a = client.post("/api/accounts", json={
        "name": "Backup-HK-1", "site": "global", "region": "cn-hongkong",
        "access_key_id": "LTAI-backup", "access_key_secret": "backup-secret-123",
        "instance_id": "i-backup123", "quota_gb": 200, "group_id": g["id"]},
        headers=h).json()
    d = client.post("/api/domains", json={
        "name": "backup.example.xyz", "zone": "example.xyz", "priority": 0,
        "group_id": g["id"]}, headers=h).json()
    assert a["name"] == "Backup-HK-1" and d["name"] == "backup.example.xyz"

    r = client.get("/api/backup/export", headers=h)
    assert r.status_code == 200
    assert "attachment" in r.headers.get("Content-Disposition", "")
    data = r.json()
    assert data["version"] == 1
    assert data.get("master_key_b64")
    names = [x["name"] for x in data["accounts"]]
    assert "Backup-HK-1" in names
    assert any(x["name"] == "backup.example.xyz" for x in data["domains"])

    # 删掉账户，再恢复：账户与加密凭据都回来
    assert client.delete(f"/api/accounts/{a['id']}", headers=h).status_code == 200
    assert db.get_account(a["id"]) is None
    r = client.post("/api/backup/restore", json=data, headers=h)
    assert r.status_code == 200
    assert r.json()["counts"]["accounts"] >= 1
    row = db.get_account(a["id"])
    assert row and row["name"] == "Backup-HK-1"
    assert decrypt_secret(row["access_key_secret_enc"]) == "backup-secret-123"
    assert db.get_domain(d["id"])["name"] == "backup.example.xyz"


def test_backup_restore_rejects_invalid(client):
    h = auth_headers(client)
    assert client.post("/api/backup/restore", json={}, headers=h).status_code == 400
    assert client.post("/api/backup/restore", json={"version": 99, "settings": {}, "flags": {}}, headers=h).status_code == 400
    assert client.post("/api/backup/restore",
                       json={"version": 1, "settings": {}, "flags": {},
                             "accounts": [{"instance_id": "i-x"}]}, headers=h).status_code == 400


def test_backup_export_requires_login_when_configured(client):
    s = client.get("/api/auth/status").json()
    if s["admin_configured"]:
        assert client.get("/api/backup/export").status_code == 401
        assert client.post("/api/backup/restore", json={}).status_code == 401
