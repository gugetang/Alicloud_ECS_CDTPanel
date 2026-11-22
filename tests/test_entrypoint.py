"""入口脚本 entrypoint.sh：启动时自动修好 /data 属主，再降权运行服务。

背景：宿主机上 Docker 自动创建的挂载目录属主是 root，容器直接以
UID 10001 启动会报 sqlite3 OperationalError: unable to open database file。
入口脚本以 root 启动，chown 后切到 cdt 用户，拉取启动不用手动 chown。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
ENTRYPOINT = PROJECT / "entrypoint.sh"


def _run_entrypoint(args, env_extra, cwd=None):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(
        ["/bin/sh", str(ENTRYPOINT), *args],
        env=env, cwd=cwd, capture_output=True, text=True, timeout=30,
    )


def _stub_runuser(stub: Path) -> None:
    """假 runuser：去掉 -u cdt -- 后直接执行（本机没有 cdt 用户时用）。"""
    stub.mkdir(parents=True, exist_ok=True)
    (stub / "runuser").write_text(
        "#!/bin/sh\nwhile [ \"$1\" != \"--\" ]; do shift; done\nshift\nexec \"$@\"\n")
    (stub / "runuser").chmod(0o755)


def test_entrypoint_syntax():
    r = subprocess.run(["sh", "-n", str(ENTRYPOINT)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_dockerfile_uses_entrypoint():
    text = (PROJECT / "Dockerfile").read_text()
    assert 'ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]' in text
    assert "\nUSER 10001" not in text  # 入口脚本必须先以 root 启动才能 chown


def test_entrypoint_runs_command_via_runuser(tmp_path, monkeypatch):
    # 用假 runuser（去掉 -u cdt -- 后直接执行），验证脚本走到降权执行分支。
    stub = tmp_path / "bin"
    _stub_runuser(stub)
    data = tmp_path / "data"
    marker = tmp_path / "marker.txt"
    env = {
        "PATH": f"{stub}:{os.environ['PATH']}",
        "CDT_DATA_DIR": str(data),
    }
    r = _run_entrypoint(
        [sys.executable, "-c",
         f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ok')"],
        env)
    assert r.returncode == 0, r.stderr
    assert marker.read_text() == "ok"
    assert data.is_dir()


@pytest.mark.skipif(os.getuid() != 0, reason="chown 到 10001 需要 root")
def test_entrypoint_chowns_data_dir_to_10001(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "old.db").write_text("x")
    assert os.stat(data).st_uid == 0
    stub = tmp_path / "bin"
    _stub_runuser(stub)  # 本机没有 cdt 用户，用假 runuser 走通执行分支
    r = _run_entrypoint(["true"], {
        "PATH": f"{stub}:{os.environ['PATH']}",
        "CDT_DATA_DIR": str(data),
    })
    assert r.returncode == 0, r.stderr
    assert os.stat(data).st_uid == 10001
    assert os.stat(data / "old.db").st_uid == 10001


def test_entrypoint_fallback_without_privilege_tools(tmp_path):
    # PATH 里只有 mkdir/chown/python，没有 runuser/setpriv/su：
    # 应退回当前用户直接执行命令，而不是崩溃。
    stub = tmp_path / "bin"
    stub.mkdir()
    for name, target in (("mkdir", "/bin/mkdir"), ("chown", "/bin/chown"),
                         ("python", sys.executable)):
        os.symlink(target, stub / name)
    marker = tmp_path / "marker.txt"
    env = {
        "PATH": str(stub),
        "CDT_DATA_DIR": str(tmp_path / "data"),
    }
    r = _run_entrypoint(
        [sys.executable, "-c",
         f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ok')"],
        env)
    assert r.returncode == 0, r.stderr
    assert marker.read_text() == "ok"
