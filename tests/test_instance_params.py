"""实例设置“参数设置”：跟随系统 / 自定义（流量额度 + 流量保护阈值）。

- 跟随系统：quota_gb / protection_threshold_pct 存 NULL，生效值取全局
  （默认额度 200 GiB、阈值 95%）；公开接口同时返回原始值 *_raw，
  前端据此判断模式，卡片/列表仍用生效值显示。
- 自定义：两个值都存入，生效值就是自定义值。
- 初始模式规则：阈值原始值不为空，或额度原始值与全局值不同 → 自定义；
  否则 → 跟随系统（额度=200 且全局也是 200 的旧实例不会无故变自定义）。
- 校验：自定义阈值必须在 (0, 100] 内，否则 400 / ValueError 中文报错。
"""
from pathlib import Path

import pytest


@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


def _mode(raw_quota, raw_threshold, global_quota=200):
    """前端初始模式规则（与 AccountForm 一致）：阈值原始值不为空，
    或额度原始值与全局值不同 → 自定义，否则跟随系统。"""
    return ("custom" if raw_threshold is not None
            or (raw_quota is not None and float(raw_quota) != global_quota)
            else "follow")


def test_follow_saves_null_and_effective_is_global(tmpdb):
    from app.api import _public_account
    row = tmpdb.create_account({"name": "IP1", "group_id": 1,
                                "quota_gb": None,
                                "protection_threshold_pct": None})
    assert row["quota_gb"] is None
    assert row["protection_threshold_pct"] is None
    pub = _public_account(tmpdb.get_account(row["id"]))
    assert pub["quota_gb_raw"] is None
    assert pub["protection_threshold_pct_raw"] is None
    assert pub["quota_gb"] == 200  # 生效值取全局
    assert pub["protection_threshold_pct"] == 95
    assert _mode(pub["quota_gb_raw"], pub["protection_threshold_pct_raw"]) == "follow"


def test_custom_saves_quota_and_threshold_and_effective(tmpdb):
    from app.api import _public_account
    row = tmpdb.create_account({"name": "IP1", "group_id": 1})
    row = tmpdb.update_account(row["id"], {"quota_gb": 300,
                                           "protection_threshold_pct": 80})
    assert row["quota_gb"] == 300
    assert row["protection_threshold_pct"] == 80
    pub = _public_account(tmpdb.get_account(row["id"]))
    assert pub["quota_gb_raw"] == 300
    assert pub["protection_threshold_pct_raw"] == 80
    assert pub["quota_gb"] == 300
    assert pub["protection_threshold_pct"] == 80
    assert _mode(pub["quota_gb_raw"], pub["protection_threshold_pct_raw"]) == "custom"


def test_quota_equal_to_global_without_threshold_is_follow(tmpdb):
    # 旧实例额度=200 且全局也是 200、阈值未填：算跟随系统，有效值不变。
    from app.api import _public_account
    row = tmpdb.create_account({"name": "IP1", "group_id": 1, "quota_gb": 200})
    pub = _public_account(tmpdb.get_account(row["id"]))
    assert pub["quota_gb_raw"] == 200
    assert pub["protection_threshold_pct_raw"] is None
    assert _mode(pub["quota_gb_raw"], pub["protection_threshold_pct_raw"]) == "follow"
    # 只填了阈值（额度仍等于全局）也算自定义
    tmpdb.update_account(row["id"], {"protection_threshold_pct": 80})
    pub = _public_account(tmpdb.get_account(row["id"]))
    assert _mode(pub["quota_gb_raw"], pub["protection_threshold_pct_raw"]) == "custom"


def test_invalid_threshold_rejected(tmpdb):
    row = tmpdb.create_account({"name": "IP1", "group_id": 1})
    for bad in (0, -5, 101, "abc"):
        with pytest.raises(ValueError, match="0 到 100"):
            tmpdb.update_account(row["id"], {"protection_threshold_pct": bad})
    with pytest.raises(ValueError, match="0 到 100"):
        tmpdb.create_account({"name": "IP2", "group_id": 1,
                              "protection_threshold_pct": 150})
    # 边界值通过：100 可以，NULL（跟随系统）也可以
    row = tmpdb.update_account(row["id"], {"protection_threshold_pct": 100})
    assert row["protection_threshold_pct"] == 100
    row = tmpdb.update_account(row["id"], {"protection_threshold_pct": None})
    assert row["protection_threshold_pct"] is None


def test_api_follow_and_custom_and_invalid_threshold(tmpdb):
    from fastapi.testclient import TestClient
    from app.main import create_app
    client = TestClient(create_app())
    # 跟随系统：发 NULL，返回原始值 NULL、生效值全局
    r = client.post("/api/accounts", json={
        "name": "IP-API-1", "site": "global", "region": "cn-hongkong",
        "access_key_id": "LTAI-test", "access_key_secret": "secret-test",
        "instance_id": "i-param1", "quota_gb": None,
        "protection_threshold_pct": None})
    assert r.status_code == 200, r.text
    assert r.json()["quota_gb_raw"] is None
    assert r.json()["protection_threshold_pct_raw"] is None
    assert r.json()["quota_gb"] == 200
    assert r.json()["protection_threshold_pct"] == 95
    aid = r.json()["id"]
    # 自定义：发额度+阈值，返回原始值与生效值一致
    r = client.put(f"/api/accounts/{aid}", json={
        "quota_gb": 300, "protection_threshold_pct": 80})
    assert r.status_code == 200, r.text
    assert r.json()["quota_gb_raw"] == 300
    assert r.json()["protection_threshold_pct_raw"] == 80
    assert r.json()["quota_gb"] == 300
    # 非法阈值：400 中文报错，不保存
    r = client.put(f"/api/accounts/{aid}", json={"protection_threshold_pct": 101})
    assert r.status_code == 400, r.text
    assert "0 到 100" in r.text
    from app import db as dbmod
    assert dbmod.get_account(aid)["protection_threshold_pct"] == 80
    # 切回跟随系统：发 NULL，原始值清空、生效值回到全局
    r = client.put(f"/api/accounts/{aid}", json={
        "quota_gb": None, "protection_threshold_pct": None})
    assert r.status_code == 200, r.text
    assert r.json()["quota_gb_raw"] is None
    assert r.json()["protection_threshold_pct_raw"] is None
    assert r.json()["quota_gb"] == 200
    assert r.json()["protection_threshold_pct"] == 95
