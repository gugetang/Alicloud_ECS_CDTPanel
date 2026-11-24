"""面板在线更新：版本检查（GitHub API + 缓存 + 失败容错）、Docker 重建
参数拼装、没有 socket 时返回手动命令、接口都要登录。"""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

tmp = tempfile.mkdtemp()
os.environ["CDT_DATA_DIR"] = tmp
os.environ["CDT_NO_SCHEDULER"] = "1"

from app.main import create_app  # noqa: E402
from app import update as updater  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def auth_headers(client):
    for pw in ("newpass123", "secret123"):
        r = client.post("/api/auth/login", json={"username": "管理员", "password": pw})
        if r.status_code == 200:
            return {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.post("/api/auth/setup", json={"admin_name": "管理员", "password": "secret123"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(autouse=True)
def _reset_cache():
    updater._cache["at"] = 0.0
    updater._cache["data"] = None
    updater._run_state["running"] = False
    updater._run_state["last"] = None
    yield
    updater._cache["at"] = 0.0
    updater._cache["data"] = None
    updater._run_state["running"] = False
    updater._run_state["last"] = None


def _fake_latest(monkeypatch, sha="abc1234", message="feat: test", date="2026-11-24T00:00:00Z"):
    monkeypatch.setattr(updater, "_fetch_latest",
                        lambda: {"sha": sha, "message": message, "date": date})


def test_current_version_defaults_to_dev(monkeypatch):
    monkeypatch.delenv("CDT_VERSION", raising=False)
    assert updater.current_version() == "dev"


def test_status_no_update_when_dev(monkeypatch):
    # 当前版本是 dev（镜像未注入版本号）时无法比较，不谎报有更新
    monkeypatch.delenv("CDT_VERSION", raising=False)
    _fake_latest(monkeypatch, sha="abc1234")
    s = updater.status()
    assert s["current_version"] == "dev"
    assert s["latest_version"] == "abc1234"
    assert s["update_available"] is False


def test_status_update_available_when_sha_differs(monkeypatch):
    monkeypatch.setenv("CDT_VERSION", "old0000")
    _fake_latest(monkeypatch, sha="abc1234")
    s = updater.status()
    assert s["update_available"] is True
    assert s["latest_message"] == "feat: test"
    assert s["latest_date"] == "2026-11-24T00:00:00Z"


def test_status_no_update_when_same_sha(monkeypatch):
    monkeypatch.setenv("CDT_VERSION", "abc1234")
    _fake_latest(monkeypatch, sha="abc1234")
    assert updater.status()["update_available"] is False


def test_check_latest_failure_is_graceful(monkeypatch):
    # GitHub 挂了 / 没网：状态接口不抛错，只标记 ok=false + 错误原因
    def boom():
        raise RuntimeError("network down")
    monkeypatch.setattr(updater, "_fetch_latest", boom)
    monkeypatch.delenv("CDT_VERSION", raising=False)
    s = updater.status()
    assert s["ok"] is False
    assert "network down" in (s["error"] or "")
    assert s["update_available"] is False


def test_check_latest_caches_within_ttl(monkeypatch):
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return {"sha": "abc1234", "message": "", "date": ""}
    monkeypatch.setattr(updater, "_fetch_latest", counting)
    updater.check_latest()
    updater.check_latest()
    assert calls["n"] == 1
    updater.check_latest(force=True)
    assert calls["n"] == 2


def test_start_update_without_socket_returns_manual(monkeypatch):
    # 本机没有 /var/run/docker.sock：返回手动命令，不假装更新成功
    monkeypatch.setattr(updater, "_docker_available", lambda: False)
    r = updater.start_update()
    assert r["started"] is False
    assert "docker compose pull" in r["manual"]


def test_create_kwargs_from_inspect():
    attrs = {
        "Name": "/Alicloud_ECS_CDTPanel",
        "Config": {
            "Env": ["TZ=Asia/Shanghai", "CDT_DATA_DIR=/data", "CDT_PROXY_ONLY=1"],
            "Cmd": None,
            "Entrypoint": ["/entrypoint.sh"],
        },
        "HostConfig": {
            "PortBindings": {"1999/tcp": [{"HostIp": "", "HostPort": "1999"}]},
            "Binds": ["/opt/docker/cdt-alicloud_ecs_cdtpanel:/data",
                      "/var/run/docker.sock:/var/run/docker.sock"],
            "RestartPolicy": {"Name": "unless-stopped"},
            "NetworkMode": "default",
            "Init": True,
        },
    }
    kw = updater._create_kwargs(attrs)
    assert kw["name"] == "Alicloud_ECS_CDTPanel"
    assert kw["image"] == "ghcr.io/gugetang/alicloud_ecs_cdtpanel:latest"
    assert kw["environment"] == ["TZ=Asia/Shanghai", "CDT_DATA_DIR=/data", "CDT_PROXY_ONLY=1"]
    assert kw["entrypoint"] == ["/entrypoint.sh"]
    assert kw["ports"] == {"1999/tcp": 1999}
    assert kw["volumes"]["/opt/docker/cdt-alicloud_ecs_cdtpanel"] == {"bind": "/data", "mode": "rw"}
    assert kw["volumes"]["/var/run/docker.sock"] == {"bind": "/var/run/docker.sock", "mode": "rw"}
    assert kw["restart_policy"] == {"Name": "unless-stopped"}
    assert kw["network"] == "default"
    assert kw["init"] is True
    assert kw["detach"] is True


def test_update_endpoints_require_login(client):
    # 未设置管理员时放行（首次向导）；设置管理员后未登录要 401
    h = auth_headers(client)
    assert client.get("/api/update/status").status_code == 401
    assert client.post("/api/update/check").status_code == 401
    assert client.post("/api/update/run").status_code == 401
    assert client.get("/api/update/status", headers=h).status_code == 200


def test_update_status_endpoint(client, monkeypatch):
    h = auth_headers(client)
    _fake_latest(monkeypatch, sha="abc1234")
    monkeypatch.setenv("CDT_VERSION", "old0000")
    r = client.get("/api/update/status", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["update_available"] is True
    assert body["current_version"] == "old0000"
    assert body["latest_version"] == "abc1234"
    # 强制刷新走同一接口
    r = client.post("/api/update/check", headers=h)
    assert r.status_code == 200
    assert r.json()["latest_version"] == "abc1234"


def test_update_run_endpoint_without_socket(client, monkeypatch):
    h = auth_headers(client)
    monkeypatch.setattr(updater, "_docker_available", lambda: False)
    r = client.post("/api/update/run", headers=h)
    assert r.status_code == 200
    assert r.json()["started"] is False
    assert "docker compose pull" in r.json()["manual"]
