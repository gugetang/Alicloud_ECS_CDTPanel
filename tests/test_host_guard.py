"""部署安全：应用层 Host 白名单（CDT_PUBLIC_DOMAIN）。

配了域名才生效：只认该域名 Host，IP / 其它 Host 直接 403；
本机回环（健康检查）始终放行；反代透传 X-Forwarded-Proto=http 也拒绝。
不配域名时不限制（本地开发、旧部署保持原样，见 test_api.py 的全量用例）。

注意：中间件每次请求都读 settings.PUBLIC_DOMAIN，所以这里用 fixture
在每个测试里设置、用完恢复，不依赖模块导入顺序，也不会污染其它测试文件。
"""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# 测试用隔离数据库：必须在 import app.main 之前设环境变量
tmp = tempfile.mkdtemp()
os.environ["CDT_DATA_DIR"] = tmp
os.environ["CDT_NO_SCHEDULER"] = "1"

from app import settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def _enable_host_guard():
    prev = settings.PUBLIC_DOMAIN
    settings.PUBLIC_DOMAIN = "panel.example.test"
    yield
    settings.PUBLIC_DOMAIN = prev


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(), base_url="https://panel.example.test")


def test_domain_host_allowed(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_domain_host_with_forwarded_https_allowed(client):
    r = client.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert r.status_code == 200


def test_ip_host_rejected():
    c = TestClient(create_app(), base_url="http://1.2.3.4:1999")
    r = c.get("/api/health")
    assert r.status_code == 403


def test_other_domain_rejected():
    c = TestClient(create_app(), base_url="https://other.example.com")
    r = c.get("/api/health")
    assert r.status_code == 403


def test_loopback_healthcheck_allowed():
    # 容器健康检查走 127.0.0.1，不受白名单影响。
    c = TestClient(create_app(), base_url="http://127.0.0.1:1999")
    r = c.get("/api/health")
    assert r.status_code == 200


def test_forwarded_http_rejected(client):
    # 正常流量经用户自己的反代只会是 https；明文 http 透传过来直接拒绝。
    r = client.get("/api/health", headers={"X-Forwarded-Proto": "http"})
    assert r.status_code == 403
