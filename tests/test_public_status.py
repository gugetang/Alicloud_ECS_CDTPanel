"""公开概览接口 GET /api/public/status（未登录也可看概览数据）。

管理员明确决定公开只读：未登录时概览显示真实数据。公开接口返回与
登录态 /api/status 相同的概览数据，但经收窄：AK ID 不给，IP 与实例 ID
按页面显示规则打码（域名与事件里的 IP 同理），另加只含 6 个公开字段的
settings 小对象；不含任何凭据与完整设置。

用与其他测试相同的 tmpdb 夹具隔离库：各测试模块共用进程级 app.db
全局连接，函数级夹具每次重指到本测试的临时库。
"""
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["CDT_NO_SCHEDULER"] = "1"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.api as apimod
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    monkeypatch.setattr(apimod, "DATA_DIR", Path(tmp_path))
    from app.main import create_app
    yield TestClient(create_app())
    dbmod.close()


def _authed_headers(client):
    r = client.post("/api/auth/setup", json={"admin_name": "管理员", "password": "secret123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_public_status_open_before_setup(client):
    # 未配置管理员时（首次向导），公开接口无需 token 即可访问。
    r = client.get("/api/public/status")
    assert r.status_code == 200
    body = r.json()
    assert "accounts" in body
    assert "total_used_gb" in body
    assert "settings" in body
    assert set(body["settings"].keys()) == {
        "site_name", "site_logo", "site_intro",
        "exchange_rate_usd_cny", "settlement_currency",
        "protection_threshold_pct",
    }


def test_public_status_narrowed_after_setup(client):
    import app.db as dbmod
    headers = _authed_headers(client)
    # 放两个实例进库（ECS + 非 i- 开头的 SWAS 式 ID），让两边都有真实
    # 账户数据可比对；public_ip 是调度刷新字段，建完再补。
    a1 = dbmod.create_account({
        "name": "PUB-1",
        "access_key_id": "LTAI5tAbCdEfGhIjKlMn",
        "access_key_secret_enc": "aesgcm$dummy",
        "instance_id": "i-j6c66hv9i4kym1v358dn",
    })
    dbmod.update_account(a1["id"], {"public_ip": "47.243.214.192"})
    a2 = dbmod.create_account({
        "name": "PUB-2",
        "access_key_id": "LTAI5tZzZzZzZzZzZzZzZz",
        "instance_id": "9f86d081-884c-4d2d-8a2f-1234567890ab",
    })
    dbmod.update_account(a2["id"], {"public_ip": "8.210.121.164"})
    dom = dbmod.create_domain({"name": "pub.example.com", "zone": "example.com"})
    dbmod.update_domain(dom["id"], {"current_ip": "47.243.214.192"})
    dbmod.add_event("info", "更换 EIP", "PUB-1 EIP 已更换：47.243.214.192 → 8.210.121.164")
    pub = client.get("/api/public/status")
    assert pub.status_code == 200
    pub_body = pub.json()
    # 登录态接口无 token 时拒绝，公开接口放行。
    assert client.get("/api/status").status_code == 401
    auth = client.get("/api/status", headers=headers)
    assert auth.status_code == 200
    auth_body = auth.json()
    # 与登录态相同的顶层结构（除 settings 小对象是公开接口独有的）。
    assert set(pub_body.keys()) - {"settings"} == set(auth_body.keys())
    assert [a["name"] for a in pub_body["accounts"]] == ["PUB-1", "PUB-2"]
    # 公开 payload 里任何位置都不含 AK ID 与 AK Secret。
    pub_json = json.dumps(pub_body)
    assert "access_key_id" not in pub_json
    assert "access_key_secret_enc" not in pub_json
    assert "access_key_secret" not in pub_json
    assert "LTAI5tAbCdEfGhIjKlMn" not in pub_json
    assert "LTAI5tZzZzZzZzZzZzZzZz" not in pub_json
    # 账户行：IP 与实例 ID 按页面规则打码（与前端 maskIp / maskInstanceId 一致）。
    pub_accounts = {a["name"]: a for a in pub_body["accounts"]}
    assert pub_accounts["PUB-1"]["public_ip"] == "47.243.*.*"
    assert pub_accounts["PUB-1"]["instance_id"] == "i-j6c66h***"
    assert pub_accounts["PUB-2"]["public_ip"] == "8.210.*.*"
    assert pub_accounts["PUB-2"]["instance_id"] == "9f86d0***"
    # 分组内嵌的账户行同样收窄。
    for g in pub_body["groups"]:
        for a in g.get("accounts") or []:
            assert "access_key_id" not in a
            assert a.get("public_ip") in (None, "", "47.243.*.*", "8.210.*.*")
    # 域名 current_ip 打码；事件正文里的 IP 打码；完整原文不出现。
    assert pub_body["domains"][0]["current_ip"] == "47.243.*.*"
    ev = next(e for e in pub_body["events"] if e["title"] == "更换 EIP")
    assert ev["detail"] == "PUB-1 EIP 已更换：47.243.*.* → 8.210.*.*"
    assert "47.243.214.192" not in pub_json
    assert "8.210.121.164" not in pub_json
    assert "i-j6c66hv9i4kym1v358dn" not in pub_json
    # settings 小对象字段齐全，只含公开字段。
    s = pub_body["settings"]
    assert set(s.keys()) == {
        "site_name", "site_logo", "site_intro",
        "exchange_rate_usd_cny", "settlement_currency",
        "protection_threshold_pct",
    }
    assert s["site_name"] == "CDT 面板"
    assert s["site_logo"] is False
    assert s["settlement_currency"] == "USD"
    assert s["exchange_rate_usd_cny"] is not None
    assert s["protection_threshold_pct"] is not None
    # 登录态仍是完整原文，不受收窄影响。
    auth_accounts = {a["name"]: a for a in auth_body["accounts"]}
    assert auth_accounts["PUB-1"]["access_key_id"] == "LTAI5tAbCdEfGhIjKlMn"
    assert auth_accounts["PUB-1"]["public_ip"] == "47.243.214.192"
    assert auth_accounts["PUB-1"]["instance_id"] == "i-j6c66hv9i4kym1v358dn"
    assert auth_body["domains"][0]["current_ip"] == "47.243.214.192"
    # 其余概览字段不变：总量、费用、账户数与登录态一致。
    assert pub_body["total_used_gb"] == auth_body["total_used_gb"]
    assert pub_body["total_cost"] == auth_body["total_cost"]
    assert pub_body["accounts_total"] == auth_body["accounts_total"]
