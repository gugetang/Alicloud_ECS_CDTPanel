"""概览卡片/列表的单台操作：刷新 / 启动 / 重启 / 停机。"""
import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

tmp = tempfile.mkdtemp()
os.environ["CDT_DATA_DIR"] = tmp
os.environ["CDT_NO_SCHEDULER"] = "1"

from app.main import create_app  # noqa: E402
from app import db  # noqa: E402


class FakeAliyun:
    """假阿里云客户端：记录开关机调用，返回固定快照。"""
    calls: list = []

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
        FakeAliyun.calls.append("start")

    def reboot_instance(self):
        FakeAliyun.calls.append("reboot")

    def stop_instance(self, stop_charging=False):
        FakeAliyun.calls.append("stop")


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    # 用例跑完删掉测试账户：全部测试文件共用首次 import 的隔离库，
    # 不清理会让 test_api 里“初始总流量为 0”的断言读到这台的 42 GiB。
    yield
    for a in db.list_accounts():
        if a["name"] == "Action-HK-1":
            db.delete_account(a["id"])


@pytest.fixture(scope="module")
def account_id(client):
    a = client.post("/api/accounts", json={
        "name": "Action-HK-1", "site": "global", "region": "cn-hongkong",
        "access_key_id": "LTAI-test", "access_key_secret": "secret-test",
        "instance_id": "i-action123", "quota_gb": 200}).json()
    return a["id"]


def test_refresh_account(client, account_id):
    with patch("app.aliyun.AliyunClient", FakeAliyun), \
         patch("app.scheduler.AliyunClient", FakeAliyun):
        r = client.post(f"/api/accounts/{account_id}/refresh")
    assert r.status_code == 200
    assert r.json()["traffic_used_gb"] == 42.0
    assert r.json()["public_ip"] == "203.0.113.10"
    assert "access_key_secret_enc" not in r.json()
    row = db.get_account(account_id)
    assert row["instance_status"] == "Running"


def test_start_restart_stop(client, account_id):
    FakeAliyun.calls.clear()
    with patch("app.aliyun.AliyunClient", FakeAliyun), \
         patch("app.scheduler.AliyunClient", FakeAliyun):
        assert client.post(f"/api/accounts/{account_id}/start").status_code == 200
        assert client.post(f"/api/accounts/{account_id}/restart").status_code == 200
        assert client.post(f"/api/accounts/{account_id}/stop").status_code == 200
    assert FakeAliyun.calls == ["start", "reboot", "stop"]


def test_account_actions_errors(client, account_id):
    assert client.post("/api/accounts/999999/refresh").status_code == 404
    assert client.post("/api/accounts/999999/start").status_code == 404
    # 未配齐凭据/实例 → 400
    db.update_account(account_id, {"instance_id": ""})
    assert client.post(f"/api/accounts/{account_id}/start").status_code == 400
    db.update_account(account_id, {"instance_id": "i-action123"})
