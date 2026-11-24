#!/usr/bin/env bash
# alicloud_ECS_CDT 一键部署 / 升级 / 卸载脚本
#
# 一键安装（域名必填，面板只通过该域名 + HTTPS 访问，IP:端口打不开）：
#   curl -fsSL https://raw.githubusercontent.com/gugetang/Alicloud_ECS_CDTPanel/main/install.sh | DOMAIN=cdt.example.com bash
# 交互式（不传 DOMAIN 时提示输入）：
#   curl -fsSL https://raw.githubusercontent.com/gugetang/Alicloud_ECS_CDTPanel/main/install.sh | bash
# 改目录：
#   curl -fsSL https://raw.githubusercontent.com/gugetang/Alicloud_ECS_CDTPanel/main/install.sh | DOMAIN=cdt.example.com DIR=/srv/cdt bash
# 升级：重跑同一条命令（git 已是最新时不重建容器；旧部署升级也要先配好域名）。
# 卸载：
#   curl -fsSL https://raw.githubusercontent.com/gugetang/Alicloud_ECS_CDTPanel/main/install.sh | UNINSTALL=1 bash
#
# 环境变量：
#   DOMAIN     访问域名（必填），如 cdt.example.com；也可写 CDT_PUBLIC_DOMAIN
#   DIR        项目目录（内含 docker-compose.yml 与 data/），默认 /opt/docker/alicloud_ECS_CDT
#   REPO_URL   仓库地址，默认 https://github.com/gugetang/Alicloud_ECS_CDTPanel.git
#              （如需换仓库，直接传 REPO_URL）
#   ALLOW_UNRESOLVED_DOMAIN  设为 1 时，域名解析不到本机也不中断（仅用于
#              CDN / Cloudflare 代理等解析结果不是本机 IP 的场景）
#   UNINSTALL  设为 1 时卸载
set -euo pipefail

DOMAIN="${DOMAIN:-${CDT_PUBLIC_DOMAIN:-}}"
# DIR 是代码目录（克隆仓库的位置），默认 /opt/docker/alicloud_ECS_CDT。
# 数据卷在 docker-compose.yml 里写死为绝对路径
# /opt/docker/cdt-alicloud_ecs_cdtpanel:/data，数据固定落在那里，与 DIR 无关。
DIR="${DIR:-/opt/docker/alicloud_ECS_CDT}"
REPO_URL="${REPO_URL:-https://github.com/gugetang/Alicloud_ECS_CDTPanel.git}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

log() { printf '%s\n' "[install] $*"; }
die() { printf '%s\n' "[install] 错误：$*" >&2; exit 1; }

# 域名校验：只要裸域名，不要协议、端口、路径；IP 直接拒绝。
valid_domain() {
  case "$1" in
    *://*|*/*|*:*|*.|*..*) return 1 ;;
  esac
  case "$1" in
    [0-9]*.[0-9]*.[0-9]*.[0-9]*) return 1 ;;  # IPv4 不是域名
  esac
  printf '%s' "$1" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$'
}

# 解析域名到 IP（多工具兜底，返回空格分隔的 IP 列表，可能为空）。
resolve_domain() {
  if command -v getent >/dev/null 2>&1; then
    getent ahosts "$1" 2>/dev/null | awk '{print $1}' | sort -u
  elif command -v nslookup >/dev/null 2>&1; then
    nslookup "$1" 2>/dev/null | awk '/^Address: /{print $2}' | sort -u
  elif command -v dig >/dev/null 2>&1; then
    dig +short "$1" 2>/dev/null | grep -E '^[0-9.]+$' | sort -u
  else
    python3 -c "import socket;print('\n'.join(sorted({str(i[4][0]) for i in socket.getaddrinfo('$1', None)})))" 2>/dev/null || true
  fi
}

# 本机公网 IP（拿不到时为空，不阻塞）。
public_ip() {
  curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null \
    || curl -fsS --max-time 5 https://ifconfig.me 2>/dev/null || true
}

# 确定本次部署用的域名：.env 已有就沿用；没有就问用户要（非交互且未传
# DOMAIN 时直接报错，不静默装成一个 IP 就能访问的站）。
# 域名解析不到任何 IP 时报错；解析到了但不是本机 IP 时明确提示
#（可能是 Cloudflare 等 CDN 代理），交互时要用户确认，非交互时警告后继续。
ensure_domain() {
  local env_file="$1"
  if [ -z "$DOMAIN" ] && [ -f "$env_file" ]; then
    DOMAIN="$(grep -E '^CDT_PUBLIC_DOMAIN=' "$env_file" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '[:space:]')"
  fi
  if [ -z "$DOMAIN" ]; then
    if [ -t 0 ]; then
      printf '请输入访问域名（如 cdt.example.com，面板只通过该域名 + HTTPS 访问，IP:端口打不开）：'
      read -r DOMAIN
    else
      die "必须指定访问域名：请用 DOMAIN=你的域名 bash 重新运行，例如：DOMAIN=cdt.example.com bash install.sh"
    fi
  fi
  DOMAIN="$(printf '%s' "$DOMAIN" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  valid_domain "$DOMAIN" || die "域名不合法：$DOMAIN（请填裸域名，如 cdt.example.com，不要带 http://、端口或路径，也不要填 IP）"
  local resolved server_ip
  resolved="$(resolve_domain "$DOMAIN" | tr '\n' ' ')"
  if [ -z "$resolved" ]; then
    if [ "${ALLOW_UNRESOLVED_DOMAIN:-0}" = "1" ]; then
      log "警告：域名 $DOMAIN 解析不到任何 IP（ALLOW_UNRESOLVED_DOMAIN=1，继续部署；证书申请会失败，请先把域名解析到本机）"
    else
      die "域名 $DOMAIN 解析不到任何 IP：请先把域名解析到本机（A 记录指向服务器公网 IP），再重跑；确认域名无误但暂时不想等解析生效时，可用 ALLOW_UNRESOLVED_DOMAIN=1 bash 重跑"
    fi
  else
    server_ip="$(public_ip)"
    if [ -n "$server_ip" ] && ! printf '%s' "$resolved" | grep -qw "$server_ip"; then
      log "注意：域名 $DOMAIN 解析到 $resolved，本机公网 IP 是 $server_ip，两者不一致。"
      log "如果你用了 Cloudflare 等 CDN 代理，这是正常的（解析到的是 CDN 节点）；否则请检查域名解析是否指向本机。"
      if [ -t 0 ]; then
        printf '确认域名解析无误、继续部署吗？输入 yes 继续：'
        read -r ans
        [ "$ans" = "yes" ] || die "已取消部署"
      fi
    fi
  fi
  # 写入 .env（compose 自动读取）：已有就替换该行，没有就追加。
  if [ -f "$env_file" ] && grep -qE '^CDT_PUBLIC_DOMAIN=' "$env_file"; then
    sed -i "s|^CDT_PUBLIC_DOMAIN=.*|CDT_PUBLIC_DOMAIN=$DOMAIN|" "$env_file"
  else
    printf 'CDT_PUBLIC_DOMAIN=%s\n' "$DOMAIN" >> "$env_file"
  fi
  log "访问域名：$DOMAIN（只通过 https://$DOMAIN 访问，IP:端口打不开）"
}

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
    docker rm -f Alicloud_ECS_CDTPanel cdt-caddy 2>/dev/null || true
  fi
  log "提示：Caddy 的证书数据在 compose 命名卷 caddy_data / caddy_config 里，卸载不删除；想彻底清理可用 docker volume rm <项目名>_caddy_data <项目名>_caddy_config"
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
       && docker ps --format '{{.Names}}' | grep -qx Alicloud_ECS_CDTPanel \
       && [ -f "$DIR/.env" ] && grep -qE '^CDT_PUBLIC_DOMAIN=[A-Za-z0-9.-]+$' "$DIR/.env"; then
      # 已是最新且已按域名 + HTTPS 部署：不用重建，直接确认容器在跑。
      log "已是最新版本，容器正在运行，无需重建"
      (cd "$DIR" && compose up -d)
      DOMAIN="$(grep -E '^CDT_PUBLIC_DOMAIN=' "$DIR/.env" | tail -1 | cut -d= -f2-)"
      log "访问地址：https://$DOMAIN（IP:端口打不开）"
      exit 0
    fi
    # 否则走下面的完整流程：旧部署（IP:1999 对外）升级到新部署时，
    # ensure_domain 会先让用户配好域名，再用新 compose 重建容器；
    # 数据目录不动，数据库和数据不受影响。
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

# ---------------- 域名（必填）+ 拉取镜像并启动 ----------------
# 精简版 compose 只拉 GHCR 镜像，不本地构建；对外只发布 80/443（Caddy），
# 1999 不对外发布，面板只监听容器内 127.0.0.1:1999。
if [ -n "${PORT:-}" ]; then
  log "注意：新部署不再对外发布端口，PORT=$PORT 已忽略（访问只走 https://域名，端口是 80/443）"
fi
# 就地部署（当前目录就是仓库）时 .env 也写在当前目录。
ensure_domain "$PROJECT_DIR/.env"
cd "$PROJECT_DIR"
log "拉取镜像并启动（域名 $DOMAIN，目录 $PROJECT_DIR）…"
compose pull
compose up -d

# ---------------- 等待健康检查 ----------------
# 面板只监听容器内 127.0.0.1:1999，宿主机上 127.0.0.1:1999 是打不开的，
# 所以健康检查进容器里做（和镜像 HEALTHCHECK 同一个地址）。
log "等待面板健康检查通过（最多 60 秒）…"
ok=0
for _ in $(seq 1 60); do
  if docker exec Alicloud_ECS_CDTPanel python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:1999/api/health',timeout=4).status==200 else 1)" >/dev/null 2>&1; then
    ok=1; break
  fi
  sleep 1
done
[ "$ok" = "1" ] || {
  compose logs --tail=50
  die "健康检查 60 秒未通过，请用 docker compose logs -f Alicloud_ECS_CDTPanel 查看日志"
}

# 再等 Caddy 把证书申请下来、域名能 HTTPS 访问（最多 180 秒）。
# 证书申请需要域名已解析到本机且 80/443 对外开放；失败时不谎报成功。
log "等待 https://$DOMAIN 可访问（证书申请中，最多 180 秒）…"
ok=0
for _ in $(seq 1 180); do
  if curl -fsS "https://$DOMAIN/api/health" >/dev/null 2>&1; then
    ok=1; break
  fi
  sleep 1
done
[ "$ok" = "1" ] || {
  compose logs --tail=50 caddy
  die "https://$DOMAIN 180 秒仍不可访问：请检查域名是否解析到本机、服务器 80/443 端口是否对外开放，再看 Caddy 日志（docker compose logs -f caddy）；面板本身健康检查已通过，数据不受影响"
}

log "部署完成！"
log "访问地址：https://$DOMAIN（只通过域名 + HTTPS 访问，IP:端口打不开）"
log "下一步：打开页面 → 添加 Ali 账户 → 添加域名 → 全局设置 → 先点「调度试算」确认无误，再点「立即调度」。"
log "查看日志：cd $PROJECT_DIR && docker compose logs -f"
log "升级：重跑本脚本即可。卸载：UNINSTALL=1 bash install.sh"
