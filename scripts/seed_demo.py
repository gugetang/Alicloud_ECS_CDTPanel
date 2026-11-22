"""写入虚构演示数据。全部为示例值：example.xyz 域名、203.0.113.x 测试网段 IP、
占位 AccessKey，不含任何真实凭据/域名/IP。

分组规则：1 分组 = 1 域名，一个分组只选一种切换模式。
- 分组A（IP1、IP2）：backup.example.xyz，按负载均衡（差值 %/GB）
- 分组B（IP3）：www.example.xyz，按时间切换（间隔 6 小时）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db
from app.security import encrypt_secret

ACCOUNTS = [
    # name, site, region, ak, iid, used, status, ip, group_name
    ("AliCloud-HK-1", "global", "cn-hongkong", "LTAI5t-demo-0001", "i-demo000000000000001", 37.77, "Running", "203.0.113.10", "分组A"),
    ("AliCloud-HK-2", "global", "cn-hongkong", "LTAI5t-demo-0002", "i-demo000000000000002", 36.06, "Running", "203.0.113.11", "分组A"),
    ("AliYun-HK", "cn", "cn-hongkong", "LTAI5t-demo-0003", "i-demo000000000000003", 33.95, "Running", "203.0.113.12", "分组B"),
]
DOMAINS = [
    # name, zone, prio, group_name
    ("backup.example.xyz", "example.xyz", 0, "分组A"),
    ("www.example.xyz", "example.xyz", 1, "分组B"),
]


def main():
    db.init_db()
    groups = {}
    for gname, mode in (("分组A", "load_balance"), ("分组B", "time")):
        row = db.create_group({"name": gname, "switch_mode": mode,
                               "interval_hours": 6 if mode == "time" else None})
        groups[gname] = row["id"]
        print("group", row["id"], gname, mode)
    for i, (name, site, region, ak, iid, used, status, ip, gname) in enumerate(ACCOUNTS):
        row = db.create_account({
            "name": name, "site": site, "region": region, "access_key_id": ak,
            "access_key_secret_enc": encrypt_secret("demo-secret-placeholder"),
            "instance_id": iid, "priority": i, "group_id": groups[gname],
        })
        db.update_account(row["id"], {"traffic_used_gb": used,
                                      "instance_status": status, "public_ip": ip})
        print("account", row["id"], name, "->", gname)
    # 域名当前指向：每组内用量最低的可分配 IP（与调度结果一致），
    # 这样演示数据里就能看到绿点（在线服务中）与黄点（排队待命）
    current = {"backup.example.xyz": ("AliCloud-HK-2", "203.0.113.11"),
               "www.example.xyz": ("AliYun-HK", "203.0.113.12")}
    accounts_by_name = {a["name"]: a for a in db.list_accounts()}
    for name, zone, prio, gname in DOMAINS:
        row = db.create_domain({"name": name, "zone": zone, "priority": prio,
                                "group_id": groups[gname]})
        acc_name, ip = current[name]
        db.update_domain(row["id"], {
            "current_account_id": accounts_by_name[acc_name]["id"],
            "current_ip": ip})
        print("domain", row["id"], name, "->", gname, "指向", acc_name)
    db.set_settings({"fallback_address": "fallback.example.xyz"})
    print("seed done (demo data only)")


if __name__ == "__main__":
    main()
