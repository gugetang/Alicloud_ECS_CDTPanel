#!/usr/bin/env bash
# alicloud_ECS_CDT 一键部署 / 升级 / 卸载脚本
#
# 一键安装（数据目录 /opt/docker/alicloud_ECS_CDT）：
#   curl -fsSL https://raw.githubusercontent.com/gugetang/Alicloud_ECS_CDTPanel/main/install.sh | bash
# 注意：装好后 1999 只绑本机回环（127.0.0.1:1999），服务器 IP:端口打不开；
# 必须在前面配自己的反代（Nginx / Caddy / Cloudflare 等）+ 自己的证书，
# 反代指向 127.0.0.1:1999，见 README“部署安全”。项目本身不申请、不管理证书。
# 改目录：
#   curl -fsSL https://raw.githubusercontent.com/gugetang/Alicloud_ECS_CDTPanel/main/install.sh | DIR=/srv/cdt bash
# 升级：重跑同一条命令（git 已是最新时不重建容器）。
# 卸载：
#   curl -fsSL https://raw.githubusercontent.com/gugetang/Alicloud_ECS_CDTPanel/main/install.sh | UNINSTALL=1 bash
#
# 环境变量：
#   DIR        项目目录（内含 docker-compose.yml 与 data/），默认 /opt/docker/alicloud_ECS_CDT
#   REPO_URL   仓库地址，默认 https://github.com/gugetang/Alicloud_ECS_CDTPanel.git
#              （如需换仓库，直接传 REPO_URL）
#   UNINSTALL  设为 1 时卸载
set -euo pipefail

# DIR 是代码目录（克隆仓库的位置），默认 /opt/docker/alicloud_ECS_CDT。
# 数据卷在 docker-compose.yml 里写死为绝对路径
# /opt/docker/cdt-alicloud_ecs_cdtpanel:/data，数据固定落在那里，与 DIR 无关。
DIR="${DIR:-/opt/docker/alicloud_ECS_CDT}"
REPO_URL="${REPO_URL:-https://github.com/gugetang/Alicloud_ECS_CDTPanel.git}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

log() { printf '%s\n' "[install] $*"; }
die() { printf '%s\n' "[install] 错误：$*" >&2; exit 1; }

# root / sudo
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    die "请用 root 运行，或安装 sudo 后重试"
  fi
else
  SUDO=""
fi

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    die "未找到 docker compose（需要 Docker Compose v2 插件或 docker-compose）"
  fi
}

# ---------------- 卸载 ----------------
if [ "${UNINSTALL:-0}" = "1" ]; then
  log "卸载 alicloud_ECS_CDT（项目目录：$DIR）"
  printf '确认删除容器 Alicloud_ECS_CDTPanel 吗？输入 yes 继续：'
  read -r ans
  [ "$ans" = "yes" ] || die "已取消卸载"
  if [ -f "$DIR/$COMPOSE_FILE" ]; then
    (cd "$DIR" && compose down --remove-orphans || true)
  else
    docker rm -f Alicloud_ECS_CDTPanel 2>/dev/null || true
  fi
  printf '同时删除数据目录 %s/data 吗？（含 cdt.db 与 master.key，删后无法恢复）输入 yes 删除：' "$DIR"
  read -r ans2
  if [ "$ans2" = "yes" ]; then
    rm -rf "$DIR/data"
    log "数据目录已删除"
  fi
  printf '删除整个项目目录 %s 吗？输入 yes 删除：' "$DIR"
  read -r ans3
  if [ "$ans3" = "yes" ]; then
    rm -rf "$DIR"
    log "项目目录已删除"
  fi
  log "卸载完成"
  exit 0
fi

# ---------------- 拉镜像之前：先检查 Docker，没有先装 ----------------
install_docker() {
  log "正在安装 Docker / Docker Compose…"
  if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -y
    $SUDO apt-get install -y docker.io docker-compose-plugin git curl ca-certificates \
      || $SUDO apt-get install -y docker.io git curl ca-certificates
  elif command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y docker docker-compose-plugin git curl \
      || $SUDO dnf install -y docker git curl
  elif command -v yum >/dev/null 2>&1; then
    $SUDO yum install -y docker git curl
  elif command -v apk >/dev/null 2>&1; then
    $SUDO apk add --no-cache docker docker-cli-compose git curl ca-certificates
    $SUDO rc-update add docker default 2>/dev/null || true
  else
    die "无法识别包管理器（apt/yum/dnf/apk），请手动安装 Docker + Docker Compose 后重跑"
  fi
  # 兜底：发行版包里没有 compose 插件时，用官方脚本装 Docker（含 compose）
  if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
    log "发行版源里没有 Compose，改用 get.docker.com 安装…"
    curl -fsSL https://get.docker.com | $SUDO sh \
      || die "Docker 安装失败，请手动安装 Docker 后重跑"
  fi
}

# 拉镜像之前先检查 Docker：没有就先装 Docker，装完必须能用，
# 装不上就明确报错退出，不继续往下拉镜像、不谎报成功。
if ! command -v docker >/dev/null 2>&1; then
  log "未检测到 Docker，先安装 Docker…"
  install_docker || die "Docker 安装失败，请手动安装 Docker（https://docs.docker.com/engine/install/）后重跑"
fi
if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
  log "未检测到 Docker Compose，先安装…"
  install_docker || die "Docker Compose 安装失败，请手动安装 Docker Compose 后重跑"
fi
# 装完再确认一次：docker 命令和 compose 必须能用，否则直接报错退出。
command -v docker >/dev/null 2>&1 || die "Docker 安装失败：本机没有 docker 命令，请手动安装 Docker 后重跑"
if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
  die "Docker Compose 安装失败：找不到 docker compose 插件或 docker-compose 命令，请手动安装后重跑"
fi

# 启动 Docker 守护进程（systemd / OpenRC / service 均尝试，失败则报错）
if ! docker info >/dev/null 2>&1; then
  $SUDO systemctl enable --now docker 2>/dev/null \
    || $SUDO service docker start 2>/dev/null \
    || $SUDO rc-service docker start 2>/dev/null \
    || true
fi
docker info >/dev/null 2>&1 || die "Docker 守护进程未运行，请手动启动 Docker 后重跑（systemctl start docker）"

command -v git >/dev/null 2>&1 || {
  log "安装 git…"
  if command -v apt-get >/dev/null 2>&1; then $SUDO apt-get install -y git
  elif command -v dnf >/dev/null 2>&1; then $SUDO dnf install -y git
  elif command -v yum >/dev/null 2>&1; then $SUDO yum install -y git
  elif command -v apk >/dev/null 2>&1; then $SUDO apk add --no-cache git
  else die "请手动安装 git 后重跑"; fi
}
command -v curl >/dev/null 2>&1 || die "请先安装 curl 后重跑"

# ---------------- 获取项目源码 ----------------
if [ -f "./$COMPOSE_FILE" ] && [ -f "./Dockerfile" ]; then
  # 在仓库检出目录里直接运行：就地部署
  PROJECT_DIR="$(pwd)"
  log "检测到本地仓库检出，使用当前目录：$PROJECT_DIR"
elif [ -f "$DIR/$COMPOSE_FILE" ]; then
  PROJECT_DIR="$DIR"
  log "使用已有项目目录：$PROJECT_DIR"
  if [ -d "$DIR/.git" ]; then
    BEFORE="$(cd "$DIR" && git rev-parse HEAD 2>/dev/null || echo none)"
    (cd "$DIR" && git pull --ff-only) || log "git pull 失败，继续使用本地源码"
    AFTER="$(cd "$DIR" && git rev-parse HEAD 2>/dev/null || echo none)"
    if [ "$BEFORE" = "$AFTER" ] && [ "$BEFORE" != "none" ] \
       && docker ps --format '{{.Names}}' | grep -qx Alicloud_ECS_CDTPanel; then
      log "已是最新版本，容器正在运行，无需重建"
      (cd "$DIR" && compose up -d)
      log "面板只在本机 127.0.0.1:1999 提供服务，服务器 IP:端口打不开；请用你自己的反代（域名 + HTTPS）访问，见 README“部署安全”"
      exit 0
    fi
  fi
else
  case "$REPO_URL" in
    *"<user>"*) die "请把 REPO_URL 里的 <user> 换成你的 GitHub 用户名再运行，例如：REPO_URL=https://github.com/你的用户名/Alicloud_ECS_CDTPanel.git" ;;
  esac
  log "克隆仓库到 $DIR …"
  mkdir -p "$(dirname "$DIR")"
  if [ -d "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
    die "目录 $DIR 已存在且不含 docker-compose.yml，请清空它或改用 DIR 指定其他目录"
  fi
  git clone "$REPO_URL" "$DIR" || die "克隆仓库失败：$REPO_URL（检查地址与网络）"
  PROJECT_DIR="$DIR"
fi

mkdir -p "$PROJECT_DIR/data"

# 数据卷宿主机目录：新版镜像的入口脚本会在容器启动时自动把 /data 改属主
# 为 10001，拉取启动不用手动处理。这里再提前改一次是双保险（旧版镜像
# 没有入口脚本时也靠它），改失败也不影响，容器启动时入口脚本会再修一次。
DATA_HOST_DIR="/opt/docker/cdt-alicloud_ecs_cdtpanel"
mkdir -p "$DATA_HOST_DIR"
if ! chown -R 10001:10001 "$DATA_HOST_DIR" 2>/dev/null; then
  log "提示：无法把 $DATA_HOST_DIR 改属主为 10001:10001（需要 root）；新版镜像启动时入口脚本会自动修好，旧版镜像请手动执行：sudo chown -R 10001:10001 $DATA_HOST_DIR 后重启容器"
fi

# ---------------- 拉取镜像并启动 ----------------
# 精简版 compose 只拉 GHCR 镜像，不本地构建；1999 只绑本机回环。
cd "$PROJECT_DIR"
log "拉取镜像并启动（目录 $PROJECT_DIR）…"
compose pull
compose up -d

# ---------------- 等待健康检查 ----------------
# 1999 只绑本机回环，宿主机上 curl http://127.0.0.1:1999 就能通。
log "等待健康检查通过（最多 60 秒）…"
ok=0
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:1999/api/health" >/dev/null 2>&1; then
    ok=1; break
  fi
  sleep 1
done
[ "$ok" = "1" ] || {
  compose logs --tail=50
  die "健康检查 60 秒未通过，请用 docker compose logs -f Alicloud_ECS_CDTPanel 查看日志"
}

log "部署完成！"
log "面板只在本机 127.0.0.1:1999 提供服务，服务器 IP:端口打不开。"
log "下一步：配好你自己的反代（域名 + HTTPS，指向 127.0.0.1:1999，见 README“部署安全”），再打开页面 → 添加 Ali 账户 → 添加域名 → 全局设置 → 先点「调度试算」确认无误，再点「立即调度」。"
log "查看日志：cd $PROJECT_DIR && docker compose logs -f"
log "升级：重跑本脚本即可。卸载：UNINSTALL=1 bash install.sh"
