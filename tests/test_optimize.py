"""本次优化的回归测试：批量 flag 读取、数据清理、并行刷新、regions POST。

只用 tmpdb 隔离库 + monkeypatch 假客户端，不碰真实阿里云接口。
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


def test_get_flags_batch(tmpdb):
    tmpdb.set_flag("night_done:1", "2026-11-22")
    tmpdb.set_flag("fixed_done:1", "2026-11-22")
    tmpdb.set_flag("other", "x")
    got = tmpdb.get_flags(["night_done:1", "fixed_done:1", "missing"])
    assert got == {"night_done:1": "2026-11-22", "fixed_done:1": "2026-11-22"}
    assert tmpdb.get_flags([]) == {}


def test_list_evicted_account_ids(tmpdb):
    tmpdb.set_flag("evicted:2", "2026-11-22T10:00:00")
    tmpdb.set_flag("evicted:5", "2026-11-22T10:00:00")
    tmpdb.set_flag("evicted:notanid", "x")
    tmpdb.set_flag("night_done:1", "2026-11-22")
    assert tmpdb.list_evicted_account_ids() == {2, 5}


def test_prune_old_data(tmpdb):
    import time
    now = int(time.time())
    old = now - 100 * 86400
    conn = tmpdb.connect()
    for i in range(3):
        conn.execute(
            "INSERT INTO events(kind, title, created_at) VALUES('info', ?, ?)",
            (f"old-{i}", old))
    for i in range(2):
        conn.execute(
            "INSERT INTO events(kind, title, created_at) VALUES('info', ?, ?)",
            (f"new-{i}", now))
    conn.execute(
        "INSERT INTO traffic_samples(account_id, used_gb, month, created_at)"
        " VALUES(1, 10.0, '2026-08', ?)", (old,))
    conn.execute(
        "INSERT INTO traffic_samples(account_id, used_gb, month, created_at)"
        " VALUES(1, 20.0, '2026-11', ?)", (now,))
    conn.commit()
    out = tmpdb.prune_old_data(now=now)
    assert out["events_deleted"] == 3
    assert out["traffic_samples_deleted"] == 1
    rows = conn.execute("SELECT title FROM events ORDER BY id").fetchall()
    assert [r["title"] for r in rows] == ["new-0", "new-1"]
    rows = conn.execute("SELECT used_gb FROM traffic_samples").fetchall()
    assert [r["used_gb"] for r in rows] == [20.0]


def test_prune_events_cap(tmpdb, monkeypatch):
    import time
    now = int(time.time())
    monkeypatch.setattr(tmpdb, "EVENTS_MAX_ROWS", 3)
    conn = tmpdb.connect()
    for i in range(5):
        conn.execute(
            "INSERT INTO events(kind, title, created_at) VALUES('info', ?, ?)",
            (f"e{i}", now))
    conn.commit()
    out = tmpdb.prune_old_data(now=now)
    assert out["events_deleted"] == 2
    rows = conn.execute("SELECT title FROM events ORDER BY id").fetchall()
    assert [r["title"] for r in rows] == ["e2", "e3", "e4"]


def test_public_account_batched_matches_default(tmpdb):
    from app.api import _public_account
    row = tmpdb.create_account({"name": "A1"})
    tmpdb.update_account(row["id"], {"traffic_used_gb": 37.77,
                                     "instance_status": "Running"})
    settings = tmpdb.get_settings()
    evicted = tmpdb.list_evicted_account_ids()
    a = _public_account(tmpdb.get_account(row["id"]))
    b = _public_account(tmpdb.get_account(row["id"]), settings=settings,
                        evicted_ids=evicted)
    for k in ("usage_rate", "evicted", "status_dot", "status_label",
              "exclusion_action_effective", "protection_threshold_pct"):
        assert a[k] == b[k], k
    # 已踢出账户：批量传入的 evicted_ids 同样生效
    tmpdb.set_flag(f"evicted:{row['id']}", "2026-11-22T10:00:00")
    tmpdb.update_account(row["id"], {"traffic_used_gb": 190.0})
    evicted = tmpdb.list_evicted_account_ids()
    c = _public_account(tmpdb.get_account(row["id"]), settings=settings,
                        evicted_ids=evicted)
    assert c["evicted"] is True
    assert c["status_dot"] == "gray"


def test_refresh_cloud_status_parallel(tmpdb, monkeypatch):
    import app.scheduler as sched
    seen = []
    for name in ("A1", "A2", "A3"):
        tmpdb.create_account({"name": name})
    accounts = tmpdb.list_accounts()
    assert len(accounts) == 3

    def fake_refresh(a, settings):
        seen.append(a["id"])

    monkeypatch.setattr(sched, "_refresh_one", fake_refresh)
    out = sched.refresh_cloud_status(tmpdb.get_settings())
    assert sorted(seen) == sorted(a["id"] for a in accounts)
    assert len(out) == 3


def test_regions_post_not_get(tmpdb):
    from fastapi.testclient import TestClient
    from app.main import create_app
    client = TestClient(create_app())
    # POST + JSON body：无凭据时应报 400（路由存在、body 正常解析）
    r = client.post("/api/aliyun/regions", json={"site": "global"})
    assert r.status_code == 400
    assert "请先选择账户" in r.json()["detail"]
    # 旧的 GET 已关闭：密钥不再能走 URL 查询参数
    r = client.get("/api/aliyun/regions?site=global")
    assert r.status_code == 405
