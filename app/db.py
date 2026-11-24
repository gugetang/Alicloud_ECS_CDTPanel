"""SQLite 存储（WAL）。表：groups / accounts / domains / settings / events /
traffic_samples / dns_records / jobs / flags。

groups: 分组（IP 池 + 最多 1 个域名 + 只选一种切换模式 time/load_balance，
  分组层无 both）。默认分组 id=1，模式 load_balance。
accounts: 一个阿里云账号 + 一台 ECS（与参考项目一致：1 账号 1 实例），
  group_id 指向所属分组（NULL=默认分组）；一个账户只属于一个分组。
  site: cn | global；阈值动作/恢复是分组级策略（groups.exclusion_action /
  recovery_action），空=跟随全局设置。
domains: 域名 + 优先级（priority 越小越先分配）+ 当前指向的账户，
  group_id 指向所属分组（NULL=默认分组）；1 分组最多 1 个域名
  （partial UNIQUE 索引 + 创建/更新时校验双重保障）。
settings: key/value，见 settings.DEFAULT_SETTINGS。
  旧的全局 switch_mode 只作新建分组的默认模式与无分组 legacy 回退，
  分组账户的有效模式以 groups.switch_mode 为准。
flags: 一次性状态（夜间已优化日期、固定时刻已切换日期、持续故障去重键；
  分组路径用 night_done:<gid> / fixed_done:<gid> / last_time_switch:<gid>）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

from . import settings as cfg

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = db_path or cfg.DB_PATH
        cfg.ensure_dirs()
        try:
            _conn = sqlite3.connect(str(path), check_same_thread=False, timeout=15)
        except sqlite3.OperationalError as e:
            # ensure_dirs 已确认目录可写，这里还失败通常是数据库文件本身
            # 不可写（如旧文件属主是 root）或路径被目录占用。
            raise RuntimeError(
                f"打不开数据库文件 {path}（{e}）：请确认该文件与所在目录对容器"
                "用户（UID 10001）可写；宿主机执行：sudo chown -R 10001:10001 "
                "/opt/docker/cdt-alicloud_ecs_cdtpanel，然后重启容器。"
            ) from e
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout=15000")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    conn = connect(db_path)
    with _lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS groups(
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              switch_mode TEXT DEFAULT 'load_balance'
                CHECK (switch_mode IN ('time', 'load_balance', 'dynamic')),
              interval_hours INTEGER,
              custom_interval_minutes INTEGER,
              interval_start_time TEXT,
              fixed_switch_time TEXT,
              night_window_start TEXT,
              night_window_end TEXT,
              night_optimize_enabled INTEGER,
              switch_diff_mode TEXT,
              switch_diff_gb REAL,
              switch_diff_pct REAL,
              time_switch_mode TEXT,
              exclusion_action TEXT,
              recovery_action TEXT,
              paused INTEGER NOT NULL DEFAULT 0,
              priority INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              dynamic_interval_minutes INTEGER
            );
            CREATE TABLE IF NOT EXISTS account_groups(
              account_id INTEGER NOT NULL,
              group_id INTEGER NOT NULL,
              PRIMARY KEY(account_id, group_id)
            );
            CREATE TABLE IF NOT EXISTS accounts(
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
              exclusion_action TEXT NOT NULL DEFAULT '',
              priority INTEGER NOT NULL DEFAULT 0,
              traffic_used_gb REAL,
              instance_status TEXT DEFAULT '',
              public_ip TEXT DEFAULT '',
              billing_month_cost REAL,
              billing_currency TEXT DEFAULT '',
              billing_balance REAL,
              billing_balance_currency TEXT DEFAULT '',
              instance_type TEXT DEFAULT '',
              instance_spec TEXT DEFAULT '',
              daily_schedule_enabled INTEGER NOT NULL DEFAULT 0,
              daily_on_time TEXT NOT NULL DEFAULT '08:00',
              daily_off_time TEXT NOT NULL DEFAULT '20:00',
              daily_report TEXT NOT NULL DEFAULT 'follow',
              recovery_action TEXT,
              billing_updated_at INTEGER DEFAULT 0,
              last_seen_at INTEGER DEFAULT 0,
              ddns_enabled INTEGER NOT NULL DEFAULT 1,
              api_refresh_minutes INTEGER,
              stop_reason TEXT NOT NULL DEFAULT '',
              stopped_at INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS domains(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL UNIQUE,
              zone TEXT NOT NULL DEFAULT '',
              priority INTEGER NOT NULL DEFAULT 0,
              record_type TEXT NOT NULL DEFAULT 'A',
              proxied INTEGER NOT NULL DEFAULT 0,
              ttl INTEGER NOT NULL DEFAULT 1,
              current_account_id INTEGER,
              current_ip TEXT DEFAULT '',
              fallback_active INTEGER NOT NULL DEFAULT 0,
              enabled INTEGER NOT NULL DEFAULT 1,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings(
              key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL, title TEXT NOT NULL, detail TEXT DEFAULT '',
              account_id INTEGER, domain_id INTEGER,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS traffic_samples(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              account_id INTEGER NOT NULL,
              used_gb REAL NOT NULL, month TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dns_records(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              domain_id INTEGER NOT NULL, zone_id TEXT DEFAULT '',
              record_id TEXT DEFAULT '', ip TEXT DEFAULT '',
              updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
              result_json TEXT DEFAULT '', created_at INTEGER NOT NULL,
              finished_at INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS flags(
              key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS webauthn_credentials(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              credential_id TEXT NOT NULL UNIQUE,
              public_key TEXT NOT NULL,
              sign_count INTEGER NOT NULL DEFAULT 0,
              transports TEXT NOT NULL DEFAULT '',
              label TEXT NOT NULL DEFAULT '',
              created_at INTEGER NOT NULL
            );
            """
        )
        # 轻量迁移：已建库追加新列（列已存在时报错可忽略）
        for ddl in (
            "ALTER TABLE accounts ADD COLUMN switch_diff_mode TEXT",
            "ALTER TABLE accounts ADD COLUMN switch_diff_gb REAL",
            "ALTER TABLE accounts ADD COLUMN group_id INTEGER",
            "ALTER TABLE domains ADD COLUMN group_id INTEGER",
            "ALTER TABLE accounts ADD COLUMN billing_balance REAL",
            "ALTER TABLE accounts ADD COLUMN billing_balance_currency TEXT",
            "ALTER TABLE accounts ADD COLUMN instance_type TEXT",
            "ALTER TABLE accounts ADD COLUMN instance_spec TEXT",
            "ALTER TABLE accounts ADD COLUMN daily_schedule_enabled INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE accounts ADD COLUMN daily_on_time TEXT NOT NULL DEFAULT '08:00'",
            "ALTER TABLE accounts ADD COLUMN daily_off_time TEXT NOT NULL DEFAULT '20:00'",
            "ALTER TABLE accounts ADD COLUMN daily_report TEXT NOT NULL DEFAULT 'follow'",
            "ALTER TABLE accounts ADD COLUMN recovery_action TEXT",
            "ALTER TABLE accounts ADD COLUMN ddns_enabled INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE accounts ADD COLUMN api_refresh_minutes INTEGER",
            # 停机原因标记（保活用，持久化）：阿里云 DescribeInstances 只返回
            # Running/Stopped，分不出“谁停的”。manual=面板手动停机、
            # threshold=阈值动作停机、schedule=定时关机，''=无标记（阿里云
            # 侧停机等未记录原因的情况，保活默认拉起）。stopped_at=标记时间。
            "ALTER TABLE accounts ADD COLUMN stop_reason TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE accounts ADD COLUMN stopped_at INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE groups ADD COLUMN priority INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE groups ADD COLUMN interval_start_time TEXT",
            "ALTER TABLE groups ADD COLUMN paused INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE groups ADD COLUMN exclusion_action TEXT",
            "ALTER TABLE groups ADD COLUMN recovery_action TEXT",
            "ALTER TABLE groups ADD COLUMN dynamic_interval_minutes INTEGER",
            "CREATE TABLE IF NOT EXISTS account_groups(account_id INTEGER NOT NULL, group_id INTEGER NOT NULL, PRIMARY KEY(account_id, group_id))",
            "INSERT OR IGNORE INTO account_groups(account_id, group_id) SELECT id, group_id FROM accounts WHERE group_id IS NOT NULL",
        ):
            try:
                conn.execute(ddl)
            except Exception:
                pass
        # 1 分组=1 域名：partial UNIQUE 索引（NULL 不参与，允许未分组域名）
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_domains_group_id_unique "
            "ON domains(group_id) WHERE group_id IS NOT NULL")
        # 动态切换模式（dynamic）：groups.switch_mode 的 CHECK 原先只允许
        # time/load_balance，旧库需重建表才能存 dynamic（SQLite 的 ALTER
        # TABLE 改不了 CHECK 约束）。新库建表时 CHECK 已含 dynamic，不会
        # 走到这里。重建在事务内完成、数据按列名全量复制；若 CHECK 已含
        # dynamic 则跳过。groups_new 残留表（上次重建失败时）先清掉。
        # 顶层“跟随系统”：switch_mode 为 NULL 即继承全局切换模式，旧库的
        # NOT NULL 约束也要重建表才能去掉（NULL 本身能通过 CHECK，不用改
        # CHECK）；新库建表时 switch_mode 已可空，不会走到这里。
        _gsql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='groups'"
        ).fetchone()
        if _gsql and ("'dynamic'" not in (_gsql[0] or "")
                      or "switch_mode TEXT NOT NULL" in (_gsql[0] or "")):
            _in_txn = conn.in_transaction
            try:
                if not _in_txn:
                    conn.execute("BEGIN")
                conn.execute("DROP TABLE IF EXISTS groups_new")
                conn.execute(
                    """CREATE TABLE groups_new(
                      id INTEGER PRIMARY KEY,
                      name TEXT NOT NULL,
                      switch_mode TEXT DEFAULT 'load_balance'
                        CHECK (switch_mode IN ('time', 'load_balance', 'dynamic')),
                      interval_hours INTEGER,
                      custom_interval_minutes INTEGER,
                      interval_start_time TEXT,
                      fixed_switch_time TEXT,
                      night_window_start TEXT,
                      night_window_end TEXT,
                      night_optimize_enabled INTEGER,
                      switch_diff_mode TEXT,
                      switch_diff_gb REAL,
                      switch_diff_pct REAL,
                      time_switch_mode TEXT,
                      exclusion_action TEXT,
                      recovery_action TEXT,
                      paused INTEGER NOT NULL DEFAULT 0,
                      priority INTEGER NOT NULL DEFAULT 0,
                      created_at INTEGER NOT NULL,
                      dynamic_interval_minutes INTEGER
                    )""")
                _gcols = [r[1] for r in conn.execute("PRAGMA table_info(groups)")]
                _collist = ", ".join(_gcols)
                conn.execute(
                    f"INSERT INTO groups_new({_collist}) "
                    f"SELECT {_collist} FROM groups")
                conn.execute("DROP TABLE groups")
                conn.execute("ALTER TABLE groups_new RENAME TO groups")
                if not _in_txn:
                    conn.execute("COMMIT")
            except Exception:
                if not _in_txn:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                raise
        # 默认分组（id=1，load_balance）：只有从未删过它、且库里一个分组都没有
        # 时才建立；用户删掉默认分组后不再自动重建（flags.default_group_removed
        # 记住这个选择，见 delete_group）。
        _removed = conn.execute(
            "SELECT value FROM flags WHERE key='default_group_removed'").fetchone()
        _n_groups = conn.execute("SELECT COUNT(*) c FROM groups").fetchone()["c"]
        if not _removed and _n_groups == 0:
            conn.execute(
                "INSERT OR IGNORE INTO groups(id, name, switch_mode, created_at) "
                "VALUES(1, '默认分组', 'load_balance', ?)", (int(time.time()),))
        # 旧数据迁移已移除：group_id 为 NULL 现在表示“未绑定”（添加域名时不再
        # 选分组），是合法状态，不再自动归默认分组或新建分组。
        # 旧库规范化：只有 NULL 行才改成 none（旧库建表时 exclusion_action
        # 可空，NULL 是无效值）。空字符串 '' 现在表示“跟随全局”，是合法值，
        # 必须保留、绝不能改成 none，否则跟随全局的账户会在重启后变回显式
        # none、再也跟不上全局改动。已有 'none' 账户保持原样（显式自定义，
        # 全局也是踢出DDNS 时行为与跟随全局相同）。
        conn.execute("UPDATE accounts SET exclusion_action='none' "
                     "WHERE exclusion_action IS NULL")
        for k, v in cfg.DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v)
            )
        conn.commit()
    return conn


def _row(r):
    return dict(r) if r is not None else None


def get_settings() -> dict[str, str]:
    conn = connect()
    out = dict(cfg.DEFAULT_SETTINGS)
    stored: dict[str, str] = {}
    with _lock:
        for r in conn.execute("SELECT key, value FROM settings"):
            stored[r["key"]] = r["value"]
    out.update(stored)
    # 清理时间归一化：库里非法值（如备份恢复带入的）读出来也按 03:00 算，
    # 调度器与前端永远拿到合法 HH:MM；写路径由 api._validate_settings 归一化。
    out["cleanup_time"] = norm_cleanup_time(out.get("cleanup_time"))
    # API 刷新时间合并迁移：旧库只存了 check_interval_minutes 时，
    # 把旧值作为 api_refresh_minutes（新字段），避免旧间隔丢失。
    if "api_refresh_minutes" not in stored and "check_interval_minutes" in stored:
        out["api_refresh_minutes"] = stored["check_interval_minutes"]
    return out


def set_settings(values: dict[str, str]) -> dict[str, str]:
    # api_refresh_minutes 与 check_interval_minutes 是同一项：保存任一时同步另一项，
    # 保证新旧代码读到的间隔一致。
    values = dict(values)
    if "api_refresh_minutes" in values:
        values["check_interval_minutes"] = values["api_refresh_minutes"]
    elif "check_interval_minutes" in values:
        values["api_refresh_minutes"] = values["check_interval_minutes"]
    conn = connect()
    with _lock:
        for k, v in values.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, str(v)),
            )
        conn.commit()
    return get_settings()


def get_flag(key: str) -> str:
    conn = connect()
    r = conn.execute("SELECT value FROM flags WHERE key=?", (key,)).fetchone()
    return r["value"] if r else ""


def set_flag(key: str, value: str) -> None:
    conn = connect()
    with _lock:
        conn.execute(
            "INSERT INTO flags(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def delete_flag(key: str) -> None:
    conn = connect()
    with _lock:
        conn.execute("DELETE FROM flags WHERE key=?", (key,))
        conn.commit()


def get_flags(keys: list[str]) -> dict[str, str]:
    """批量读 flags（一次查询）：列表接口/调度轮次一次取齐所需旗标，
    避免每账户、每分组各一次 get_flag 的 N+1。"""
    if not keys:
        return {}
    conn = connect()
    rows = conn.execute(
        "SELECT key, value FROM flags WHERE key IN (%s)"
        % ",".join("?" * len(keys)),
        tuple(keys)).fetchall()
    return {r["key"]: r["value"] for r in rows}


# 停机原因标记（保活用，见 init_db 迁移注释）：
# manual=面板手动停机、threshold=阈值动作停机、schedule=定时关机；
# ''=无标记。保活只排除 manual/threshold 两类。
STOP_REASONS = ("manual", "threshold", "schedule")
STOP_REASON_LABELS = {"manual": "手动停机", "threshold": "阈值停机",
                      "schedule": "定时关机"}
# 保活排除的停机原因：手动停机与阈值停机在排除期内不由保活拉起；
# 排除只到下一次 API 流量刷新为止，刷新显示用量在阈值内即解除。
KEEPALIVE_EXCLUDED_STOP_REASONS = ("manual", "threshold")


def set_stop_reason(aid: int, reason: str, now: int | None = None) -> None:
    """记停机原因（停机动作成功后调用）。reason 只能是 manual/threshold/
    schedule，非法值直接报错，不写库。"""
    if reason not in STOP_REASONS:
        raise ValueError(f"停机原因只能是 {STOP_REASONS}，收到 {reason!r}")
    conn = connect()
    with _lock:
        conn.execute(
            "UPDATE accounts SET stop_reason=?, stopped_at=? WHERE id=?",
            (reason, now if now is not None else int(time.time()), aid))
        conn.commit()


def clear_stop_reason(aid: int) -> None:
    """清停机原因标记（任何一次启动成功、或刷新发现实例已 Running 时调用；
    标记清空后保活恢复默认拉起）。"""
    conn = connect()
    with _lock:
        conn.execute(
            "UPDATE accounts SET stop_reason='', stopped_at=0 WHERE id=?", (aid,))
        conn.commit()


def list_evicted_account_ids() -> set[int]:
    """上一轮被踢出的账户 id（flags.evicted:<id>），一次查询。"""
    conn = connect()
    rows = conn.execute(
        "SELECT key FROM flags WHERE key LIKE 'evicted:%'").fetchall()
    out: set[int] = set()
    for r in rows:
        try:
            out.add(int(r["key"].split(":", 1)[1]))
        except (ValueError, IndexError):
            continue
    return out


# 数据保留期（调度循环每天清理一次，见 scheduler._maybe_cleanup_daily）：
# 事件留 90 天且最多 5000 条；流量采样留 90 天。
EVENTS_RETENTION_DAYS = 90
EVENTS_MAX_ROWS = 5000
TRAFFIC_SAMPLES_RETENTION_DAYS = 90

# 数据清理固定时间默认值（HH:MM）
CLEANUP_TIME_DEFAULT = "03:00"

# 数据目录临时文件后缀：清理时删除（只删这三种，logo/密钥/库文件不动）
CLEANUP_TEMP_SUFFIXES = (".tmp", ".bak")
CLEANUP_TEMP_SUFFIX = "~"


def norm_cleanup_time(value: object) -> str:
    """清理时间归一化：合法 HH:MM 原样返回（去空白），否则回默认值 03:00。"""
    v = str(value or "").strip()
    if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
        return v
    return CLEANUP_TIME_DEFAULT


def prune_old_data(now: int | None = None) -> dict:
    """清理过期事件与流量采样。事件：删 90 天前的，超过 5000 条时只留
    最新 5000 条；流量采样：删 90 天前的。返回删掉的行数。"""
    conn = connect()
    now = now if now is not None else int(time.time())
    with _lock:
        cur = conn.execute(
            "DELETE FROM events WHERE created_at < ?",
            (now - EVENTS_RETENTION_DAYS * 86400,))
        n_events_old = cur.rowcount
        cur = conn.execute(
            "DELETE FROM events WHERE id NOT IN "
            "(SELECT id FROM events ORDER BY id DESC LIMIT ?)",
            (EVENTS_MAX_ROWS,))
        n_events_over = cur.rowcount
        cur = conn.execute(
            "DELETE FROM traffic_samples WHERE created_at < ?",
            (now - TRAFFIC_SAMPLES_RETENTION_DAYS * 86400,))
        n_samples = cur.rowcount
        conn.commit()
    return {"events_deleted": n_events_old + n_events_over,
            "traffic_samples_deleted": n_samples}


def _db_file_path() -> Path:
    """当前主库文件路径（PRAGMA database_list，不依赖全局 DATA_DIR，
    测试用 tmpdb 时也指向临时库）。"""
    conn = connect()
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is not None and row[2]:
        return Path(row[2])
    return cfg.DB_PATH


def run_cleanup(now: int | None = None, ran_at: str | None = None) -> dict:
    """定时清理（每天固定时间一次，见 scheduler._maybe_cleanup_daily，
    也可由 POST /api/maintenance/cleanup 手动触发）：

    1. prune_old_data：删过期事件/流量采样；
    2. VACUUM：删行后 SQLite 文件不自动缩小，VACUUM 重建回收空间；
    3. PRAGMA wal_checkpoint(TRUNCATE)：把 WAL 落盘并截断，清 cdt.db-wal；
    4. 删除数据目录下的临时文件（*.tmp、*.bak、*~）。

    ran_at：显示用的“YYYY-MM-DD HH:MM”（调用方按设置时区传，见调度器
    与手动接口；直接调用时用本地时间）。结果写 flags.cleanup_last
    （JSON，供 /api/status 前端显示）并记一条 cleanup 事件；返回结果字典
    （含删行数、回收字节数、检查点、临时文件）。
    """
    now = now if now is not None else int(time.time())
    ran_at = ran_at or time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    pruned = prune_old_data(now=now)
    db_path = _db_file_path()
    size_before = db_path.stat().st_size if db_path.is_file() else 0
    conn = connect()
    with _lock:
        # 先提交可能挂起的事务：VACUUM 不能在事务内执行
        conn.commit()
        conn.execute("VACUUM")
        conn.commit()
        # WAL 模式下 VACUUM 的结果先落在 WAL 里，检查点落盘后主库文件才缩小，
        # 所以 size_after 必须在检查点之后再量
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        conn.commit()
        size_after = db_path.stat().st_size if db_path.is_file() else 0
    wal = {"busy": int(row[0]) if row else 0,
           "log": int(row[1]) if row else 0,
           "checkpointed": int(row[2]) if row else 0}
    # 临时文件：只删数据目录（库文件所在目录）下的 *.tmp / *.bak / *~
    tmp_deleted: list[str] = []
    data_dir = db_path.parent
    if data_dir.is_dir():
        for p in sorted(data_dir.iterdir()):
            if p.is_file() and (p.suffix in CLEANUP_TEMP_SUFFIXES
                                or p.name.endswith(CLEANUP_TEMP_SUFFIX)):
                try:
                    p.unlink()
                    tmp_deleted.append(p.name)
                except OSError:
                    pass
    result = {
        "ran_at": ran_at,
        "events_deleted": pruned["events_deleted"],
        "traffic_samples_deleted": pruned["traffic_samples_deleted"],
        "temp_files_deleted": len(tmp_deleted),
        "temp_files": tmp_deleted,
        "db_size_before": size_before,
        "db_size_after": size_after,
        "db_size_reclaimed": max(0, size_before - size_after),
        "wal_checkpoint": wal,
    }
    set_flag("cleanup_last", json.dumps(result, ensure_ascii=False))
    add_event("cleanup", "数据清理完成",
              f"事件 {result['events_deleted']} 条、流量采样 "
              f"{result['traffic_samples_deleted']} 条、临时文件 "
              f"{result['temp_files_deleted']} 个、回收 "
              f"{result['db_size_reclaimed']} 字节")
    return result


def add_event(kind: str, title: str, detail: str = "",
              account_id: int | None = None, domain_id: int | None = None) -> int:
    conn = connect()
    with _lock:
        cur = conn.execute(
            "INSERT INTO events(kind, title, detail, account_id, domain_id, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (kind, title, detail, account_id, domain_id, int(time.time())),
        )
        conn.commit()
        return cur.lastrowid


def list_events(limit: int = 100) -> list[dict]:
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_groups() -> list[dict]:
    conn = connect()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM groups ORDER BY priority ASC, id ASC").fetchall()]


def get_group(gid: int) -> dict | None:
    conn = connect()
    return _row(conn.execute("SELECT * FROM groups WHERE id=?", (gid,)).fetchone())


def _default_group_id() -> int | None:
    """默认分组 id：第一个已建分组（按 priority/id 排序）；没有分组时返回 None。

    注意：不要再把 1 硬编码为默认分组——用户可能删了 id=1 的默认分组
    （如只剩 Premium id=2），新建账户发 group_id=1 会直接 400
    “分组 1 不存在”，且前端自动保存静默吞错，用户看不到任何提示。
    """
    groups = list_groups()
    return groups[0]["id"] if groups else None


def _resolve_group_id(gid) -> int | None:
    """新建账户用的分组解析：给定的分组存在就用它；没给或不存在时落到
    第一个已建分组；一个分组都没有时返回 None（不绑分组，不报错）。"""
    if gid is not None:
        try:
            if get_group(int(gid)) is not None:
                return int(gid)
        except (TypeError, ValueError):
            pass
    return _default_group_id()


def _check_switch_mode(mode: str | None) -> str | None:
    # None = 跟随系统（继承全局切换模式，存 NULL）；空串/未填按旧行为默认
    # 负载均衡；其余非法值拒绝
    if mode is None:
        return None
    m = (mode or "load_balance").strip().lower()
    if m not in ("time", "load_balance", "dynamic"):
        raise ValueError(
            "分组切换模式只能是 time（定时轮巡）/ load_balance（负载均衡）"
            " / dynamic（动态切换）")
    return m


def _norm_dynamic_interval(data: dict) -> dict:
    """动态切换间隔（分钟）归一化：None/空/非法 = 不填（存 NULL，用默认 30）；
    小于 30 的钳到 30（动态切换最少 30 分钟一切换）；否则存正整数分钟。"""
    if "dynamic_interval_minutes" not in data:
        return data
    v = data.get("dynamic_interval_minutes")
    if v is None or v == "":
        return {**data, "dynamic_interval_minutes": None}
    try:
        n = int(v)
    except (TypeError, ValueError):
        return {**data, "dynamic_interval_minutes": None}
    return {**data, "dynamic_interval_minutes": max(30, n) if n > 0 else None}


def _check_group_exists(gid) -> None:
    if gid is None:
        return
    if get_group(int(gid)) is None:
        raise ValueError(f"分组 {gid} 不存在")


def _next_group_id(conn) -> int:
    """新建分组的 id：补最小空缺（删掉的 id 由后面的新建补上）。

    如只有 id=2，新建得 1；1、2、4 都有，新建得 3；都没有，得 1。
    分组数很少，直接读全部 id 在内存里找。"""
    used = {r[0] for r in conn.execute("SELECT id FROM groups").fetchall()}
    nid = 1
    while nid in used:
        nid += 1
    return nid


def create_group(data: dict) -> dict:
    data = _norm_group_threshold(data)
    data = _norm_dynamic_interval(data)
    conn = connect()
    now = int(time.time())
    cols = ["name", "switch_mode", "interval_hours", "custom_interval_minutes",
            "interval_start_time",
            "fixed_switch_time", "night_window_start", "night_window_end",
            "night_optimize_enabled", "switch_diff_mode", "switch_diff_gb",
            "switch_diff_pct", "time_switch_mode",
            "exclusion_action", "recovery_action", "paused", "priority",
            "dynamic_interval_minutes"]
    vals = [data.get(c) for c in cols]
    vals[0] = (data.get("name") or "").strip() or "未命名分组"
    # 新建未填模式默认负载均衡；显式传 switch_mode=null 时保留 NULL
    # （跟随系统），只有未传/空串时才默认负载均衡
    if "switch_mode" not in data or data.get("switch_mode") == "":
        vals[1] = "load_balance"
    else:
        vals[1] = _check_switch_mode(data.get("switch_mode"))
    # 停止状态归一化（新建默认运行中）
    vals[cols.index("paused")] = 1 if data.get("paused") else 0
    # 开始时间归一化（非法回退 00:00:00）
    if "interval_start_time" in cols:
        vals[cols.index("interval_start_time")] = _norm_start_time(data.get("interval_start_time"))
    # 新分组排最后（旧库已有分组 priority 全 0 时 max+1 也成立）
    _mx = conn.execute("SELECT COALESCE(MAX(priority), -1) FROM groups").fetchone()
    vals[cols.index("priority")] = (_mx[0] if _mx else -1) + 1
    # 创建分组时可选一个已添加的域名（一个域名只能被选取一次）：先校验该域名
    # 存在且未被其他分组选取，建组后直接绑过来
    domain_id = data.get("domain_id")
    with _lock:
        if domain_id not in (None, ""):
            row = conn.execute("SELECT group_id FROM domains WHERE id=?",
                               (int(domain_id),)).fetchone()
            if not row:
                raise ValueError("域名不存在")
            if row["group_id"] is not None:
                raise ValueError("该域名已被其他分组选取")
        # id 自动补位：删掉的 id 由后面的新建补上（只有 id=2 时新建得 1）。
        # 在锁里算 id、紧接着插，保证并发新建也不会撞 id。
        nid = _next_group_id(conn)
        conn.execute(
            f"INSERT INTO groups(id, {','.join(cols)}, created_at) "
            f"VALUES(?, {','.join('?' * len(cols))}, ?)", (nid, *vals, now))
        if domain_id not in (None, ""):
            conn.execute("UPDATE domains SET group_id=? WHERE id=?",
                         (nid, int(domain_id)))
        conn.commit()
        return get_group(nid)


def _norm_start_time(value) -> str | None:
    """开始时间归一化：HH:MM:SS（也接受 HH:MM），非法回退 00:00:00；
    None/空 = 不填（存 NULL，继承全局）。"""
    if value is None or value == "":
        return None
    v = value.strip() if isinstance(value, str) else "00:00:00"
    parts = v.split(":")
    try:
        if len(parts) == 2:
            h, m, s = int(parts[0]), int(parts[1]), 0
        elif len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        else:
            return "00:00:00"
    except ValueError:
        return "00:00:00"
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
        return "00:00:00"
    return f"{h:02d}:{m:02d}:{s:02d}"


def update_group(gid: int, data: dict) -> dict | None:
    conn = connect()
    data = _norm_group_threshold(data)
    data = _norm_dynamic_interval(data)
    allowed = ["name", "switch_mode", "interval_hours", "custom_interval_minutes",
               "interval_start_time",
               "fixed_switch_time", "night_window_start", "night_window_end",
               "night_optimize_enabled", "switch_diff_mode", "switch_diff_gb",
               "switch_diff_pct", "time_switch_mode",
               "exclusion_action", "recovery_action", "paused", "priority",
               "dynamic_interval_minutes"]
    sets = [(k, data[k]) for k in allowed if k in data]
    if any(k == "paused" for k, _ in sets):
        sets = [(k, (1 if v else 0) if k == "paused" else v) for k, v in sets]
    if any(k == "switch_mode" for k, _ in sets):
        sets = [(k, _check_switch_mode(v) if k == "switch_mode" else v)
                for k, v in sets]
    if any(k == "interval_start_time" for k, _ in sets):
        sets = [(k, _norm_start_time(v) if k == "interval_start_time" else v)
                for k, v in sets]
    if not sets:
        return get_group(gid)
    with _lock:
        conn.execute(
            "UPDATE groups SET " + ", ".join(f"{k}=?" for k, _ in sets) + " WHERE id=?",
            (*[v for _, v in sets], gid),
        )
        conn.commit()
    return get_group(gid)


def delete_group(gid: int) -> None:
    """删分组：默认分组也可删；还有域名引用时拒绝（先删/移走域名）。
    成员账户只解除该分组关系（主分组顺延到其余成员分组，没有则置空），
    账户本身不删。删默认分组后记 flags.default_group_removed，
    重启不再自动重建它。"""
    gid = int(gid)
    conn = connect()
    n_dom = conn.execute(
        "SELECT COUNT(*) c FROM domains WHERE group_id=?", (gid,)).fetchone()["c"]
    if n_dom:
        raise ValueError(
            f"该分组下还有 {n_dom} 个域名，"
            "请先删除或移走其域名再删除分组")
    with _lock:
        if gid == 1:
            conn.execute(
                "INSERT INTO flags(key, value) VALUES('default_group_removed', '1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'")
        members = [r["account_id"] for r in conn.execute(
            "SELECT account_id FROM account_groups WHERE group_id=?",
            (gid,)).fetchall()]
        conn.execute("DELETE FROM account_groups WHERE group_id=?", (gid,))
        for aid in members:
            rest = [r["group_id"] for r in conn.execute(
                "SELECT group_id FROM account_groups WHERE account_id=? "
                "ORDER BY group_id", (aid,)).fetchall()]
            conn.execute("UPDATE accounts SET group_id=? WHERE id=?",
                         (rest[0] if rest else None, aid))
        # 删组同时清掉该组的调度旗标（night_done:<gid> 等），否则新建
        # 分组补位拿到这个 id 时会继承旧组的“今晚已切过/上次切换时间”。
        for key in (f"night_done:{gid}", f"fixed_done:{gid}",
                    f"last_time_switch:{gid}"):
            conn.execute("DELETE FROM flags WHERE key=?", (key,))
        conn.execute("DELETE FROM groups WHERE id=?", (gid,))
        conn.commit()


def group_domain(gid: int) -> dict | None:
    """该分组已绑定的域名（1 分组=1 域名，最多 1 条）。"""
    conn = connect()
    return _row(conn.execute(
        "SELECT * FROM domains WHERE group_id=? ORDER BY id LIMIT 1", (gid,)).fetchone())


def check_domain_group_free(group_id, exclude_domain_id: int | None = None) -> None:
    """1 分组=1 域名校验：目标分组已被另一域名占用时报错。"""
    if group_id is None:
        return
    row = group_domain(int(group_id))
    if row and (exclude_domain_id is None or row["id"] != exclude_domain_id):
        raise ValueError(
            f"该分组已配置域名 {row['name']}，一个分组只能配置一个域名")


def list_accounts() -> list[dict]:
    conn = connect()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM accounts ORDER BY priority ASC, id ASC").fetchall()]


def get_account(aid: int) -> dict | None:
    conn = connect()
    return _row(conn.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone())


def get_account_group_ids(aid: int) -> list[int]:
    """账户所属的分组（多对多）：一个实例可同时属于多个分组。"""
    conn = connect()
    return [r["group_id"] for r in conn.execute(
        "SELECT group_id FROM account_groups WHERE account_id=? ORDER BY group_id",
        (aid,)).fetchall()]


def list_account_groups() -> list[dict]:
    conn = connect()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM account_groups ORDER BY account_id, group_id").fetchall()]


def set_account_groups(aid: int, gids: list[int]) -> None:
    """替换账户的分组关系（空列表=不属于任何分组）；主分组顺延为列表第一个，
    无成员时主分组置空。accounts.group_id 仍保留作主分组/回退。"""
    gids = [int(x) for x in dict.fromkeys(gids or [])]
    for gid in gids:
        _check_group_exists(gid)
    conn = connect()
    with _lock:
        conn.execute("DELETE FROM account_groups WHERE account_id=?", (aid,))
        for gid in gids:
            conn.execute(
                "INSERT INTO account_groups(account_id, group_id) VALUES(?, ?)",
                (aid, gid))
        conn.execute("UPDATE accounts SET group_id=? WHERE id=?",
                     (gids[0] if gids else None, aid))
        conn.commit()


def _check_account_name(name, group_id, exclude_id: int | None = None) -> str:
    """服务器名称：必填、1–64 字符、同一分组内不重名。"""
    n = (name or "").strip()
    if not n:
        raise ValueError("服务器名称不能为空")
    if len(n) > 64:
        raise ValueError("服务器名称最多 64 个字符")
    conn = connect()
    gid = group_id if group_id is not None else 1
    row = conn.execute(
        "SELECT id FROM accounts WHERE name=? AND COALESCE(group_id, 1)=?",
        (n, gid)).fetchone()
    if row and (exclude_id is None or row["id"] != exclude_id):
        raise ValueError(f"同一分组内已存在同名服务器 {n}")
    return n


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _check_daily_fields(data: dict, cur: dict | None = None) -> None:
    """校验每日定时开关机 / 日报参与 / 流量额度字段。

    更新时用旧行补全未传字段再校验（例如只改开关时间时仍要校验开≠关）。
    """
    def val(key):
        return data.get(key) if key in data else (cur.get(key) if cur else None)

    site = val("site")
    if site is not None and site not in ("cn", "global"):
        raise ValueError("站点只能是 cn（国内站）或 global（国际站）")
    region = val("region")
    if region is not None and not str(region).strip():
        raise ValueError("区域不能为空")
    q = val("quota_gb")
    if q is not None:
        try:
            if float(q) <= 0:
                raise ValueError("流量额度必须大于 0")
        except (TypeError, ValueError):
            raise ValueError("流量额度必须大于 0")
    th = val("protection_threshold_pct")
    if th is not None:
        try:
            if not (0 < float(th) <= 100):
                raise ValueError("流量保护阈值必须在 0 到 100 之间")
        except (TypeError, ValueError):
            raise ValueError("流量保护阈值必须在 0 到 100 之间")
    if val("daily_schedule_enabled"):
        on, off = val("daily_on_time"), val("daily_off_time")
        if not (_HHMM.match(str(on or "")) and _HHMM.match(str(off or ""))):
            raise ValueError("每日开关机时间必须是 HH:MM 格式")
        if on == off:
            raise ValueError("每日开机时间与关机时间不能相同")


def _norm_exclusion_action(data: dict) -> dict:
    """阈值动作别名归一化：evict_ddns 等价于 none（踢出DDNS），只存一档的值。"""
    if data.get("exclusion_action") == "evict_ddns":
        data = {**data, "exclusion_action": "none"}
    return data


def _norm_group_threshold(data: dict) -> dict:
    """分组级阈值策略归一化/校验：exclusion_action 别名 evict_ddns→none；
    recovery_action 只能是 ''/start/keep_stopped（空=跟随全局），非法报错；
    exclusion_action 只能是 ''/none/stop/stop_charging（空=跟随全局），非法报错。"""
    data = _norm_exclusion_action(data)
    excl = data.get("exclusion_action")
    if excl is not None and excl not in ("", "none", "stop", "stop_charging"):
        raise ValueError("阈值动作只能是 none/stop_charging/stop（evict_ddns=none）")
    ra = data.get("recovery_action")
    if ra is not None and ra not in ("", "start", "keep_stopped"):
        raise ValueError("阈值恢复操作只能是 start/keep_stopped（空=跟随全局）")
    return data


def _norm_refresh_minutes(data: dict) -> dict:
    """单账户 API 刷新时间（分钟）归一化：None/空/0/非法 = 跟随全局（存 NULL）；
    否则存正整数分钟。"""
    if "api_refresh_minutes" not in data:
        return data
    v = data.get("api_refresh_minutes")
    if v is None or v == "":
        return {**data, "api_refresh_minutes": None}
    try:
        n = int(v)
    except (TypeError, ValueError):
        return {**data, "api_refresh_minutes": None}
    return {**data, "api_refresh_minutes": n if n > 0 else None}


def create_account(data: dict) -> dict:
    data = _norm_refresh_minutes(data)
    # 新建时分组先解析再校验：前端新建默认发 group_id=1，但库里未必有
    # id=1 的分组（删过默认分组时只剩 Premium 之类），不存在的分组不再
    # 直接 400，改落到第一个已建分组；解析后再做重名与存在性校验。
    data = {**data, "group_id": _resolve_group_id(data.get("group_id"))}
    data = {**data, "name": _check_account_name(
        data.get("name"), data.get("group_id"))}
    _check_group_exists(data.get("group_id"))
    _check_daily_fields(data)
    conn = connect()
    now = int(time.time())
    cols = ["name", "site", "region", "access_key_id", "access_key_secret_enc",
            "instance_id", "quota_gb", "protection_threshold_pct",
            "switch_diff_pct", "switch_diff_mode", "switch_diff_gb",
            "priority", "group_id",
            "daily_schedule_enabled", "daily_on_time", "daily_off_time",
            "api_refresh_minutes"]
    _text = {"name", "site", "region", "access_key_id", "access_key_secret_enc",
             "instance_id",
             "daily_on_time", "daily_off_time"}
    _defaults = {"daily_schedule_enabled": 0, "daily_on_time": "08:00",
                 "daily_off_time": "20:00",
                 # 区域默认中国香港，站点默认国际站（USD）。
                 "region": "cn-hongkong", "site": "global"}
    vals = [data.get(c) if data.get(c) is not None
            else _defaults.get(c, "" if c in _text else 0 if c == "priority" else None)
            for c in cols]
    with _lock:
        cur = conn.execute(
            f"INSERT INTO accounts({','.join(cols)}, created_at) "
            f"VALUES({','.join('?' * len(cols))}, ?)", (*vals, now))
        conn.commit()
        # 新建账户归属其主分组（多对多关系的初始成员）
        _gid = data.get("group_id")
        if _gid is not None:
            conn.execute(
                "INSERT OR IGNORE INTO account_groups(account_id, group_id) "
                "VALUES(?, ?)", (cur.lastrowid, int(_gid)))
            conn.commit()
        return get_account(cur.lastrowid)


def update_account(aid: int, data: dict) -> dict | None:
    conn = connect()
    data = _norm_refresh_minutes(data)
    if "group_id" in data:
        _check_group_exists(data.get("group_id"))
    if "name" in data:
        cur = get_account(aid)
        gid = data.get("group_id", cur.get("group_id") if cur else None)
        data = {**data, "name": _check_account_name(data.get("name"), gid,
                                                    exclude_id=aid)}
    _check_daily_fields(data, cur=get_account(aid))
    allowed = ["name", "site", "region", "access_key_id", "access_key_secret_enc",
               "instance_id", "quota_gb", "protection_threshold_pct",
               "switch_diff_pct", "switch_diff_mode", "switch_diff_gb",
               "priority", "group_id",
               "daily_schedule_enabled", "daily_on_time", "daily_off_time",
               "api_refresh_minutes",
               "traffic_used_gb", "instance_status", "public_ip",
               "billing_month_cost", "billing_currency", "billing_balance",
               "billing_balance_currency", "instance_type", "instance_spec",
               "billing_updated_at",
               "last_seen_at", "ddns_enabled"]
    sets = [(k, data[k]) for k in allowed if k in data]
    if not sets and "group_ids" not in data:
        return get_account(aid)
    with _lock:
        if sets:
            conn.execute(
                "UPDATE accounts SET " + ", ".join(f"{k}=?" for k, _ in sets) + " WHERE id=?",
                (*[v for _, v in sets], aid),
            )
            conn.commit()
        # 单改主分组时也把它加进成员关系（不踢掉已有的其他分组）
        if "group_id" in data and data.get("group_id") is not None:
            conn.execute(
                "INSERT OR IGNORE INTO account_groups(account_id, group_id) "
                "VALUES(?, ?)", (aid, int(data["group_id"])))
            conn.commit()
    # group_ids：替换整个成员关系（多对多），主分组顺延为列表第一个
    if "group_ids" in data:
        set_account_groups(aid, [int(x) for x in (data.get("group_ids") or [])])
    return get_account(aid)


def delete_account(aid: int) -> None:
    conn = connect()
    with _lock:
        conn.execute("DELETE FROM accounts WHERE id=?", (aid,))
        conn.commit()


def list_domains() -> list[dict]:
    conn = connect()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM domains ORDER BY priority ASC, id ASC").fetchall()]


def get_domain(did: int) -> dict | None:
    conn = connect()
    return _row(conn.execute("SELECT * FROM domains WHERE id=?", (did,)).fetchone())


def create_domain(data: dict) -> dict:
    # 不指定分组 = 未绑定（group_id NULL）；指定分组时校验存在且空闲
    # （1 分组=1 域名，先校验，唯一索引是第二道防线）
    gid = data.get("group_id")
    if gid not in (None, ""):
        gid = int(gid)
        _check_group_exists(gid)
        check_domain_group_free(gid)
        data = {**data, "group_id": gid}
    else:
        data = {**data, "group_id": None}
    conn = connect()
    now = int(time.time())
    cols = ["name", "zone", "priority", "record_type", "proxied", "ttl", "enabled", "group_id"]
    vals = [data.get(c, 1 if c in ("ttl", "enabled") else 0 if c in ("proxied", "priority")
            else "" if c in ("zone",) else "A" if c == "record_type"
            else None) for c in cols]
    with _lock:
        try:
            cur = conn.execute(
                f"INSERT INTO domains({','.join(cols)}, created_at) "
                f"VALUES({','.join('?' * len(cols))}, ?)", (*vals, now))
            conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError("该分组已配置域名，一个分组只能配置一个域名")
        return get_domain(cur.lastrowid)


def update_domain(did: int, data: dict) -> dict | None:
    if "group_id" in data:
        gid = data.get("group_id")
        if gid in (None, ""):
            # 解除绑定：group_id 置 NULL（未分组）
            data = {**data, "group_id": None}
        else:
            data = {**data, "group_id": int(gid)}
            _check_group_exists(data.get("group_id"))
            check_domain_group_free(data.get("group_id"), exclude_domain_id=did)
    conn = connect()
    allowed = ["name", "zone", "priority", "record_type", "proxied", "ttl",
               "current_account_id", "current_ip", "fallback_active", "enabled",
               "group_id"]
    sets = [(k, data[k]) for k in allowed if k in data]
    if not sets:
        return get_domain(did)
    with _lock:
        try:
            conn.execute(
                "UPDATE domains SET " + ", ".join(f"{k}=?" for k, _ in sets) + " WHERE id=?",
                (*[v for _, v in sets], did),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError("该分组已配置域名，一个分组只能配置一个域名")
    return get_domain(did)


def bind_domain_to_group(did: int, gid: int) -> dict | None:
    """把域名绑到分组（1 分组=1 域名）：一个事务里先把该分组现有的域名解绑，
    再把目标域名绑过来；目标域名原来在别的分组也一并移过来。"""
    _check_group_exists(gid)
    conn = connect()
    with _lock:
        conn.execute(
            "UPDATE domains SET group_id=NULL WHERE group_id=? AND id<>?",
            (gid, did))
        conn.execute("UPDATE domains SET group_id=? WHERE id=?", (gid, did))
        conn.commit()
    return get_domain(did)


def delete_domain(did: int) -> None:
    conn = connect()
    with _lock:
        conn.execute("DELETE FROM domains WHERE id=?", (did,))
        conn.commit()


def upsert_dns_record(domain_id: int, zone_id: str, record_id: str, ip: str) -> None:
    conn = connect()
    with _lock:
        row = conn.execute(
            "SELECT id FROM dns_records WHERE domain_id=?", (domain_id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE dns_records SET zone_id=?, record_id=?, ip=?, updated_at=? WHERE id=?",
                (zone_id, record_id, ip, int(time.time()), row["id"]))
        else:
            conn.execute(
                "INSERT INTO dns_records(domain_id, zone_id, record_id, ip, updated_at)"
                " VALUES(?,?,?,?,?)",
                (domain_id, zone_id, record_id, ip, int(time.time())))
        conn.commit()


def get_dns_record(domain_id: int) -> dict | None:
    conn = connect()
    return _row(conn.execute(
        "SELECT * FROM dns_records WHERE domain_id=?", (domain_id,)).fetchone())


def add_traffic_sample(account_id: int, used_gb: float, month: str) -> None:
    conn = connect()
    with _lock:
        conn.execute(
            "INSERT INTO traffic_samples(account_id, used_gb, month, created_at)"
            " VALUES(?,?,?,?)", (account_id, used_gb, month, int(time.time())))
        conn.commit()


def get_latest_traffic_sample_at(account_id: int) -> int | None:
    """该账户最新一条流量采样的 created_at（unix 秒），无采样返回 None。
    B-04 用：24 小时兜底恢复只在流量数据新鲜时才触发。"""
    conn = connect()
    row = conn.execute(
        "SELECT MAX(created_at) FROM traffic_samples WHERE account_id=?",
        (account_id,)).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def create_job(kind: str) -> int:
    conn = connect()
    with _lock:
        cur = conn.execute(
            "INSERT INTO jobs(kind, created_at) VALUES(?,?)", (kind, int(time.time())))
        conn.commit()
        return cur.lastrowid


def finish_job(job_id: int, status: str, result: dict) -> None:
    conn = connect()
    with _lock:
        conn.execute(
            "UPDATE jobs SET status=?, result_json=?, finished_at=? WHERE id=?",
            (status, json.dumps(result, ensure_ascii=False), int(time.time()), job_id))
        conn.commit()


def get_job(job_id: int) -> dict | None:
    conn = connect()
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["result"] = json.loads(d.pop("result_json") or "{}")
    except Exception:
        d["result"] = {}
    return d


def list_webauthn_credentials() -> list[dict]:
    conn = connect()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM webauthn_credentials ORDER BY id ASC").fetchall()]


def add_webauthn_credential(credential_id: str, public_key: str,
                            sign_count: int = 0, transports: str = "",
                            label: str = "") -> dict:
    conn = connect()
    with _lock:
        cur = conn.execute(
            "INSERT INTO webauthn_credentials"
            "(credential_id, public_key, sign_count, transports, label, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (credential_id, public_key, sign_count, transports, label,
             int(time.time())))
        conn.commit()
        row = conn.execute(
            "SELECT * FROM webauthn_credentials WHERE id=?",
            (cur.lastrowid,)).fetchone()
        return dict(row)


def update_webauthn_sign_count(credential_id: str, sign_count: int) -> None:
    conn = connect()
    with _lock:
        conn.execute(
            "UPDATE webauthn_credentials SET sign_count=? WHERE credential_id=?",
            (sign_count, credential_id))
        conn.commit()


def delete_webauthn_credential(credential_id: str) -> None:
    conn = connect()
    with _lock:
        conn.execute(
            "DELETE FROM webauthn_credentials WHERE credential_id=?",
            (credential_id,))
        conn.commit()
