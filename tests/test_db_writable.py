"""数据目录不可写时，报错必须说明原因和修复方法，而不是裸 sqlite 报错。

背景：容器以 UID 10001 运行，宿主机上 Docker 自动创建的挂载目录属主是
root，容器写不进去时启动报 OperationalError: unable to open database file。
"""
import pytest

from app import db as dbmod
from app import settings as cfg


@pytest.fixture(autouse=True)
def _reset_conn():
    dbmod._conn = None
    yield
    if dbmod._conn is not None:
        try:
            dbmod._conn.close()
        except Exception:
            pass
    dbmod._conn = None


def test_ensure_dirs_unwritable_parent(monkeypatch, tmp_path):
    # DATA_DIR 落在一个普通文件下面：mkdir 必然失败（不依赖 chmod，
    # 以 root 跑测试也一样能复现）。
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setattr(cfg, "DATA_DIR", blocker / "data")
    with pytest.raises(RuntimeError, match="10001"):
        cfg.ensure_dirs()


def test_connect_db_path_is_directory(monkeypatch, tmp_path):
    # 目录可写，但 cdt.db 本身是个目录：sqlite 打不开，connect 应包一层
    # 带修复提示的 RuntimeError，而不是裸 OperationalError。
    data = tmp_path / "data"
    data.mkdir()
    (data / "cdt.db").mkdir()
    monkeypatch.setattr(cfg, "DATA_DIR", data)
    monkeypatch.setattr(cfg, "DB_PATH", data / "cdt.db")
    with pytest.raises(RuntimeError, match="cdt.db"):
        dbmod.connect()


def test_ensure_dirs_ok(monkeypatch, tmp_path):
    data = tmp_path / "data"
    monkeypatch.setattr(cfg, "DATA_DIR", data)
    cfg.ensure_dirs()
    assert data.is_dir()
    assert not (data / ".write-test").exists()
