import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# 测试用隔离数据库：必须在 import app.main 之前设环境变量
tmp = tempfile.mkdtemp()
os.environ["CDT_DATA_DIR"] = tmp
os.environ["CDT_NO_SCHEDULER"] = "1"

from app.main import create_app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_status_and_crud(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["total_used_gb"] == 0
    a = client.post("/api/accounts", json={
        "name": "Test-HK-1", "site": "global", "region": "cn-hongkong",
        "access_key_id": "LTAI-test", "instance_id": "i-test123"}).json()
    assert a["name"] == "Test-HK-1" and "access_key_secret_enc" not in a
    d = client.post("/api/domains", json={
        "name": "www.example.xyz", "zone": "example.xyz", "priority": 0}).json()
    assert d["name"] == "www.example.xyz"
    r = client.post("/api/schedule/dry-run")
    assert r.status_code == 200
    assert r.json()["dry_run"] is True
    assert r.json()["assignments"] == {"1": None}  # 无流量快照 → 无可分配账户 → 备用
    assert r.json()["fallback_domains"] == [d["id"]]
    assert client.get("/api/accounts").status_code == 200
    assert client.get("/api/domains").status_code == 200
    assert client.get("/api/logs").status_code == 200
