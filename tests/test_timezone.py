"""DDNS 设置时区可选（拉取菜单）：

- 默认时区 Asia/Shanghai（取环境变量 TZ，缺省 Asia/Shanghai）。
- GET /api/timezones 返回常用 IANA 时区列表（含 Asia/Shanghai）。
- PUT /settings 校验 timezone：非法 IANA 名返回 400。
- 调度器 _tz() 读 settings 表的 timezone；日报推送时间按所选时区判定。
"""
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

SH = ZoneInfo("Asia/Shanghai")


@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


def _mk_account(tmpdb):
    a = tmpdb.create_account({"name": "IP1", "group_id": 1})
    tmpdb.update_account(a["id"], {"traffic_used_gb": 37.77,
                                   "instance_status": "Running",
                                   "public_ip": "203.0.113.10"})
    return a


def test_default_timezone_is_shanghai(tmpdb):
    assert tmpdb.get_settings()["timezone"] == "Asia/Shanghai"


def test_timezones_endpoint_lists_shanghai(tmpdb):
    from app.api import TIMEZONES, list_timezones
    r = list_timezones(True)
    assert r["current"] == "Asia/Shanghai"
    assert r["timezones"] == TIMEZONES
    values = [t["value"] for t in TIMEZONES]
    for want in ("Asia/Shanghai", "Asia/Hong_Kong", "Asia/Taipei",
                 "Asia/Singapore", "Asia/Tokyo", "Asia/Seoul", "UTC",
                 "Europe/London", "Europe/Berlin", "Europe/Paris",
                 "America/New_York", "America/Chicago", "America/Denver",
                 "America/Los_Angeles", "Australia/Sydney"):
        assert want in values
    # 每项都有中文标签
    assert all(t.get("label") for t in TIMEZONES)


def test_put_settings_rejects_bad_timezone(tmpdb):
    from app.api import _validate_settings
    with pytest.raises(HTTPException) as ei:
        _validate_settings({"timezone": "Mars/Olympus"})
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException) as ei:
        _validate_settings({"timezone": "not-a-zone"})
    assert ei.value.status_code == 400
    # 合法时区不报错，且能保存
    _validate_settings({"timezone": "America/New_York"})
    tmpdb.set_settings({"timezone": "America/New_York"})
    assert tmpdb.get_settings()["timezone"] == "America/New_York"


def test_scheduler_tz_follows_setting(tmpdb):
    from app import scheduler
    assert scheduler._tz() == SH
    tmpdb.set_settings({"timezone": "America/New_York"})
    assert scheduler._tz() == ZoneInfo("America/New_York")
    assert scheduler._now().tzinfo == ZoneInfo("America/New_York")
    # 非法值（绕过 API 校验直接入库）回退环境变量 TZ，不崩溃
    tmpdb.set_settings({"timezone": "Mars/Olympus"})
    assert scheduler._tz() == SH


def test_daily_report_time_judged_by_selected_timezone(tmpdb):
    """同一瞬间：上海时间已过 00:00 推送时刻，UTC 时间还没过。
    推送时间 00:00，上次检查在前一天 23:00（上海）/ 15:00（UTC）。"""
    from app import scheduler
    _mk_account(tmpdb)
    s = tmpdb.get_settings()
    assert s["daily_report_time"] == "00:00"
    # 上海视角：now=11-22 00:05+08，跨过当日 00:00 → 推
    tmpdb.set_flag("daily_report_last_check",
                   dt.datetime(2026, 11, 21, 23, 0, tzinfo=SH).isoformat())
    assert scheduler._maybe_send_daily_report(
        s, dt.datetime(2026, 11, 22, 0, 5, tzinfo=SH)) is True
    assert tmpdb.get_flag("daily_report_triggered:2026-11-22") != ""
    # 同一瞬间换 UTC 视角：now=11-21 16:05Z，上次检查 15:00Z 在当日
    # 00:00 之后（未跨过推送时刻）→ 不推
    tmpdb.delete_flag("daily_report_triggered:2026-11-22")
    tmpdb.delete_flag("daily_report_triggered:2026-11-21")
    tmpdb.set_flag("daily_report_last_check",
                   dt.datetime(2026, 11, 21, 15, 0,
                               tzinfo=dt.timezone.utc).isoformat())
    assert scheduler._maybe_send_daily_report(
        s, dt.datetime(2026, 11, 21, 16, 5,
                       tzinfo=dt.timezone.utc)) is False
