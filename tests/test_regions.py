"""区域与站点（来源必须是阿里云接口）：

- aliyun.parse_regions / parse_instance_regions：解析、排序、去重。
- AliyunClient.describe_regions / discover_instance_regions /
  find_instance_region：用 fake 传输层测试，不调真实阿里云。
- API：GET /api/aliyun/regions（account_id 或临时 AK）、
  POST /api/aliyun/discover-regions（已有实例所在地域 + 全部可用地域）、
  POST /api/aliyun/find-instance-region（按实例 ID 自动发现地域）；
  接口失败返回明确错误 + fallback_regions。
"""
import datetime as dt
from pathlib import Path

import pytest

from app.aliyun import (AliyunError, parse_instance_regions, parse_regions)


@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


def _client(monkeypatch):
    """绕过 __init__ 的 SDK 检查，造一个 AliyunClient 空壳。"""
    from app.aliyun import AliyunClient
    c = object.__new__(AliyunClient)
    c.account = {"access_key_id": "LTAI5t-demo-0001", "region": "cn-hongkong",
                 "site": "global", "instance_id": "i-demo000000000000001"}
    c.secret = "fake-secret"
    c.region = "cn-hongkong"
    # 与 __init__ 一致：SWAS 403 标记默认 False（find_instance_region /
    # describe_instance 的最终报错要读它）
    c._swas_denied = False
    return c


# ---------------------------------------------------------------------------
# 纯解析：DescribeRegions / DescribeInstances
# ---------------------------------------------------------------------------

def test_parse_regions_sorted_and_skips_empty():
    data = {"Regions": {"Region": [
        {"RegionId": "ap-southeast-1", "LocalName": "新加坡",
         "RegionEndpoint": "ecs.ap-southeast-1.aliyuncs.com"},
        {"RegionId": "cn-hongkong", "LocalName": "中国香港",
         "RegionEndpoint": "ecs.cn-hongkong.aliyuncs.com"},
        {"RegionId": "", "LocalName": "空"},
        {"RegionId": "cn-beijing", "LocalName": "北京"},
    ]}}
    out = parse_regions(data)
    # 按 local_name 排序（Python 字符串按码位排：中 < 北 < 新）
    assert [r["region_id"] for r in out] == [
        "cn-hongkong", "cn-beijing", "ap-southeast-1"]
    assert out[1]["local_name"] == "北京"
    assert out[0]["endpoint"] == "ecs.cn-hongkong.aliyuncs.com"


def test_parse_instance_regions_dedup_sorted():
    data = {"Instances": {"Instance": [
        {"InstanceId": "i-1", "RegionId": "cn-hongkong"},
        {"InstanceId": "i-2", "RegionId": "ap-southeast-1"},
        {"InstanceId": "i-3", "RegionId": "cn-hongkong"},
        {"InstanceId": "i-4"},
    ]}}
    assert parse_instance_regions(data) == ["ap-southeast-1", "cn-hongkong"]


# ---------------------------------------------------------------------------
# 客户端方法（fake 传输层）
# ---------------------------------------------------------------------------

def test_describe_regions_uses_common_api(monkeypatch):
    import json
    c = _client(monkeypatch)
    seen = {}

    def fake_common(domain, version, action, region):
        seen.update(domain=domain, version=version, action=action)
        return json.dumps({"Regions": {"Region": [
            {"RegionId": "cn-hongkong", "LocalName": "中国香港",
             "RegionEndpoint": "ecs.cn-hongkong.aliyuncs.com"},
            {"RegionId": "ap-southeast-1", "LocalName": "新加坡",
             "RegionEndpoint": "ecs.ap-southeast-1.aliyuncs.com"},
        ]}})
    monkeypatch.setattr(c, "_common", fake_common)
    out = c.describe_regions()
    assert seen["domain"] == "ecs.aliyuncs.com"
    assert seen["action"] == "DescribeRegions"
    assert [r["region_id"] for r in out] == ["cn-hongkong", "ap-southeast-1"]


def test_describe_regions_failure_raises(monkeypatch):
    c = _client(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("InvalidAccessKeyId.NotFound")
    monkeypatch.setattr(c, "_common", boom)
    with pytest.raises(AliyunError, match="地域查询失败"):
        c.describe_regions()


def test_discover_instance_regions_dedup(monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setattr(c, "describe_regions", lambda: [
        {"region_id": "cn-hongkong", "local_name": "中国香港"},
        {"region_id": "ap-southeast-1", "local_name": "新加坡"},
        {"region_id": "cn-beijing", "local_name": "北京"},
    ])
    has = {"cn-hongkong": ["cn-hongkong"], "ap-southeast-1": [],
           "cn-beijing": ["cn-beijing"]}
    monkeypatch.setattr(c, "_list_instance_regions_in",
                        lambda region: has[region])
    assert c.discover_instance_regions() == ["cn-beijing", "cn-hongkong"]


def test_find_instance_region_hits_second_region(monkeypatch):
    import json
    c = _client(monkeypatch)
    monkeypatch.setattr(c, "describe_regions", lambda: [
        {"region_id": "cn-hongkong", "local_name": "中国香港"},
        {"region_id": "ap-southeast-1", "local_name": "新加坡"},
    ])

    class FakeReq:
        def __init__(self, region):
            self.region = region

        def do_action_with_exception(self, req):
            if self.region == "ap-southeast-1":
                return json.dumps({"Instances": {"Instance": [
                    {"InstanceId": "i-found0000000000001",
                     "RegionId": "ap-southeast-1"}]}})
            return json.dumps({"Instances": {"Instance": []}})

    monkeypatch.setattr(c, "_client", lambda region=None: FakeReq(region or "cn-hongkong"))
    assert c.find_instance_region("i-found0000000000001") == "ap-southeast-1"


def test_find_instance_region_not_found_raises(monkeypatch):
    import json
    c = _client(monkeypatch)
    monkeypatch.setattr(c, "describe_regions", lambda: [
        {"region_id": "cn-hongkong", "local_name": "中国香港"}])

    class FakeReq:
        def do_action_with_exception(self, req):
            # “哪里都查不到”：空列表对两种解析都成立——ECS 读
            # Instances.Instance，SWAS 读 Instances 列表本身。
            # 不能返回 ECS 形的 {"Instances": {"Instance": []}}，
            # SWAS 解析会对 dict 做 [0] 取下标抛 KeyError: 0。
            return json.dumps({"Instances": []})

    monkeypatch.setattr(c, "_client", lambda region=None: FakeReq())
    with pytest.raises(AliyunError, match="查不到"):
        c.find_instance_region("i-missing000000000000")


# ---------------------------------------------------------------------------
# API 端点（Fake AliyunClient，不调真实阿里云）
# ---------------------------------------------------------------------------

class FakeAliyun:
    def __init__(self, account, secret):
        self.account = account
        self.secret = secret

    def describe_regions(self):
        if self.account.get("site") == "cn":
            return [{"region_id": "cn-beijing", "local_name": "北京", "endpoint": ""},
                    {"region_id": "cn-hongkong", "local_name": "中国香港", "endpoint": ""}]
        return [{"region_id": "cn-hongkong", "local_name": "中国香港", "endpoint": ""},
                {"region_id": "ap-southeast-1", "local_name": "新加坡", "endpoint": ""}]

    def discover_instance_regions(self, regions=None):
        return ["cn-hongkong"]

    def find_instance_region(self, instance_id):
        if instance_id == "i-found0000000000001":
            return "ap-southeast-1"
        raise AliyunError(f"实例 {instance_id} 在该账号下查不到", kind="other")


def _fake_client(monkeypatch, cls=FakeAliyun):
    monkeypatch.setattr("app.aliyun.AliyunClient", cls)


def _mk_account_with_ak(tmpdb, name="IP1", site="global"):
    from app.security import encrypt_secret
    return tmpdb.create_account({
        "name": name, "group_id": 1, "site": site,
        "access_key_id": "LTAI5t-demo-0001",
        "access_key_secret_enc": encrypt_secret("fake-secret"),
        "instance_id": "i-demo000000000000001"})


def test_api_regions_by_account_id(tmpdb, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    _fake_client(monkeypatch)
    a = _mk_account_with_ak(tmpdb)
    client = TestClient(create_app())
    r = client.post("/api/aliyun/regions", json={"account_id": a["id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["site"] == "global" and body["source"] == "aliyun"
    assert [x["region_id"] for x in body["regions"]] == [
        "cn-hongkong", "ap-southeast-1"]


def test_api_regions_site_switch_changes_list(tmpdb, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    _fake_client(monkeypatch)
    a = _mk_account_with_ak(tmpdb, site="cn")
    client = TestClient(create_app())
    # 站点切换后地域列表随站点变：国内站返回北京/中国香港
    r = client.post("/api/aliyun/regions",
                    json={"account_id": a["id"], "site": "cn"})
    assert [x["region_id"] for x in r.json()["regions"]] == [
        "cn-beijing", "cn-hongkong"]
    # 同一账户用 site=global 覆盖：返回国际站列表
    r = client.post("/api/aliyun/regions",
                    json={"account_id": a["id"], "site": "global"})
    assert [x["region_id"] for x in r.json()["regions"]] == [
        "cn-hongkong", "ap-southeast-1"]


def test_api_regions_temp_ak_and_no_creds(tmpdb, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    _fake_client(monkeypatch)
    client = TestClient(create_app())
    # 临时 AK 查询（不保存）：能查
    r = client.post("/api/aliyun/regions", json={
        "site": "global", "access_key_id": "LTAI5t-demo-0001",
        "access_key_secret": "fake-secret"})
    assert r.status_code == 200
    assert r.json()["regions"]
    # 无账户、无 AK：明确报错
    r = client.post("/api/aliyun/regions", json={"site": "global"})
    assert r.status_code == 400
    assert "AccessKey" in r.json()["detail"]
    # 未配置 AK 的账户：明确报错
    a = tmpdb.create_account({"name": "IP9", "group_id": 1})
    r = client.post("/api/aliyun/regions", json={"account_id": a["id"]})
    assert r.status_code == 400


def test_api_regions_failure_returns_fallback(tmpdb, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app

    class Broken(FakeAliyun):
        def describe_regions(self):
            raise AliyunError("InvalidAccessKeyId.NotFound", kind="auth")

    _fake_client(monkeypatch, Broken)
    client = TestClient(create_app())
    r = client.post("/api/aliyun/regions", json={
        "site": "global", "access_key_id": "LTAI5t-demo-0001",
        "access_key_secret": "bad"})
    assert r.status_code == 502
    assert "地域查询失败" in r.json()["detail"]
    ids = [x["region_id"] for x in r.json()["fallback_regions"]]
    assert "cn-hongkong" in ids and "cn-beijing" in ids  # 内置常用地域


def test_api_discover_regions_owned_and_all(tmpdb, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    _fake_client(monkeypatch)
    a = _mk_account_with_ak(tmpdb)
    client = TestClient(create_app())
    r = client.post("/api/aliyun/discover-regions",
                    json={"account_id": a["id"]})
    assert r.status_code == 200
    body = r.json()
    assert [x["region_id"] for x in body["owned"]] == ["cn-hongkong"]
    assert body["owned"][0]["owned"] is True
    assert len(body["all"]) == 2  # 全部可用地域


def test_api_find_instance_region(tmpdb, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    _fake_client(monkeypatch)
    a = _mk_account_with_ak(tmpdb)
    client = TestClient(create_app())
    r = client.post("/api/aliyun/find-instance-region",
                    json={"account_id": a["id"],
                          "instance_id": "i-found0000000000001"})
    assert r.status_code == 200
    assert r.json()["region_id"] == "ap-southeast-1"
    assert r.json()["local_name"] == "新加坡"
    # 查不到：报错，不静默填错
    r = client.post("/api/aliyun/find-instance-region",
                    json={"account_id": a["id"],
                          "instance_id": "i-missing000000000000"})
    assert r.status_code == 502
    assert "查不到" in r.json()["detail"]
    # 未填实例 ID：400
    r = client.post("/api/aliyun/find-instance-region",
                    json={"account_id": a["id"], "instance_id": ""})
    assert r.status_code == 400
