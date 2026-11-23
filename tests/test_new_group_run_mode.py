"""新建分组运行模式：跟随系统 / 定时轮巡 / 负载均衡（+切换模式）。

新建表单两级选择：运行模式（默认跟随系统），负载均衡时再选切换模式
（百分比/固定流量/动态模式/跟随系统，默认跟随系统）。后端 create_group
显式传 switch_mode=null 时保留 NULL（跟随系统），只有未传/空串时才默认
负载均衡。
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


def test_create_group_explicit_null_keeps_follow_system(tmpdb):
    g = tmpdb.create_group({"name": "跟随组", "switch_mode": None})
    assert g["switch_mode"] is None
    assert tmpdb.get_group(g["id"])["switch_mode"] is None


def test_create_group_missing_or_empty_defaults_load_balance(tmpdb):
    assert tmpdb.create_group({"name": "默认组"})["switch_mode"] == "load_balance"
    assert tmpdb.create_group({"name": "空串组", "switch_mode": ""})["switch_mode"] == "load_balance"


def test_create_group_time_and_dynamic(tmpdb):
    assert tmpdb.create_group({"name": "定时组", "switch_mode": "time"})["switch_mode"] == "time"
    g = tmpdb.create_group({"name": "动态组", "switch_mode": "dynamic"})
    assert g["switch_mode"] == "dynamic"
    assert g["switch_diff_mode"] is None
    assert g["switch_diff_pct"] is None
    assert g["switch_diff_gb"] is None


def test_create_group_invalid_mode_rejected(tmpdb):
    with pytest.raises(ValueError, match="只能是 time"):
        tmpdb.create_group({"name": "坏分组", "switch_mode": "both"})


def _client():
    return TestClient(create_app())


def test_api_create_group_follow_system(tmpdb):
    # 新建表单“跟随系统”发来的 payload：switch_mode=null，其余参数也为 null
    r = _client().post("/api/groups", json={
        "name": "API跟随组", "switch_mode": None,
        "switch_diff_mode": None, "switch_diff_pct": None,
        "switch_diff_gb": None, "dynamic_interval_minutes": None})
    assert r.status_code == 200, r.text
    assert r.json()["switch_mode"] is None
    assert tmpdb.get_group(r.json()["id"])["switch_mode"] is None


def test_api_create_group_load_balance_percent_with_global_diff(tmpdb):
    # 新建表单“负载均衡+百分比”发来的 payload：差值取全局值作初始值
    settings = tmpdb.get_settings()
    pct = float(settings.get("switch_diff_pct") or 10)
    gb = float(settings.get("switch_diff_gb") or 10)
    r = _client().post("/api/groups", json={
        "name": "API百分比组", "switch_mode": "load_balance",
        "switch_diff_mode": "percent", "switch_diff_pct": pct,
        "switch_diff_gb": gb, "dynamic_interval_minutes": None})
    assert r.status_code == 200, r.text
    row = tmpdb.get_group(r.json()["id"])
    assert row["switch_mode"] == "load_balance"
    assert row["switch_diff_mode"] == "percent"
    assert float(row["switch_diff_pct"]) == pct
    assert float(row["switch_diff_gb"]) == gb


def test_api_create_group_load_balance_dynamic(tmpdb):
    r = _client().post("/api/groups", json={
        "name": "API动态组", "switch_mode": "dynamic",
        "switch_diff_mode": None, "switch_diff_pct": None,
        "switch_diff_gb": None, "dynamic_interval_minutes": None})
    assert r.status_code == 200, r.text
    assert r.json()["switch_mode"] == "dynamic"


def test_api_create_group_time(tmpdb):
    r = _client().post("/api/groups", json={
        "name": "API定时组", "switch_mode": "time"})
    assert r.status_code == 200, r.text
    assert r.json()["switch_mode"] == "time"


def test_api_create_group_no_mode_defaults_load_balance(tmpdb):
    # 旧的必填校验不再误报：不传运行模式也能创建，默认负载均衡
    r = _client().post("/api/groups", json={"name": "API默认组"})
    assert r.status_code == 200, r.text
    assert r.json()["switch_mode"] == "load_balance"
