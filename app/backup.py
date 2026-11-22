"""备份与恢复：整库导出 / 导入（JSON 文件）。

备份内容：settings、flags（含管理员密码哈希与 OAuth 绑定）、groups、
accounts（含 AES-GCM 加密后的 AK Secret）、domains、dns_records、
events（最近 500 条）、traffic_samples（最近 5000 条）、
webauthn_credentials，以及加密主密钥（data/master.key，base64）。

带上主密钥，备份才能在另一台机器/重装后恢复出可用的 AK Secret；
所以备份文件本身等同于全部凭据，请妥善保管，不要外发。
恢复会整体替换当前数据（先校验再写，一个事务内完成），恢复后
管理员密码也回到备份里的那一个；当前登录会话不受影响。
"""
from __future__ import annotations

import base64
import datetime as dt
import os

from . import db, settings as cfg

VERSION = 1
MAX_ROWS = {"events": 500, "traffic_samples": 5000}

COLUMNS: dict[str, list[str]] = {
    "groups": ["id", "name", "switch_mode", "interval_hours",
               "custom_interval_minutes", "interval_start_time",
               "fixed_switch_time",
               "night_window_start", "night_window_end",
               "night_optimize_enabled", "switch_diff_mode",
               "switch_diff_gb", "switch_diff_pct", "time_switch_mode",
               "exclusion_action", "recovery_action",
               "priority", "created_at"],
    "accounts": ["id", "name", "site", "region", "access_key_id",
                 "access_key_secret_enc", "instance_id", "quota_gb",
                 "protection_threshold_pct", "switch_diff_pct",
                 "switch_diff_mode", "switch_diff_gb", "exclusion_action",
                 "priority", "traffic_used_gb", "instance_status",
                 "public_ip", "billing_month_cost", "billing_currency",
                 "billing_balance", "billing_balance_currency",
                 "instance_type", "instance_spec",
                 "daily_schedule_enabled", "daily_on_time", "daily_off_time",
                 "daily_report", "recovery_action", "billing_updated_at",
                 "last_seen_at", "created_at", "group_id", "ddns_enabled"],
    "domains": ["id", "name", "zone", "priority", "record_type", "proxied",
                "ttl", "current_account_id", "current_ip", "fallback_active",
                "enabled", "created_at", "group_id"],
    "dns_records": ["id", "domain_id", "zone_id", "record_id", "ip",
                    "updated_at"],
    "events": ["id", "kind", "title", "detail", "account_id", "domain_id",
               "created_at"],
    "traffic_samples": ["id", "account_id", "used_gb", "month", "created_at"],
    "webauthn_credentials": ["id", "credential_id", "public_key",
                             "sign_count", "transports", "label",
                             "created_at"],
    "account_groups": ["account_id", "group_id"],
}

# 恢复顺序：先分组（域名/账户引用它），再账户、域名，其余随意
TABLE_ORDER = ["groups", "accounts", "domains", "dns_records",
               "events", "traffic_samples", "webauthn_credentials",
               "account_groups"]


def build_backup() -> dict:
    conn = db.connect()
    data: dict = {
        "version": VERSION,
        "app": cfg.APP_NAME,
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "settings": db.get_settings(),
        "flags": {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM flags")},
    }
    if cfg.MASTER_KEY_PATH.exists():
        raw = cfg.MASTER_KEY_PATH.read_bytes()
        if len(raw) >= 32:
            data["master_key_b64"] = base64.b64encode(raw[:32]).decode()
    for table in TABLE_ORDER:
        cols = COLUMNS[table]
        limit = MAX_ROWS.get(table)
        # account_groups 没有 id 列，按 account_id/group_id 排序
        order = "account_id, group_id" if table == "account_groups" else "id DESC"
        sql = f"SELECT {', '.join(cols)} FROM {table} ORDER BY {order}"
        if limit:
            sql += f" LIMIT {limit}"
        rows = [dict(r) for r in conn.execute(sql)]
        if order == "id DESC":
            rows.reverse()
        data[table] = rows
    return data


def _clean_rows(table: str, rows) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError(f"{table} 不是列表")
    cols = COLUMNS[table]
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{table} 里有一行不是对象")
        out.append({c: row.get(c) for c in cols if c in row})
    return out


def restore_backup(data: dict) -> dict:
    """校验并整体替换当前数据，返回各表恢复的行数。"""
    if not isinstance(data, dict):
        raise ValueError("备份文件不是 JSON 对象")
    if data.get("version") != VERSION:
        raise ValueError(f"不支持的备份版本：{data.get('version')}（需要 {VERSION}）")
    if not isinstance(data.get("settings"), dict):
        raise ValueError("备份缺少 settings")
    if not isinstance(data.get("flags"), dict):
        raise ValueError("备份缺少 flags")
    tables = {t: _clean_rows(t, data.get(t) or []) for t in TABLE_ORDER}
    for a in tables["accounts"]:
        if not a.get("name"):
            raise ValueError("备份里有账户缺少名称")
    settings = {str(k): str(v) for k, v in data["settings"].items()}
    flags = {str(k): str(v) for k, v in data["flags"].items()}

    key_b64 = data.get("master_key_b64") or ""
    key_bytes = None
    if key_b64:
        try:
            key_bytes = base64.b64decode(key_b64)
        except Exception:
            raise ValueError("备份里的主密钥不是合法 base64")
        if len(key_bytes) < 32:
            raise ValueError("备份里的主密钥长度不对")

    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table in TABLE_ORDER:
            conn.execute(f"DELETE FROM {table}")
            for row in tables[table]:
                cols = [c for c in COLUMNS[table] if c in row]
                if not cols:
                    continue
                conn.execute(
                    f"INSERT INTO {table}({', '.join(cols)}) "
                    f"VALUES({', '.join('?' * len(cols))})",
                    [row[c] for c in cols])
        conn.execute("DELETE FROM settings")
        for k, v in settings.items():
            conn.execute("INSERT INTO settings(key, value) VALUES(?, ?)", (k, v))
        conn.execute("DELETE FROM flags")
        for k, v in flags.items():
            conn.execute("INSERT INTO flags(key, value) VALUES(?, ?)", (k, v))
        # 兜底：默认分组被用户删过（flags.default_group_removed）时不再重建；
        # 否则保证默认分组存在，域名引用的分组不存在时归默认分组
        import time as _time
        if flags.get("default_group_removed") != "1":
            conn.execute(
                "INSERT OR IGNORE INTO groups(id, name, switch_mode, created_at) "
                "VALUES(1, '默认分组', 'load_balance', ?)", (int(_time.time()),))
            conn.execute(
                "UPDATE domains SET group_id=1 WHERE group_id IS NOT NULL AND "
                "group_id NOT IN (SELECT id FROM groups)")
        else:
            conn.execute(
                "UPDATE domains SET group_id=NULL WHERE group_id IS NOT NULL AND "
                "group_id NOT IN (SELECT id FROM groups)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if key_bytes:
        cfg.MASTER_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        cfg.MASTER_KEY_PATH.write_bytes(key_bytes[:32])
        try:
            os.chmod(cfg.MASTER_KEY_PATH, 0o600)
        except OSError:
            pass
    counts = {t: len(tables[t]) for t in TABLE_ORDER}
    counts["settings"] = len(settings)
    counts["flags"] = len(flags)
    return counts
