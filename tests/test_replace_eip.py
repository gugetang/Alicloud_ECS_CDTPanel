"""更换 EIP：POST /api/accounts/{id}/replace-eip。

顺序（用户定）：申请新 EIP → 解绑旧 EIP → 绑定新 EIP → 带宽与旧 EIP 一致
→ DNS 切到新 IP → 共享带宽：旧 EIP 在包里时先移出包、再把新 EIP 加进
同一个包 → 释放旧 EIP。仅 ECS；轻量服务器、无 EIP 的都拒绝；
申请成功后后面任何一步失败都回滚（释放新 EIP、旧 EIP 尽量绑回）。
共享带宽步骤放在 DNS 已切走后：失败只告警，不回滚。"""
import json
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
from app.aliyun import AliyunClient, AliyunError  # noqa: E402

OLD_IP = "203.0.113.10"
NEW_IP = "203.0.113.99"
OLD_ALLOC = "eip-old1"
NEW_ALLOC = "eip-new1"
OLD_PKG = "cbwp-test1"


class FakeAliyun:
    """假阿里云客户端：EIP 带内存状态，记录调用顺序。"""
    calls: list = []
    fail_on: str | None = None
    current_ip = OLD_IP
    pkg_id: str | None = None

    def __init__(self, account, secret):
        self.account = account

    def describe_instance(self):
        return {"status": "Running", "public_ip": FakeAliyun.current_ip,
                "instance_type": "ecs.t6-c1m1.large"}

    def get_traffic_gb(self):
        return 42.0

    def get_billing(self, timezone="Asia/Shanghai", force_refresh=False):
        return None

    def describe_eip_for_instance(self):
        if self.account["instance_id"] == "i-noeip123":
            return None
        return {"allocation_id": OLD_ALLOC, "ip": OLD_IP, "bandwidth": 5,
                "bandwidth_package_id": FakeAliyun.pkg_id,
                "instance_id": self.account["instance_id"], "status": "InUse"}

    def allocate_eip(self, bandwidth=None):
        FakeAliyun.calls.append(("allocate", bandwidth))
        return {"allocation_id": NEW_ALLOC, "ip": NEW_IP}

    def unassociate_eip(self, allocation_id, instance_id):
        FakeAliyun.calls.append(("unassociate", allocation_id))

    def associate_eip(self, allocation_id, instance_id):
        FakeAliyun.calls.append(("associate", allocation_id))
        if FakeAliyun.fail_on == "associate" and allocation_id == NEW_ALLOC:
            raise AliyunError("绑定 EIP 失败：模拟失败")
        FakeAliyun.current_ip = NEW_IP if allocation_id == NEW_ALLOC else OLD_IP

    def set_eip_bandwidth(self, allocation_id, bandwidth):
        FakeAliyun.calls.append(("set_bandwidth", allocation_id, bandwidth))

    def add_eip_to_bandwidth_package(self, bandwidth_package_id, allocation_id):
        FakeAliyun.calls.append(("add_to_package", bandwidth_package_id, allocation_id))
        if FakeAliyun.fail_on == "add-package":
            raise AliyunError("加入共享带宽包失败：模拟失败")

    def remove_eip_from_bandwidth_package(self, bandwidth_package_id, allocation_id):
        FakeAliyun.calls.append(("remove_from_package", bandwidth_package_id, allocation_id))
        if FakeAliyun.fail_on == "remove-package":
            raise AliyunError("移出共享带宽包失败：模拟失败")

    def release_eip(self, allocation_id):
        FakeAliyun.calls.append(("release", allocation_id))
        if FakeAliyun.fail_on == "release-old" and allocation_id == OLD_ALLOC:
            raise AliyunError("释放 EIP 失败：模拟失败")


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def auth_headers(client):
    # 全量跑时前面的测试文件可能已启用登录（管理员密码）：先试已知的
    # 两个密码，登不上就走首次 setup。单文件跑时直接 setup。
    for pw in ("newpass123", "secret123"):
        r = client.post("/api/auth/login", json={"username": "管理员", "password": pw})
        if r.status_code == 200:
            return {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.post("/api/auth/setup", json={"admin_name": "管理员", "password": "secret123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="module")
def headers(client):
    return auth_headers(client)


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    for a in db.list_accounts():
        if a["name"] in ("EIP-HK-1", "EIP-SWAS-1", "EIP-NOEIP-1"):
            db.delete_account(a["id"])


@pytest.fixture(scope="module")
def accounts(client, headers):
    ids = {}
    for name, iid in (("EIP-HK-1", "i-eip123"),
                      ("EIP-SWAS-1", "3eeeb2fb-1111-2222-3333-444455556666"),
                      ("EIP-NOEIP-1", "i-noeip123")):
        a = client.post("/api/accounts", json={
            "name": name, "site": "global", "region": "cn-hongkong",
            "access_key_id": "LTAI-test", "access_key_secret": "secret-test",
            "instance_id": iid, "quota_gb": 200}, headers=headers).json()
        ids[name] = a["id"]
    return ids


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeAliyun.calls = []
    FakeAliyun.fail_on = None
    FakeAliyun.current_ip = OLD_IP
    FakeAliyun.pkg_id = None
    yield
    FakeAliyun.calls = []
    FakeAliyun.fail_on = None
    FakeAliyun.pkg_id = None


def _patches():
    return (patch("app.aliyun.AliyunClient", FakeAliyun),
            patch("app.scheduler.AliyunClient", FakeAliyun),
            patch("app.api._refresh_dns_for_account",
                  return_value=["example.com"]))


def test_replace_eip_success(client, accounts, headers):
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        r = client.post(f"/api/accounts/{accounts['EIP-HK-1']}/replace-eip", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["old_ip"] == OLD_IP
    assert r.json()["new_ip"] == NEW_IP
    assert r.json()["dns_domains"] == ["example.com"]
    assert r.json()["warning"] == ""
    assert r.json()["public_ip"] == NEW_IP
    # 严格按用户顺序：申请 → 解绑旧 → 绑定新 → 设带宽 → 释放旧
    assert FakeAliyun.calls == [
        ("allocate", 5), ("unassociate", OLD_ALLOC),
        ("associate", NEW_ALLOC), ("set_bandwidth", NEW_ALLOC, 5),
        ("release", OLD_ALLOC)]
    row = db.get_account(accounts["EIP-HK-1"])
    assert row["public_ip"] == NEW_IP


def test_replace_eip_swas_rejected(client, accounts, headers):
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        r = client.post(f"/api/accounts/{accounts['EIP-SWAS-1']}/replace-eip", headers=headers)
    assert r.status_code == 400
    assert "轻量应用服务器" in r.json()["detail"]
    assert FakeAliyun.calls == []


def test_replace_eip_no_eip_rejected(client, accounts, headers):
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        r = client.post(f"/api/accounts/{accounts['EIP-NOEIP-1']}/replace-eip", headers=headers)
    assert r.status_code == 400
    assert "未绑定 EIP" in r.json()["detail"]
    assert FakeAliyun.calls == []


def test_replace_eip_associate_fails_rolls_back(client, accounts, headers):
    FakeAliyun.fail_on = "associate"
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        r = client.post(f"/api/accounts/{accounts['EIP-HK-1']}/replace-eip", headers=headers)
    assert r.status_code == 400
    # 新 EIP 已释放、旧 EIP 已重新绑回、旧 EIP 未释放
    assert ("release", NEW_ALLOC) in FakeAliyun.calls
    assert ("associate", OLD_ALLOC) in FakeAliyun.calls
    assert ("release", OLD_ALLOC) not in FakeAliyun.calls
    assert "重新绑回" in r.json()["detail"]
    assert FakeAliyun.current_ip == OLD_IP


def test_replace_eip_dns_fails_rolls_back(client, accounts, headers):
    p1, p2 = _patches()[0], _patches()[1]
    with p1, p2, patch(
            "app.api._refresh_dns_for_account",
            side_effect=ValueError("DNS 同步失败：example.com 未指向新 IP")):
        r = client.post(f"/api/accounts/{accounts['EIP-HK-1']}/replace-eip", headers=headers)
    assert r.status_code == 400
    assert "DNS 同步失败" in r.json()["detail"]
    assert ("release", NEW_ALLOC) in FakeAliyun.calls
    assert ("associate", OLD_ALLOC) in FakeAliyun.calls
    assert ("release", OLD_ALLOC) not in FakeAliyun.calls


def test_replace_eip_release_old_fails_warns(client, accounts, headers):
    FakeAliyun.fail_on = "release-old"
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        r = client.post(f"/api/accounts/{accounts['EIP-HK-1']}/replace-eip", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["new_ip"] == NEW_IP
    assert "释放失败" in r.json()["warning"]


def test_replace_eip_shared_bandwidth_package(client, accounts, headers):
    # 旧 EIP 在共享带宽包里：DNS 切走后先把旧 EIP 移出包、再把新 EIP 加进
    # 同一个包，最后才释放旧 EIP；全程成功则无告警。
    FakeAliyun.pkg_id = OLD_PKG
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        r = client.post(f"/api/accounts/{accounts['EIP-HK-1']}/replace-eip", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["new_ip"] == NEW_IP
    assert r.json()["warning"] == ""
    assert FakeAliyun.calls == [
        ("allocate", 5), ("unassociate", OLD_ALLOC),
        ("associate", NEW_ALLOC), ("set_bandwidth", NEW_ALLOC, 5),
        ("remove_from_package", OLD_PKG, OLD_ALLOC),
        ("add_to_package", OLD_PKG, NEW_ALLOC),
        ("release", OLD_ALLOC)]


def test_replace_eip_shared_bandwidth_add_fails_warns(client, accounts, headers):
    # 新 EIP 加进共享带宽包失败：DNS 已切走，不回滚，只告警；旧 EIP 照常释放。
    FakeAliyun.pkg_id = OLD_PKG
    FakeAliyun.fail_on = "add-package"
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        r = client.post(f"/api/accounts/{accounts['EIP-HK-1']}/replace-eip", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["new_ip"] == NEW_IP
    assert "加入共享带宽包失败" in r.json()["warning"]
    assert ("release", OLD_ALLOC) in FakeAliyun.calls
    assert ("release", NEW_ALLOC) not in FakeAliyun.calls
    row = db.get_account(accounts["EIP-HK-1"])
    assert row["public_ip"] == NEW_IP


def test_replace_eip_shared_bandwidth_remove_fails_warns(client, accounts, headers):
    # 旧 EIP 移出共享带宽包失败：只告警，继续加新 EIP 进包并释放旧 EIP。
    FakeAliyun.pkg_id = OLD_PKG
    FakeAliyun.fail_on = "remove-package"
    p1, p2, p3 = _patches()
    with p1, p2, p3:
        r = client.post(f"/api/accounts/{accounts['EIP-HK-1']}/replace-eip", headers=headers)
    assert r.status_code == 200, r.text
    assert "移出共享带宽包失败" in r.json()["warning"]
    assert ("add_to_package", OLD_PKG, NEW_ALLOC) in FakeAliyun.calls
    assert ("release", OLD_ALLOC) in FakeAliyun.calls


def test_client_describe_eip_parses_bandwidth_package():
    c = AliyunClient({"access_key_id": "x", "region": "cn-hongkong",
                      "instance_id": "i-eip123"}, "y")
    payload = {"EipAddresses": {"EipAddress": [
        {"AllocationId": OLD_ALLOC, "IpAddress": OLD_IP, "Bandwidth": "5",
         "BandwidthPackageId": OLD_PKG,
         "InstanceId": "i-eip123", "Status": "InUse"}]}}
    with patch.object(c, "_common", return_value=json.dumps(payload)):
        out = c.describe_eip_for_instance()
    assert out["bandwidth_package_id"] == OLD_PKG


def test_client_add_remove_bandwidth_package_params():
    c = AliyunClient({"access_key_id": "x", "region": "cn-hongkong",
                      "instance_id": "i-eip123"}, "y")
    with patch.object(c, "_common", return_value="{}") as m:
        c.add_eip_to_bandwidth_package(OLD_PKG, NEW_ALLOC)
    domain, version, action, region, params = m.call_args.args
    assert domain == "vpc.cn-hongkong.aliyuncs.com"
    assert version == "2016-04-28" and action == "AddCommonBandwidthPackageIp"
    # 注意：传的是 EIP 的 AllocationId（IpInstanceId），不是 IP 地址。
    assert params == {"RegionId": "cn-hongkong",
                      "BandwidthPackageId": OLD_PKG,
                      "IpInstanceId": NEW_ALLOC, "IpType": "EIP"}
    with patch.object(c, "_common", return_value="{}") as m:
        c.remove_eip_from_bandwidth_package(OLD_PKG, OLD_ALLOC)
    _, _, action, _, params = m.call_args.args
    assert action == "RemoveCommonBandwidthPackageIp"
    assert params == {"RegionId": "cn-hongkong",
                      "BandwidthPackageId": OLD_PKG,
                      "IpInstanceId": OLD_ALLOC}


def test_client_allocate_eip_params():
    c = AliyunClient({"access_key_id": "x", "region": "cn-hongkong",
                      "instance_id": "i-eip123"}, "y")
    with patch.object(c, "_common", return_value=json.dumps(
            {"AllocationId": NEW_ALLOC, "EipAddress": NEW_IP})) as m:
        out = c.allocate_eip(bandwidth=5)
    assert out == {"allocation_id": NEW_ALLOC, "ip": NEW_IP}
    domain, version, action, region, params = m.call_args.args
    assert domain == "vpc.cn-hongkong.aliyuncs.com"
    assert version == "2016-04-28" and action == "AllocateEipAddress"
    assert params["InternetChargeType"] == "PayByTraffic"
    assert params["InstanceChargeType"] == "PostPaid"
    assert params["Bandwidth"] == "5"


def test_client_allocate_eip_clamps_bandwidth_to_pay_by_traffic_max():
    # 旧 EIP 在共享带宽包里时读到的是包的带宽（如 1000），按流量计费
    # 只允许 1~200，直接传会报 OperationFailed.SaleValidate，钳到 200。
    c = AliyunClient({"access_key_id": "x", "region": "cn-hongkong",
                      "instance_id": "i-eip123"}, "y")
    with patch.object(c, "_common", return_value=json.dumps(
            {"AllocationId": NEW_ALLOC, "EipAddress": NEW_IP})) as m:
        out = c.allocate_eip(bandwidth=1000)
    assert out == {"allocation_id": NEW_ALLOC, "ip": NEW_IP}
    _, _, action, _, params = m.call_args.args
    assert action == "AllocateEipAddress"
    assert params["Bandwidth"] == "200"


def test_client_describe_eip_parses():
    c = AliyunClient({"access_key_id": "x", "region": "cn-hongkong",
                      "instance_id": "i-eip123"}, "y")
    payload = {"EipAddresses": {"EipAddress": [
        {"AllocationId": OLD_ALLOC, "IpAddress": OLD_IP, "Bandwidth": "5",
         "InstanceId": "i-eip123", "Status": "InUse"}]}}
    with patch.object(c, "_common", return_value=json.dumps(payload)) as m:
        out = c.describe_eip_for_instance()
    assert out == {"allocation_id": OLD_ALLOC, "ip": OLD_IP, "bandwidth": 5,
                   "bandwidth_package_id": None,
                   "instance_id": "i-eip123", "status": "InUse"}
    _, _, action, _, params = m.call_args.args
    assert action == "DescribeEipAddresses"
    assert params["InstanceId"] == "i-eip123"


def test_client_associate_uses_ecs_instance_type():
    c = AliyunClient({"access_key_id": "x", "region": "cn-hongkong",
                      "instance_id": "i-eip123"}, "y")
    with patch.object(c, "_common", return_value="{}") as m:
        c.associate_eip(NEW_ALLOC, "i-eip123")
    _, _, action, _, params = m.call_args.args
    assert action == "AssociateEipAddress"
    assert params["InstanceType"] == "EcsInstance"
    assert params["AllocationId"] == NEW_ALLOC


class FakeNotifier:
    """假通知器：记录 send 调用，并按真实开关规则过滤。"""
    sent: list = []

    def __init__(self, settings):
        self.s = settings

    def enabled(self, event):
        from app.settings import EVENT_TOGGLE_KEYS, LOG_ONLY_EVENTS
        if event in LOG_ONLY_EVENTS:
            return False
        key = EVENT_TOGGLE_KEYS.get(event)
        return self.s.get(key, "1") == "1" if key else True

    def send(self, event, title, body, channel=None, force=False):
        if not self.enabled(event):
            return []
        FakeNotifier.sent.append((event, title, body))
        return ["telegram"]


def test_replace_eip_success_sends_push(client, accounts, headers):
    FakeNotifier.sent = []
    p1, p2, p3 = _patches()
    with p1, p2, p3, patch("app.api.Notifier", FakeNotifier):
        r = client.post(f"/api/accounts/{accounts['EIP-HK-1']}/replace-eip", headers=headers)
    assert r.status_code == 200, r.text
    assert FakeNotifier.sent == [
        ("eip_replace", "EIP 已更换",
         f"EIP-HK-1 EIP 已更换：{OLD_IP} → {NEW_IP}；DNS 已切：example.com")]


def test_replace_eip_push_toggle_off(client, accounts, headers):
    FakeNotifier.sent = []
    r = client.put("/api/settings", json={"notify_instance_status": "0"}, headers=headers)
    assert r.status_code == 200
    try:
        p1, p2, p3 = _patches()
        with p1, p2, p3, patch("app.api.Notifier", FakeNotifier):
            r = client.post(f"/api/accounts/{accounts['EIP-HK-1']}/replace-eip", headers=headers)
        assert r.status_code == 200, r.text
        assert FakeNotifier.sent == []
    finally:
        client.put("/api/settings", json={"notify_instance_status": "1"}, headers=headers)


def test_replace_eip_failure_sends_push(client, accounts, headers):
    FakeAliyun.fail_on = "associate"
    FakeNotifier.sent = []
    p1, p2, p3 = _patches()
    with p1, p2, p3, patch("app.api.Notifier", FakeNotifier):
        r = client.post(f"/api/accounts/{accounts['EIP-HK-1']}/replace-eip", headers=headers)
    assert r.status_code == 400
    # 失败也走 eip_replace 事件（即实例状态开关），不再走只记日志的 error 事件
    assert any(e == "eip_replace" and t == "更换 EIP 失败"
               for e, t, _b in FakeNotifier.sent)
    # 失败记一条 error 日志
    assert any(ev["kind"] == "error" and ev["title"] == "更换 EIP 失败"
               for ev in db.list_events())


def test_notify_events_include_eip_replace(client, headers):
    r = client.get("/api/notify", headers=headers)
    assert r.status_code == 200
    assert r.json()["events"]["instance_status"] is True
    assert "eip_replace" not in r.json()["events"]
