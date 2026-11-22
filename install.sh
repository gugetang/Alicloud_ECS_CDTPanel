#!/usr/bin/env bash
# alicloud_ECS_CDT 一键部署 / 升级 / 卸载脚本
#
# 一键安装（默认端口 1999，数据目录 /opt/docker/alicloud_ECS_CDT）：
#   curl -fsSL https://raw.githubusercontent.com/gugetang/Alicloud_ECS_CDTPanel/main/install.sh | bash
# 改端口 / 改目录：
#   curl -fsSL https://raw.githubusercontent.com/gugetang/Alicloud_ECS_CDTPanel/main/install.sh | PORT=18288 DIR=/srv/cdt bash
# 升级：重跑同一条命令（git 已是最新时不重建容器）。
# 卸载：
#   curl -fsSL https://raw.githubusercontent.com/gugetang/Alicloud_ECS_CDTPanel/main/install.sh | UNINSTALL=1 bash
#
# 环境变量：
#   PORT       对外端口，默认 1999
#   DIR        项目目录（内含 docker-compose.yml 与 data/），默认 /opt/docker/alicloud_ECS_CDT
#   REPO_URL   仓库地址，默认 https://github.com/gugetang/Alicloud_ECS_CDTPanel.git
#              （如需换仓库，直接传 REPO_URL）
#   UNINSTALL  设为 1 时卸载
set -euo pipefail

PORT="${PORT:-1999}"
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
  printf '确认删除容器 alicloud-ecs-cdt 吗？输入 yes 继续：'
  read -r ans
  [ "$ans" = "yes" ] || die "已取消卸载"
  if [ -f "$DIR/$COMPOSE_FILE" ]; then
    (cd "$DIR" && compose down --remove-orphans || true)
  else
    docker rm -f alicloud-ecs-cdt 2>/dev/null || true
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

# ---------------- 安装 Docker ----------------
install_docker() {
  log "未检测到 Docker，尝试自动安装…"
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

if ! command -v docker >/dev/null 2>&1; then
  install_docker
fi
if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
  install_docker
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
       && docker ps --format '{{.Names}}' | grep -qx alicloud-ecs-cdt; then
      log "已是最新版本，容器正在运行，无需重建"
      (cd "$DIR" && PORT="$PORT" compose up -d)
      log "访问地址：http://$(hostname -I 2>/dev/null | awk '{print $1}'):${PORT}"
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

# ---------------- 构建并启动 ----------------
cd "$PROJECT_DIR"
export PORT
log "构建并启动（端口 $PORT，目录 $PROJECT_DIR）…"
compose up -d --build

# ---------------- 等待健康检查 ----------------
log "等待健康检查通过（最多 60 秒）…"
ok=0
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    ok=1; break
  fi
  sleep 1
done
[ "$ok" = "1" ] || {
  compose logs --tail=50
  die "健康检查 60 秒未通过，请用 docker compose logs -f alicloud_ecs_cdt 查看日志"
}

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
HOST_IP="${HOST_IP:-你的服务器地址}"
log "部署完成！"
log "访问地址：http://${HOST_IP}:${PORT}"
log "下一步：打开页面 → 添加 Ali 账户 → 添加域名 → 全局设置 → 先点「调度试算」确认无误，再点「立即调度」。"
log "查看日志：cd $PROJECT_DIR && docker compose logs -f"
log "升级：重跑本脚本即可。卸载：UNINSTALL=1 bash install.sh"
