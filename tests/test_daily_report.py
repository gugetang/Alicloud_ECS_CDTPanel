"""每日推送：渲染（标题/日期/每账户 7 行/✅⚠️）、分条、
推送时间可配（默认 00:00）且每天一次、不重复不补推。
"""
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.notify import (DAILY_TITLE, build_daily_report, format_money)

TZ = ZoneInfo("Asia/Shanghai")


def acc(i, used=37.77, status="Running", cost=0.47, currency="USD",
        balance=0.0, spec="2C0.5G", evicted=False, dot="green", label="在线"):
    return {"id": i, "name": f"Demo-HK-{i}", "quota_gb": 200,
            "traffic_used_gb": used, "usage_rate": (round(used / 2, 2) if used is not None else None),
            "instance_status": status, "public_ip": f"203.0.113.{10 + i}",
            "instance_spec": spec, "instance_type": "",
            "billing_month_cost": cost, "billing_currency": currency,
            "billing_balance": balance,
            "billing_balance_currency": currency,
            "evicted": evicted, "status_dot": dot, "status_label": label,
            "protection_threshold_pct": 95}


def test_format_money():
    assert format_money(0.47, "USD") == "$0.47"
    assert format_money(3.5, "CNY") == "¥3.50"
    assert format_money(None, "USD") == "—"
    assert format_money(0, "USD") == "$0.00"


def test_render_structure_title_date_seven_lines():
    chunks = build_daily_report([acc(1), acc(2, spec="")], "2026-11-22")
    assert len(chunks) == 1
    text = chunks[0]["text"]
    assert text.startswith(f"{DAILY_TITLE}\n日期：2026-11-22")
    assert "<b>" in chunks[0]["html"] and "37.77 GB" in chunks[0]["html"]
    # 每账户 ≤7 行：按分隔线切块数行（第一块前面还带标题+日期两行）
    parts = text.split("\n\n──────\n\n")
    assert len(parts) == 2
    first_block = parts[0].split("\n\n", 1)[1]
    assert len([ln for ln in first_block.strip().split("\n") if ln.strip()]) == 7
    assert len([ln for ln in parts[1].strip().split("\n") if ln.strip()]) == 7
    # 名称行只写服务器名，不带规格括号（(2C0.5G) 已取消）；每行图标 + 标签：
    # 名称 👤 / 状态 💻 / IP 🌐 / 流量 📊 / 账单 💰 / 余额 💰 / 评价
    assert "👤 名称：Demo-HK-1" in text
    assert "Demo-HK-1 (" not in text
    assert "💻 状态：在线（🟢）" in text
    # 日报里的 IP 也只显示 A.B 段，C.D 用 * 代替，完整 IP 不出现在推送里
    assert "🌐 IP：203.0.*.*" in text
    assert "203.0.113.11" not in text
    assert "📊 流量：37.77 GB (18.89%)" in text
    assert "💰 账单：$0.47" in text
    assert "💰 余额：$0.00" in text
    assert "💬 评价：✅" in text
    assert "本月费用" not in text and "评论：" not in text
    assert "🌐 状态" not in text  # 🌐 已移到 IP 行，状态行改用 💻
    # 评价行也有图标 💬；HTML 版每行冒号后面的值加粗（标签不加粗）
    html = chunks[0]["html"]
    assert "👤 名称：<b>Demo-HK-1</b>" in html
    assert "💻 状态：<b>在线（🟢）</b>" in html
    assert "🌐 IP：<b>203.0.*.*</b>" in html
    assert "203.0.113.11" not in html
    assert "📊 流量：<b>37.77 GB (18.89%)</b>" in html
    assert "💰 账单：<b>$0.47</b>" in html
    assert "💰 余额：<b>$0.00</b>" in html
    assert "💬 评价：<b>✅</b>" in html


def test_comment_ok_and_warnings():
    ok = build_daily_report([acc(1)], "2026-11-22")[0]["text"]
    assert "评价：✅" in ok
    evicted = build_daily_report(
        [acc(1, used=192.0, evicted=True, dot="gray", label="已跳出")],
        "2026-11-22")[0]["text"]
    assert "评价：⚠️ 超阈值已跳出" in evicted
    evicted_html = build_daily_report(
        [acc(1, used=192.0, evicted=True, dot="gray", label="已跳出")],
        "2026-11-22")[0]["html"]
    assert "💬 评价：<b>⚠️ 超阈值已跳出" in evicted_html
    stopped = build_daily_report(
        [acc(1, status="Stopped", dot="red", label="已停机")], "2026-11-22")[0]["text"]
    assert "⚠️ 已停机" in stopped
    unknown = build_daily_report(
        [acc(1, used=None, dot="gray", label="未知")], "2026-11-22")[0]["text"]
    assert "⚠️ 流量未知" in unknown and "📊 流量：—（未知）" in unknown
    bad_cost = build_daily_report([acc(1, cost=-1.0)], "2026-11-22")[0]["text"]
    assert "⚠️ 费用异常" in bad_cost
    # 无费用/余额数据时显示 —，不是 $0.00 假数据，也不算异常
    nodata = build_daily_report([acc(1, cost=None, balance=None)], "2026-11-22")[0]["text"]
    assert "💰 账单：—" in nodata and "💰 余额：—" in nodata and "评价：✅" in nodata


def test_chunking_six_accounts_two_messages_max_five():
    chunks = build_daily_report([acc(i) for i in range(1, 7)], "2026-11-22")
    assert len(chunks) == 2
    assert [c["accounts"] for c in chunks] == [5, 1]
    assert "（第 1/2 部分）" in chunks[0]["text"]
    assert "（第 2/2 部分）" in chunks[1]["text"]
    for c in chunks:
        assert len(c["html"]) <= 4000 and len(c["text"]) <= 4000
    # 邮件 HTML 字号：标题 16px / 正文 14px / 辅助 12px
    assert "font-size:16px" in chunks[0]["email_html"]
    assert "font-size:14px" in chunks[0]["email_html"]
    assert "font-size:12px" in chunks[0]["email_html"]


# ---------------------------------------------------------------------------
# 调度器：推送时间可配（默认 00:00），每天一次，不重复不补推
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


def _at(day, hh, mm):
    return dt.datetime(2026, 11, 22 + day, hh, mm, tzinfo=TZ)


def _mk_account(tmpdb):
    a = tmpdb.create_account({"name": "IP1", "group_id": 1})
    tmpdb.update_account(a["id"], {"traffic_used_gb": 37.77,
                                   "instance_status": "Running",
                                   "public_ip": "203.0.113.10"})
    return a


def test_daily_default_0000_triggers_once(tmpdb):
    from app import scheduler
    _mk_account(tmpdb)
    s = tmpdb.get_settings()
    assert s["daily_report_time"] == "00:00" and s["daily_report_enabled"] == "1"
    # 上次检查在昨天 23:00，跨过当日 00:00 → 推；同一天第二次不推
    tmpdb.set_flag("daily_report_last_check",
                   _at(-1, 23, 0).isoformat())
    assert scheduler._maybe_send_daily_report(s, _at(0, 0, 0)) is True
    assert tmpdb.get_flag("daily_report_done:2026-11-22") != ""
    assert scheduler._maybe_send_daily_report(s, _at(0, 0, 30)) is False


def test_daily_custom_0830(tmpdb):
    from app import scheduler
    _mk_account(tmpdb)
    tmpdb.set_settings({"daily_report_time": "08:30"})
    s = tmpdb.get_settings()
    assert s["daily_report_time"] == "08:30"
    tmpdb.set_flag("daily_report_last_check", _at(0, 8, 0).isoformat())
    assert scheduler._maybe_send_daily_report(s, _at(0, 8, 29)) is False
    assert scheduler._maybe_send_daily_report(s, _at(0, 8, 30)) is True
    assert scheduler._maybe_send_daily_report(s, _at(0, 9, 0)) is False


def test_daily_change_time_after_it_passed_no_catchup(tmpdb):
    from app import scheduler
    _mk_account(tmpdb)
    s = tmpdb.get_settings()  # 默认 00:00
    # 09:00 检查：首次检查不补推，但记下检查时刻 09:00
    assert scheduler._maybe_send_daily_report(s, _at(0, 9, 0)) is False
    # 改成 08:30（新时刻已过）：10:00 检查不补推，等明天
    tmpdb.set_settings({"daily_report_time": "08:30"})
    s = tmpdb.get_settings()
    assert scheduler._maybe_send_daily_report(s, _at(0, 10, 0)) is False
    assert tmpdb.get_flag("daily_report_done:2026-11-22") == ""
    # 第二天 08:30：上次检查（昨天 10:00）跨过当日 08:30 → 推
    assert scheduler._maybe_send_daily_report(s, _at(1, 8, 30)) is True


def test_daily_disabled_and_no_accounts(tmpdb):
    from app import scheduler
    s = tmpdb.get_settings()
    tmpdb.set_flag("daily_report_last_check", _at(-1, 23, 0).isoformat())
    # 无账户：不推，也不记当天已推
    assert scheduler._maybe_send_daily_report(s, _at(0, 0, 0)) is False
    assert tmpdb.get_flag("daily_report_done:2026-11-22") == ""
    _mk_account(tmpdb)
    # 关闭开关：不推，但仍更新上次检查时刻（重开后不会立刻补推）
    tmpdb.set_settings({"daily_report_enabled": "0"})
    s = tmpdb.get_settings()
    assert scheduler._maybe_send_daily_report(s, _at(0, 0, 0)) is False
    assert tmpdb.get_flag("daily_report_last_check") == _at(0, 0, 0).isoformat()


def test_daily_sends_via_notifier_channels(tmpdb, monkeypatch):
    from app import scheduler
    _mk_account(tmpdb)
    calls: list = []

    class FakeNotifier:
        def __init__(self, settings):
            self.s = settings

        def send_daily_report(self, accounts, date_str, force=False,
                              skip_global_switch=False):
            calls.append((len(accounts), date_str, skip_global_switch))
            return ["telegram"]

    monkeypatch.setattr(scheduler, "Notifier", FakeNotifier)
    s = tmpdb.get_settings()
    tmpdb.set_flag("daily_report_last_check", _at(-1, 23, 0).isoformat())
    assert scheduler._maybe_send_daily_report(s, _at(0, 0, 0)) is True
    assert calls == [(1, "2026-11-22", True)]
    assert any(e["title"] == "每日财报已推送" for e in tmpdb.list_events(10))


def test_api_daily_preview_and_test(tmpdb):
    from app import scheduler
    from fastapi.testclient import TestClient
    from app.main import create_app
    client = TestClient(create_app())
    # 预览日期按调度时区（默认 Asia/Shanghai）取当天，不能拿系统本地
    # naive 时间去比：容器是 UTC，16:00–24:00 UTC 时上海已是第二天，
    # 旧断言 dt.datetime.now() 时好时坏。调用前后各取一次，容忍恰好
    # 跨过午夜的情况。
    before = scheduler._now().strftime("%Y-%m-%d")
    prev = client.get("/api/notify/daily-preview")
    after = scheduler._now().strftime("%Y-%m-%d")
    assert prev.status_code == 200
    body = prev.json()
    assert body["date"] in (before, after)
    # 预览标题跟随站点名称（测试库默认站点名 CDT 面板），名称行不带规格括号
    assert "[CDT 面板每日报告]" in body["text"] and "👤 名称：Demo-HK-1" in body["text"]
    assert "Demo-HK-1 (" not in body["text"]
    assert "评价：✅" in body["text"] and "评价：⚠️ 超阈值已跳出" in body["text"]
    # 无渠道启用时测试财报 sent 为空，但仍返回预览与条数
    r = client.post("/api/notify/test-daily")
    assert r.status_code == 200
    assert r.json()["sent"] == [] and r.json()["messages"] == 1
    assert r.json()["preview"]["accounts"] == 2


def test_daily_title_is_alicloud_report():
    """没传站点名时日报标题沿用 [AliCloud每日报告]（Telegram 文本、邮件
    HTML 均同标题）。"""
    assert DAILY_TITLE == "[AliCloud每日报告]"
    chunk = build_daily_report([acc(1)], "2026-11-22")[0]
    assert chunk["text"].startswith("[AliCloud每日报告]\n日期：2026-11-22")
    assert "[AliCloud每日报告]" in chunk["email_html"]
    assert "阿里云多账号·每日财报" not in chunk["text"]
    assert "阿里云多账号·每日财报" not in chunk["email_html"]


def test_daily_report_ip_masked():
    """日报 IP 只显示 A.B 段：完整 IP 不出现在文本、HTML、邮件 HTML 里；
    无 IP 显示 —，非 IPv4 原样返回。"""
    from app.notify import _mask_ip
    assert _mask_ip("") == "—" and _mask_ip(None) == "—"
    assert _mask_ip("203.0.113.10") == "203.0.*.*"
    assert _mask_ip("2001:db8::1") == "2001:db8::1"
    chunk = build_daily_report([acc(1)], "2026-11-22")[0]
    for key in ("text", "html", "email_html"):
        assert "203.0.*.*" in chunk[key] and "203.0.113.11" not in chunk[key]
    noip = build_daily_report([dict(acc(1), public_ip="")], "2026-11-22")[0]
    assert "🌐 IP：—" in noip["text"] and "🌐 IP：<b>—</b>" in noip["html"]


def test_daily_title_follows_site_name():
    """日报标题跟随站点名称：传了站点名就用 `[{站点名}每日报告]`，文本、
    HTML、邮件 HTML 三处同标题；空站点名回落 AliCloud。"""
    from app.notify import daily_title
    assert daily_title("") == "[AliCloud每日报告]"
    assert daily_title("  ") == "[AliCloud每日报告]"
    assert daily_title("CDT 面板") == "[CDT 面板每日报告]"
    chunk = build_daily_report([acc(1)], "2026-11-22", site_name="我的面板")[0]
    assert chunk["text"].startswith("[我的面板每日报告]\n日期：2026-11-22")
    assert "[我的面板每日报告]" in chunk["html"]
    assert "[我的面板每日报告]" in chunk["email_html"]
    assert "[AliCloud每日报告]" not in chunk["text"]


def test_manual_push_does_not_consume_scheduled(tmpdb):
    """手动推送不占用定时一次：先手动推，定时时间到了照常再推；
    定时推过后，当天仍可手动再推。"""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app import scheduler
    _mk_account(tmpdb)
    client = TestClient(create_app())
    tmpdb.set_flag("daily_report_last_check", _at(-1, 23, 0).isoformat())
    # 手动先推：成功，不写当天已推标志
    r = client.post("/api/notify/send-daily")
    assert r.status_code == 200
    assert r.json()["accounts"] == 1 and r.json()["sent"] == []
    assert "[CDT 面板每日报告]" in r.json()["preview"]["text"]
    assert tmpdb.get_flag("daily_report_done:2026-11-22") == ""
    assert any(e["title"] == "手动推送日报" for e in tmpdb.list_events(10))
    # 定时时间到了：照常再推一次
    s = tmpdb.get_settings()
    assert scheduler._maybe_send_daily_report(s, _at(0, 0, 0)) is True
    assert tmpdb.get_flag("daily_report_done:2026-11-22") != ""
    # 定时已推过，当天仍可手动再推
    r = client.post("/api/notify/send-daily")
    assert r.status_code == 200 and r.json()["accounts"] == 1


def test_manual_push_after_scheduled_next_day_still_scheduled(tmpdb):
    """定时已推过，当天手动仍可推；手动不影响第二天定时。"""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app import scheduler
    _mk_account(tmpdb)
    client = TestClient(create_app())
    tmpdb.set_flag("daily_report_last_check", _at(-1, 23, 0).isoformat())
    s = tmpdb.get_settings()
    assert scheduler._maybe_send_daily_report(s, _at(0, 0, 0)) is True
    r = client.post("/api/notify/send-daily")
    assert r.status_code == 200 and r.json()["accounts"] == 1
    # 第二天定时照常可推
    assert scheduler._maybe_send_daily_report(s, _at(1, 0, 0)) is True
