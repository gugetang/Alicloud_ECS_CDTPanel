#!/bin/sh
# 容器入口：先以 root 身份把数据目录改属主为 cdt（UID 10001），
# 再切到 cdt 用户启动服务。
#
# 为什么需要这一步：宿主机上 Docker 自动创建的挂载目录属主是 root，
# 若容器直接以 UID 10001 启动，写 /data/cdt.db 会报
# sqlite3 OperationalError: unable to open database file。
# 入口脚本在启动时自动修好属主，`docker compose pull && docker compose up -d`
# 就能直接起来，不用手动 chown。
set -e

DATA_DIR="${CDT_DATA_DIR:-/data}"
mkdir -p "$DATA_DIR"
# 目录里已有 root 属主的文件（如旧版以 root 运行留下的）也一并改过来；
# 只读挂载等修不好的场景下不退出，由应用启动时的可写性检查报错。
chown -R 10001:10001 "$DATA_DIR" 2>/dev/null || true

# 以 cdt 用户执行命令（默认 python -m app.main；传了参数就用参数）。
# runuser / setpriv / su 按顺序取第一个可用的；都没有时退回 root 启动。
drop_privs() {
  if command -v runuser >/dev/null 2>&1; then
    exec runuser -u cdt -- "$@"
  elif command -v setpriv >/dev/null 2>&1; then
    exec setpriv --reuid=10001 --regid=10001 --clear-groups "$@"
  elif command -v su >/dev/null 2>&1; then
    exec su cdt -s /bin/sh -c 'exec "$@"' sh "$@"
  else
    echo "警告：容器内没有 runuser/setpriv/su，改以 root 身份启动（数据目录权限已修好）" >&2
    exec "$@"
  fi
}

if [ "$#" -gt 0 ]; then
  drop_privs "$@"
else
  drop_privs python -m app.main
fi
