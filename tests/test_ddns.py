import json

import pytest
import requests

from app.ddns import CloudflareClient


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
    def json(self):
        return self._payload
    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, records):
        self.records = records  # name -> content
        self.puts = []
    def get(self, url, headers=None, params=None, timeout=None):
        if url.endswith("/zones"):
            return FakeResp({"success": True, "result": [{"id": "zone1", "name": "example.xyz"}]})
        name = (params or {}).get("name")
        rec = self.records.get(name)
        return FakeResp({"success": True,
                         "result": [{"id": "rec1", "content": rec}] if rec else []})
    def put(self, url, headers=None, json=None, timeout=None):
        self.puts.append(json)
        # 回读时 records 已更新
        self.records[json["name"]] = json["content"]
        return FakeResp({"success": True, "result": json})


def test_noop_when_ip_unchanged():
    s = FakeSession({"www.example.xyz": "203.0.113.10"})
    cf = CloudflareClient("tok", session=s)
    res = cf.sync("www.example.xyz", "example.xyz", "203.0.113.10")
    assert res.status == "unchanged" and res.changed is False
    assert s.puts == []  # 零写请求


def test_put_when_ip_changed_and_verify():
    s = FakeSession({"www.example.xyz": "203.0.113.10"})
    cf = CloudflareClient("tok", session=s)
    res = cf.sync("www.example.xyz", "example.xyz", "203.0.113.99")
    assert res.status == "synced" and res.changed is True
    assert res.previous == "203.0.113.10"
    assert len(s.puts) == 1 and s.puts[0]["content"] == "203.0.113.99"


def test_invalid_ip_skipped():
    cf = CloudflareClient("tok", session=FakeSession({}))
    res = cf.sync("www.example.xyz", "example.xyz", "not-an-ip")
    assert res.status == "invalid_ip" and res.changed is False


class AccountFakeSession:
    """账户下有 kadi.eu.org 与 example.com 两个 Zone。"""
    def __init__(self):
        self.calls = []
    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(url)
        if "/accounts/" in url and url.endswith("/zones"):
            return FakeResp({"success": True,
                             "result": [{"id": "z-kadi", "name": "kadi.eu.org"},
                                        {"id": "z-ex", "name": "example.com"}],
                             "result_info": {"total_pages": 1}})
        if url.endswith("/zones"):
            return FakeResp({"success": True, "result": [{"id": "z-eu", "name": "eu.org"}]})
        return FakeResp({"success": True, "result": []})
    def put(self, url, headers=None, json=None, timeout=None):
        return FakeResp({"success": True, "result": json})


def test_account_id_longest_suffix_match():
    # 二级/三级域名：按账户查 Zone，取最长后缀匹配
    # premium.kadi.eu.org → kadi.eu.org（不是 eu.org）
    s = AccountFakeSession()
    cf = CloudflareClient("tok", session=s)
    assert cf.find_zone_id("eu.org", domain="premium.kadi.eu.org",
                           account_id="acct123") == "z-kadi"
    assert any("/accounts/acct123/zones" in u for u in s.calls)
    # 一级域名同样匹配
    assert cf.find_zone_id("example.com", domain="www.example.com",
                           account_id="acct123") == "z-ex"


def test_account_id_falls_back_to_name_lookup():
    # 账户下没有匹配的 Zone 时回退到按名查询（旧行为）
    s = AccountFakeSession()
    cf = CloudflareClient("tok", session=s)
    assert cf.find_zone_id("eu.org", domain="other.example.net",
                           account_id="acct123") == "z-eu"


def test_sync_passes_account_id():
    s = AccountFakeSession()
    cf = CloudflareClient("tok", session=s)
    # 账户方式找到 Zone 后，没有记录 → no_record（不抛错）
    res = cf.sync("premium.kadi.eu.org", "eu.org", "203.0.113.9",
                  account_id="acct123")
    assert res.status == "no_record"
    assert res.zone_id == "z-kadi"
