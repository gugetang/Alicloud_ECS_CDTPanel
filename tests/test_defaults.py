"""默认值与阈值动作三选一：

- 新建账户不填停机模式/地域/站点 → exclusion_action=""（跟随全局）、
  region=cn-hongkong（中国香港）、site=global（国际站 USD）。
- 全局默认值：exclusion_action=none、threshold_recovery_action=start、
  api_refresh_minutes=5、daily_report_time=00:00。
- 账户级阈值动作：空=跟随全局（默认）；自定义时存 none/stop_charging/stop；
  全局改动只影响跟随全局的账户，自定义账户不受影响。
- 阈值动作三选一（radio）：none=踢出DDNS（默认，不停机）/ stop_charging=节省停机 /
  stop=停机；别名 evict_ddns 归一化为 none；三档互斥，只存一档的值。
- api_refresh_minutes 与旧 check_interval_minutes 合并为同一项。
"""
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.engine import (AccountView, Config, DomainView, execute_plan,
                        plan_reconcile)

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture()
def tmpdb(tmp_path):
    import app.db as dbmod
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    yield dbmod
    dbmod.close()


def acc(i, used, status="Running", ip="203.0.113.1"):
    return AccountView(id=i, name=f"A{i}", quota_gb=200, used_gb=used,
                       status=status, public_ip=f"203.0.113.{i}")


def dom(i, current=None, ip=""):
    return DomainView(id=i, name=f"www{i}.example.xyz", priority=i - 1,
                      current_account_id=current, current_ip=ip)


def noon():
    return dt.datetime(2026, 11, 22, 12, 0, tzinfo=TZ)


class FakeCloud:
    def __init__(self):
        self.calls = []

    def start(self, account):
        self.calls.append(("start", account.id))

    def stop(self, account, stop_charging):
        self.calls.append(("stop", account.id, stop_charging))


class FakeDns:
    def __init__(self):
        self.calls = []

    def set_record(self, domain, ip):
        self.calls.append((domain.name, ip))
        return True


# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------

def test_create_account_defaults_follow_global_hongkong(tmpdb):
    row = tmpdb.create_account({"name": "IP1", "group_id": 1})
    assert row["exclusion_action"] == ""  # 默认跟随全局（空=跟随全局）
    assert row["region"] == "cn-hongkong"     # 地域默认中国香港
    assert row["site"] == "global"            # 站点默认国际站（USD）
    assert row["daily_schedule_enabled"] == 0
    assert row["daily_on_time"] == "08:00" and row["daily_off_time"] == "20:00"
    assert row["daily_report"] == "follow"
    assert row["recovery_action"] in (None, "")


def test_global_defaults(tmpdb):
    s = tmpdb.get_settings()
    assert s["exclusion_action"] == "none"
    assert s["threshold_recovery_action"] == "start"
    assert s["api_refresh_minutes"] == "5"
    assert s["check_interval_minutes"] == "5"
    assert s["daily_report_time"] == "00:00"
    assert s["daily_report_enabled"] == "1"
    assert s["protection_threshold_pct"] == "95"


def test_old_rows_with_null_exclusion_normalized_to_none(tmp_path):
    # 旧库（建表时 exclusion_action 可空）里有 NULL 行时，
    # 再次 init_db 应把它规范化为 none（新库建表已是 NOT NULL DEFAULT 'none'）
    import app.db as dbmod
    p = Path(tmp_path) / "old.db"
    try:
        dbmod.close()
        conn = dbmod.connect(db_path=p)
        conn.executescript("""
            CREATE TABLE accounts(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              site TEXT NOT NULL DEFAULT 'global',
              region TEXT NOT NULL DEFAULT 'cn-hongkong',
              access_key_id TEXT NOT NULL DEFAULT '',
              access_key_secret_enc TEXT NOT NULL DEFAULT '',
              instance_id TEXT NOT NULL DEFAULT '',
              quota_gb REAL,
              protection_threshold_pct REAL,
              switch_diff_pct REAL,
              exclusion_action TEXT,
              priority INTEGER NOT NULL DEFAULT 0,
              traffic_used_gb REAL,
              instance_status TEXT DEFAULT '',
              public_ip TEXT DEFAULT '',
              billing_month_cost REAL,
              billing_currency TEXT DEFAULT '',
              created_at INTEGER NOT NULL
            );
            INSERT INTO accounts(name, exclusion_action, created_at)
            VALUES('IP1', NULL, 0);
            INSERT INTO accounts(name, exclusion_action, created_at)
            VALUES('IP2', '', 0);
        """)
        conn.commit()
        dbmod.close()
        dbmod.init_db(db_path=p)  # 迁移：补列 + NULL 行改成 none，'' 行保持跟随全局
        assert dbmod.get_account(1)["exclusion_action"] == "none"
        # 空字符串 '' = 跟随全局，是合法值，迁移绝不能把它改成 none
        assert dbmod.get_account(2)["exclusion_action"] == ""
    finally:
        dbmod.close()


# ---------------------------------------------------------------------------
# 阈值动作三选一：默认 none（踢出DDNS），不调停机
# ---------------------------------------------------------------------------

def test_threshold_default_is_evict_ddns_no_stop():
    cfg = Config()  # 不传 exclusion_action：默认 none
    assert cfg.exclusion_action == "none"
    accounts = [acc(1, 190), acc(2, 20)]
    plan = plan_reconcile(accounts, [dom(1, current=1, ip="203.0.113.1")],
                          cfg, noon())
    assert 1 in plan.evicted_account_ids
    assert any(a.kind == "dns" and a.reason == "threshold_eviction"
               for a in plan.actions)
    assert not [a for a in plan.actions if a.kind == "stop"]
    cloud, dns = FakeCloud(), FakeDns()
    execute_plan(plan, accounts, [dom(1, current=1)], cloud, dns, dry_run=False)
    assert dns.calls == [("www1.example.xyz", "203.0.113.2")]
    assert cloud.calls == []  # 默认不停机：一次 StopInstance 都不调


def test_threshold_stop_after_dns_switch():
    cfg = Config(exclusion_action="stop")
    accounts = [acc(1, 190), acc(2, 20)]
    plan = plan_reconcile(accounts, [dom(1, current=1, ip="203.0.113.1")],
                          cfg, noon())
    kinds = [a.kind for a in plan.actions]
    assert kinds.index("dns") < kinds.index("stop")  # 先切 DNS，再停机
    cloud, dns = FakeCloud(), FakeDns()
    res = execute_plan(plan, accounts, [dom(1, current=1)], cloud, dns,
                       dry_run=False)
    assert cloud.calls == [("stop", 1, False)]  # 普通停机
    dns_i = next(i for i, e in enumerate(res["executed"]) if e.startswith("dns:"))
    stop_i = next(i for i, e in enumerate(res["executed"]) if e.startswith("stop:"))
    assert dns_i < stop_i


def test_threshold_stop_charging_after_dns_switch():
    cfg = Config(exclusion_action="stop_charging")
    accounts = [acc(1, 190), acc(2, 20)]
    plan = plan_reconcile(accounts, [dom(1, current=1, ip="203.0.113.1")],
                          cfg, noon())
    kinds = [a.kind for a in plan.actions]
    assert kinds.index("dns") < kinds.index("stop")
    cloud, dns = FakeCloud(), FakeDns()
    execute_plan(plan, accounts, [dom(1, current=1)], cloud, dns, dry_run=False)
    assert cloud.calls == [("stop", 1, True)]  # 节省停机


# ---------------------------------------------------------------------------
# 三选一互斥 + 别名 evict_ddns 归一化（分组级）
# ---------------------------------------------------------------------------

def test_exclusion_group_only_last_wins(tmpdb):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.api import _public_account
    client = TestClient(create_app())
    row = tmpdb.create_account({"name": "IP1", "group_id": 1})
    r = client.put("/api/groups/1", json={"exclusion_action": "stop"})
    assert r.status_code == 200
    assert r.json()["exclusion_action"] == "stop"
    assert _public_account(tmpdb.get_account(row["id"]))["exclusion_action_effective"] == "stop"
    # 再选节省停机：只存后者，stop 自动取消
    r = client.put("/api/groups/1", json={"exclusion_action": "stop_charging"})
    assert r.status_code == 200
    assert r.json()["exclusion_action"] == "stop_charging"
    assert tmpdb.get_group(1)["exclusion_action"] == "stop_charging"
    assert _public_account(tmpdb.get_account(row["id"]))["exclusion_action_effective"] == "stop_charging"


def test_exclusion_alias_evict_ddns_normalized_to_none(tmpdb):
    # 分组级：别名归一化存 none
    row = tmpdb.create_group({"name": "G2", "exclusion_action": "evict_ddns"})
    assert row["exclusion_action"] == "none"
    tmpdb.update_group(row["id"], {"exclusion_action": "evict_ddns"})
    assert tmpdb.get_group(row["id"])["exclusion_action"] == "none"
    # 全局设置：别名归一化存 none
    from fastapi.testclient import TestClient
    from app.main import create_app
    client = TestClient(create_app())
    r = client.put("/api/settings", json={"exclusion_action": "evict_ddns"})
    assert r.status_code == 200
    assert r.json()["exclusion_action"] == "none"
    # 引擎：旧数据/旧调用里的 evict_ddns 也按 none 处理
    assert Config.from_settings({"exclusion_action": "evict_ddns"}).exclusion_action == "none"


def test_exclusion_invalid_value_rejected(tmpdb):
    from fastapi.testclient import TestClient
    from app.main import create_app
    client = TestClient(create_app())
    r = client.put("/api/settings", json={"exclusion_action": "explode"})
    assert r.status_code == 400
    with pytest.raises(ValueError):
        tmpdb.update_group(1, {"exclusion_action": "explode"})
    with pytest.raises(ValueError):
        tmpdb.update_group(1, {"recovery_action": "explode"})


def test_exclusion_account_field_ignored(tmpdb):
    """实例级阈值动作已移走：PUT /api/accounts 传 exclusion_action 不再生效，
    有效值仍由所属分组（跟随全局）决定。"""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.api import _public_account
    client = TestClient(create_app())
    row = tmpdb.create_account({"name": "IP1", "group_id": 1})
    r = client.put(f"/api/accounts/{row['id']}", json={"exclusion_action": "stop"})
    assert r.status_code == 200
    assert _public_account(tmpdb.get_account(row["id"]))["exclusion_action_effective"] == "none"


# ---------------------------------------------------------------------------
# 分组级阈值动作：跟随全局（默认）/ 自定义
# ---------------------------------------------------------------------------

def test_exclusion_group_follow_global_tracks_global(tmpdb):
    """分组默认跟随全局：全局改成 stop 后分组内账户有效值随之变 stop；
    全局改回 none 后又跟回 none。"""
    from app.api import _public_account
    row = tmpdb.create_account({"name": "IP1", "group_id": 1})
    assert tmpdb.get_group(1)["exclusion_action"] in (None, "")
    # 全局默认 none：跟随分组内账户有效值 none
    assert _public_account(tmpdb.get_account(row["id"]))["exclusion_action_effective"] == "none"
    # 全局改成 stop：跟随分组随之生效为 stop
    tmpdb.set_settings({"exclusion_action": "stop"})
    assert _public_account(tmpdb.get_account(row["id"]))["exclusion_action_effective"] == "stop"
    # 全局改回 none：跟随分组跟回 none
    tmpdb.set_settings({"exclusion_action": "none"})
    assert _public_account(tmpdb.get_account(row["id"]))["exclusion_action_effective"] == "none"


def test_exclusion_group_custom_none_unaffected_by_global(tmpdb):
    """自定义为 none 的分组不受全局改动影响。"""
    from app.api import _public_account
    row = tmpdb.create_account({"name": "IP1", "group_id": 1})
    tmpdb.update_group(1, {"exclusion_action": "none"})
    assert tmpdb.get_group(1)["exclusion_action"] == "none"
    tmpdb.set_settings({"exclusion_action": "stop"})
    body = _public_account(tmpdb.get_account(row["id"]))
    assert body["exclusion_action_effective"] == "none"


def test_exclusion_group_follow_can_be_set_back_via_api(tmpdb):
    """分组级自定义后可通过 API 改回跟随全局（传空字符串），改回后随全局走。"""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.api import _public_account
    client = TestClient(create_app())
    row = tmpdb.create_account({"name": "IP1", "group_id": 1})
    tmpdb.update_group(1, {"exclusion_action": "stop"})
    r = client.put("/api/groups/1", json={"exclusion_action": ""})
    assert r.status_code == 200
    assert r.json()["exclusion_action"] == ""
    tmpdb.set_settings({"exclusion_action": "stop_charging"})
    assert _public_account(tmpdb.get_account(row["id"]))["exclusion_action_effective"] == "stop_charging"


def test_exclusion_group_follow_survives_reinit(tmpdb, tmp_path):
    """跟随全局的 '' 在进程重启（再次 init_db）后仍是 ''，不会被迁移改成 none。"""
    import app.db as dbmod
    tmpdb.update_group(1, {"exclusion_action": ""})
    assert tmpdb.get_group(1)["exclusion_action"] == ""
    dbmod.close()
    dbmod.init_db(db_path=Path(tmp_path) / "t.db")
    assert dbmod.get_group(1)["exclusion_action"] == ""


def test_exclusion_effective_engine_level():
    """引擎层：账户级为空用全局值，否则用账户级值。"""
    from app.engine import _eff_exclusion
    cfg_stop = Config(exclusion_action="stop")
    follow = acc(1, 40)  # exclusion_action 默认 None=跟随全局
    assert _eff_exclusion(follow, cfg_stop) == "stop"
    custom = acc(2, 40)
    custom.exclusion_action = "none"
    assert _eff_exclusion(custom, cfg_stop) == "none"
    cfg_none = Config(exclusion_action="none")
    assert _eff_exclusion(follow, cfg_none) == "none"


# ---------------------------------------------------------------------------
# API 刷新时间合并
# ---------------------------------------------------------------------------

def test_api_refresh_minutes_merged_with_check_interval(tmpdb):
    tmpdb.set_settings({"api_refresh_minutes": "15"})
    s = tmpdb.get_settings()
    assert s["api_refresh_minutes"] == "15"
    assert s["check_interval_minutes"] == "15"  # 同步旧字段
    tmpdb.set_settings({"check_interval_minutes": "30"})
    s = tmpdb.get_settings()
    assert s["check_interval_minutes"] == "30"
    assert s["api_refresh_minutes"] == "30"  # 旧字段写入也同步新字段
    # 调度器读新字段
    from app import scheduler
    assert scheduler._refresh_minutes(s) == 30
    assert scheduler._refresh_minutes({"api_refresh_minutes": "abc"}) == 60
    # 旧库迁移：只存了 check_interval_minutes 时，新字段取旧值
    tmpdb.connect().execute("DELETE FROM settings WHERE key='api_refresh_minutes'")
    tmpdb.connect().commit()
    assert tmpdb.get_settings()["api_refresh_minutes"] == "30"
