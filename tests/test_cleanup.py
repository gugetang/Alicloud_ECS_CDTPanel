"""固定时间清理日志等没用的文件/缓存：

- settings.cleanup_time（默认 03:00），非法值归一化回 03:00；
- 调度器每天在 cleanup_time 到达后跑一次（到点前不跑、同一天不重复、
  第二天再跑），清理内容：过期事件/流量采样 + VACUUM + WAL 检查点 +
  数据目录临时文件（*.tmp、*.bak、*~）；
- POST /api/maintenance/cleanup 手动触发，/api/status 返回上次清理。
只用 tmpdb 隔离库 + TestClient，不碰真实 data/。
"""
import datetime as dt
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import scheduler
from app.settings import DEFAULT_SETTINGS

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


def _at(day, hh, mm):
    return dt.datetime(2026, 11, day, hh, mm, tzinfo=TZ)


def _old_event(dbmod, kind="test", detail="", days_old=100):
    """直接插一条 days_old 天前的事件（add_event 只会写当前时间）。"""
    conn = dbmod.connect()
    conn.execute(
        "INSERT INTO events(kind, title, detail, created_at) VALUES(?,?,?,?)",
        (kind, "old", detail, int(time.time()) - days_old * 86400))
    conn.commit()


def _events(dbmod):
    return dbmod.connect().execute("SELECT * FROM events").fetchall()


# ---------------- 清理时间归一化 ----------------

def test_cleanup_time_default_and_normalize(tmpdb):
    assert DEFAULT_SETTINGS["cleanup_time"] == "03:00"
    assert tmpdb.get_settings()["cleanup_time"] == "03:00"
    assert tmpdb.norm_cleanup_time("03:30") == "03:30"
    assert tmpdb.norm_cleanup_time("23:59") == "23:59"
    assert tmpdb.norm_cleanup_time("00:00") == "00:00"
    for bad in ("25:99", "99:99", "3:00", "", "  ", None, "abc", "03:60", "24:00"):
        assert tmpdb.norm_cleanup_time(bad) == "03:00", bad
    # 库里非法值（备份恢复带入）读出来也归一化
    tmpdb.set_settings({"cleanup_time": "xx"})
    assert tmpdb.get_settings()["cleanup_time"] == "03:00"


def test_cleanup_time_api_normalizes_instead_of_400(tmpdb):
    from app.api import _validate_settings
    data = {"cleanup_time": "99:99"}
    _validate_settings(data)
    assert data["cleanup_time"] == "03:00"
    data = {"cleanup_time": "04:30"}
    _validate_settings(data)
    assert data["cleanup_time"] == "04:30"


def test_cleanup_time_saved_via_api(tmpdb):
    client = TestClient(__import__("app.main", fromlist=["create_app"]).create_app())
    r = client.put("/api/settings", json={"cleanup_time": "04:30"})
    assert r.status_code == 200
    assert r.json()["cleanup_time"] == "04:30"
    # 非法值不报 400，归一化回 03:00
    r = client.put("/api/settings", json={"cleanup_time": "bad"})
    assert r.status_code == 200
    assert r.json()["cleanup_time"] == "03:00"


# ---------------- 调度器：到点才跑 / 同一天不重复 / 第二天再跑 ----------------

def test_cleanup_runs_only_after_time(tmpdb):
    _old_event(tmpdb)
    settings = {"cleanup_time": "03:00"}
    assert scheduler._maybe_cleanup_daily(settings, _at(23, 2, 59)) is False
    assert len(_events(tmpdb)) == 1  # 未到点不跑
    assert tmpdb.get_flag("cleanup_done") == ""
    assert scheduler._maybe_cleanup_daily(settings, _at(23, 3, 0)) is True
    assert len(_events(tmpdb)) == 1  # 旧事件被删，只剩刚记的 cleanup 事件
    assert _events(tmpdb)[0]["kind"] == "cleanup"
    assert tmpdb.get_flag("cleanup_done") == "2026-11-23"


def test_cleanup_once_per_day(tmpdb):
    settings = {"cleanup_time": "03:00"}
    assert scheduler._maybe_cleanup_daily(settings, _at(23, 3, 0)) is True
    _old_event(tmpdb)
    n_before = len(_events(tmpdb))
    assert scheduler._maybe_cleanup_daily(settings, _at(23, 4, 0)) is False
    assert len(_events(tmpdb)) == n_before  # 同一天不重复


def test_cleanup_next_day_runs_again(tmpdb):
    settings = {"cleanup_time": "03:00"}
    assert scheduler._maybe_cleanup_daily(settings, _at(23, 3, 0)) is True
    _old_event(tmpdb)
    assert scheduler._maybe_cleanup_daily(settings, _at(24, 3, 0)) is True
    assert tmpdb.get_flag("cleanup_done") == "2026-11-24"


def test_cleanup_uses_custom_time(tmpdb):
    _old_event(tmpdb)
    settings = {"cleanup_time": "05:30"}
    assert scheduler._maybe_cleanup_daily(settings, _at(23, 5, 29)) is False
    assert scheduler._maybe_cleanup_daily(settings, _at(23, 5, 30)) is True


# ---------------- run_cleanup：VACUUM / WAL 检查点 / 临时文件 ----------------

def test_run_cleanup_vacuum_shrinks(tmpdb, tmp_path):
    db_path = tmp_path / "t.db"
    now = int(time.time())
    conn = tmpdb.connect()
    for i in range(300):
        conn.execute(
            "INSERT INTO events(kind, title, detail, created_at) "
            "VALUES('test', ?, ?, ?)",
            (f"e{i}", "x" * 2000, now - 100 * 86400))
    conn.commit()
    # WAL 里的膨胀先落盘进主库，否则量出来的是小文件
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    size_before = db_path.stat().st_size
    assert size_before > 500_000
    result = tmpdb.run_cleanup()
    assert result["events_deleted"] >= 300
    assert result["db_size_before"] == size_before
    assert result["db_size_after"] < size_before
    assert result["db_size_reclaimed"] > 0
    assert db_path.stat().st_size == result["db_size_after"]


def test_run_cleanup_wal_checkpoint(tmpdb):
    result = tmpdb.run_cleanup()
    wal = result["wal_checkpoint"]
    assert wal["busy"] == 0
    assert wal["log"] == 0  # TRUNCATE 后 WAL 剩 0 页
    assert result["ran_at"]  # YYYY-MM-DD HH:MM
    assert tmpdb.get_flag("cleanup_last")  # 结果写进 flags 供前端显示


def test_run_cleanup_deletes_temp_files(tmpdb, tmp_path):
    (tmp_path / "a.tmp").write_text("tmp")
    (tmp_path / "b.bak").write_text("bak")
    (tmp_path / "c~").write_text("tilde")
    (tmp_path / "keep.txt").write_text("keep")
    result = tmpdb.run_cleanup()
    assert result["temp_files_deleted"] == 3
    assert sorted(result["temp_files"]) == ["a.tmp", "b.bak", "c~"]
    assert (tmp_path / "keep.txt").is_file()
    assert (tmp_path / "t.db").is_file()
    assert not (tmp_path / "a.tmp").exists()


# ---------------- 手动接口 + /api/status ----------------

def test_manual_cleanup_endpoint(tmpdb):
    from app.main import create_app
    client = TestClient(create_app())
    _old_event(tmpdb)
    r = client.post("/api/maintenance/cleanup", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["events_deleted"] >= 1
    assert "db_size_reclaimed" in body and "wal_checkpoint" in body
    # 手动清理也记当天已清理，定时清理当天不再重复
    assert tmpdb.get_flag("cleanup_done") == dt.datetime.now(TZ).strftime("%Y-%m-%d")
    # /api/status 返回上次清理
    r = client.get("/api/status")
    assert r.status_code == 200
    cleanup = r.json()["cleanup"]
    assert cleanup is not None
    assert cleanup["events_deleted"] >= 1
    assert cleanup["ran_at"]


def test_status_cleanup_none_before_first_run(tmpdb):
    from app.main import create_app
    client = TestClient(create_app())
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["cleanup"] is None


# ---------------- 前端：数据清理卡片 + i18n ----------------

def test_frontend_cleanup_card():
    root = Path(__file__).resolve().parent.parent
    tsx = (root / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    i18n = (root / "web" / "src" / "i18n.ts").read_text(encoding="utf-8")
    # 站点设置页有数据清理卡片：清理时间输入框（失焦保存）+ 上次清理 + 立即清理
    assert 't("数据清理")' in tsx
    assert 'type="time" value={cleanupTime}' in tsx
    assert 'saveSettings({ cleanup_time: cleanupTime || "03:00" })' in tsx
    assert 'status?.cleanup' in tsx
    assert 'post("/api/maintenance/cleanup"' in tsx
    assert 't("立即清理")' in tsx
    # 立即清理是红色图标按钮（icon-btn danger + 垃圾桶，Feather trash），不是文字按钮；悬停提示仍是“立即清理”
    assert '<button className="icon-btn danger" title={t("立即清理")} onClick={doCleanupNow} disabled={busy}>' in tsx
    assert '<button className="btn" onClick={doCleanupNow}>{t("立即清理")}</button>' not in tsx
    for key, en in [("数据清理", "Data cleanup"), ("清理时间", "Cleanup time"),
                    ("立即清理", "Clean up now"), ("尚未清理", "Not cleaned yet")]:
        assert key in i18n and en in i18n
