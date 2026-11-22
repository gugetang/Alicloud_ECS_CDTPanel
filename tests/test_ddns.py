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

