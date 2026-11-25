"""部署安全：只接受经过反代的请求（CDT_PROXY_ONLY）。

反代不一定在本机，所以不能把 1999 只绑 127.0.0.1（远端反代连不上）；
改为端口正常发布、应用层拦截直访：请求必须带反代转发标识头
（X-Forwarded-For / X-Forwarded-Proto / CF-Connecting-IP 之一），
直接用 IP:端口打开 → 403；经反代转发的请求 → 200。
本机回环（容器健康检查、install.sh 轮询）始终放行。
不开 CDT_PROXY_ONLY 时不限制（本地直接跑、全量测试默认状态）。

注意：中间件每次请求都读 settings.PROXY_ONLY，所以这里用 fixture
在每个测试里设置、用完恢复，不依赖模块导入顺序，也不会污染其它测试文件。
"""
import asyncio
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

# 测试用隔离数据库：必须在 import app.main 之前设环境变量
tmp = tempfile.mkdtemp()
os.environ["CDT_DATA_DIR"] = tmp
os.environ["CDT_NO_SCHEDULER"] = "1"

from app import settings  # noqa: E402
from app.main import _ProxyOnlyMiddleware, create_app  # noqa: E402


@pytest.fixture(autouse=True)
def _enable_proxy_only():
    prev = settings.PROXY_ONLY
    settings.PROXY_ONLY = True
    yield
    settings.PROXY_ONLY = prev


@pytest.fixture(scope="module")
def client():
    # TestClient 的客户端地址是 testclient（非回环），直访应被拦截。
    return TestClient(create_app(), base_url="http://1.2.3.4:1999")


def test_direct_ip_access_rejected(client):
    # 浏览器直接打开 IP:端口：没有反代转发标识头 → 403。
    r = client.get("/api/health")
    assert r.status_code == 403
    assert r.json() == {"detail": "请通过域名访问本站点或联系管理员"}


def test_forwarded_for_allowed(client):
    r = client.get("/api/health", headers={"X-Forwarded-For": "203.0.113.1"})
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_forwarded_proto_allowed(client):
    r = client.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert r.status_code == 200


def test_cf_connecting_ip_allowed(client):
    # Cloudflare 这类远端反代会带 CF-Connecting-IP。
    r = client.get("/api/health", headers={"CF-Connecting-IP": "203.0.113.1"})
    assert r.status_code == 200


def test_loopback_allowed():
    # 本机回环（容器健康检查、install.sh 轮询）不带转发头也放行。
    # TestClient 改不了客户端地址，这里直接调中间件，scope client 为 127.0.0.1。
    seen = {}

    async def dummy_app(scope, receive, send):
        seen["called"] = True
        await JSONResponse({"status": "ok"})(scope, receive, send)

    async def receive():
        return {"type": "http.request", "body": b""}

    sent = []

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": "GET", "path": "/api/health",
             "headers": [], "client": ("127.0.0.1", 12345)}
    asyncio.run(_ProxyOnlyMiddleware(dummy_app)(scope, receive, send))
    assert seen.get("called") is True


def test_proxy_only_off_allows_direct():
    # 不开 CDT_PROXY_ONLY 时不限制：本地直接跑、全量测试不受影响。
    settings.PROXY_ONLY = False
    c = TestClient(create_app(), base_url="http://1.2.3.4:1999")
    r = c.get("/api/health")
    assert r.status_code == 200
