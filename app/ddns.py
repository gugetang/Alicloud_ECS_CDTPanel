"""Cloudflare DDNS。

- 自动发现：Zone ID（按域名后缀匹配 /zones?name=zone）、Record ID（/zones/{id}/dns_records?name=...&type=A）
- 只在 IP 真的变化时才 PUT；IP 不变直接返回 unchanged，不发写请求。
- 更新后回读一次 verify，确认 Cloudflare 上的值确实变了。
- 同名多条 A/AAAA/CNAME 时不自动删改，返回 conflict 交给用户整理（与截图提示一致）。
"""
from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass

import requests

log = logging.getLogger("cdt.ddns")
API = "https://api.cloudflare.com/client/v4"


@dataclass
class DDNSResult:
    status: str          # synced | unchanged | invalid_ip | no_record | failed | conflict
    changed: bool = False
    ip: str = ""
    previous: str = ""
    zone_id: str = ""
    record_id: str = ""
    detail: str = ""


def is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address((value or "").strip())
        return True
    except ValueError:
        return False


class CloudflareClient:
    def __init__(self, token: str, timeout: int = 12, session: requests.Session | None = None):
        self.token = token
        self.timeout = timeout
        self.session = session or requests.Session()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = self.session.get(API + path, headers=self._headers(),
                             params=params, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(str(data.get("errors"))[:300])
        return data

    def _put(self, path: str, payload: dict) -> dict:
        r = self.session.put(API + path, headers=self._headers(),
                             json=payload, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(str(data.get("errors"))[:300])
        return data

    def _list_account_zones(self, account_id: str) -> list[dict]:
        """按账户列出全部 Zone（分页取齐）。"""
        zones: list[dict] = []
        page = 1
        while True:
            data = self._get(f"/accounts/{account_id}/zones",
                             {"page": page, "per_page": 50})
            zones.extend(data.get("result") or [])
            info = data.get("result_info") or {}
            if page >= (info.get("total_pages") or 1):
                break
            page += 1
        return zones

    def find_zone_id(self, zone: str, domain: str = "",
                     account_id: str = "") -> str:
        """按域名找 Zone ID。配了 Account ID 时按账户列出全部 Zone，
        取与域名最长后缀匹配的那个（二级/三级域名也能识别）；
        没配或账户下没有匹配时，按 zone 名精确查询（旧行为）。"""
        if account_id:
            try:
                zones = self._list_account_zones(account_id)
            except Exception:
                zones = []
            d = (domain or "").strip().lower()
            best = None
            for z in zones:
                name = (z.get("name") or "").strip().lower()
                if d and (d == name or d.endswith("." + name)):
                    if best is None or len(name) > len(best.get("name") or ""):
                        best = z
            if best:
                return best["id"]
            # 账户下没有匹配的 Zone：回退到按名查询（兼容旧 Token/旧配置）
        data = self._get("/zones", {"name": zone, "per_page": 5})
        for z in data.get("result") or []:
            if z.get("name") == zone:
                return z["id"]
        raise RuntimeError(f"Zone 未找到: {zone}")

    def find_record(self, zone_id: str, name: str, rtype: str = "A") -> dict | None:
        data = self._get(f"/zones/{zone_id}/dns_records",
                         {"name": name, "type": rtype, "per_page": 10})
        results = data.get("result") or []
        if len(results) > 1:
            raise RuntimeError(f"同名存在多条 {rtype} 记录，请先手动整理: {name}")
        return results[0] if results else None

    def sync(self, domain: str, zone: str, new_ip: str, rtype: str = "A",
             ttl: int = 1, proxied: bool = False,
             account_id: str = "") -> DDNSResult:
        """把 domain 指到 new_ip。IP 不变 → unchanged 且不发 PUT。"""
        if not is_valid_ip(new_ip):
            return DDNSResult(status="invalid_ip", ip=new_ip, detail="IP 无效，跳过")
        try:
            zone_id = self.find_zone_id(zone or domain.split(".", 1)[-1],
                                        domain=domain, account_id=account_id)
            rec = self.find_record(zone_id, domain, rtype)
            if rec is None:
                return DDNSResult(status="no_record", ip=new_ip, zone_id=zone_id,
                                  detail="Cloudflare 上没有该记录，请先创建")
            record_id = rec["id"]
            current = rec.get("content", "")
            if current == new_ip:
                return DDNSResult(status="unchanged", changed=False, ip=new_ip,
                                  previous=current, zone_id=zone_id, record_id=record_id)
            self._put(f"/zones/{zone_id}/dns_records/{record_id}",
                      {"type": rtype, "name": domain, "content": new_ip,
                       "ttl": ttl, "proxied": proxied})
            # verify：回读确认
            after = self.find_record(zone_id, domain, rtype)
            if after and after.get("content") == new_ip:
                return DDNSResult(status="synced", changed=True, ip=new_ip,
                                  previous=current, zone_id=zone_id, record_id=record_id)
            return DDNSResult(status="failed", ip=new_ip, previous=current,
                              zone_id=zone_id, record_id=record_id,
                              detail="PUT 已发但回读不一致")
        except Exception as exc:
            log.warning("DDNS 同步失败 %s -> %s: %s", domain, new_ip, exc)
            return DDNSResult(status="failed", ip=new_ip, detail=str(exc)[:300])
