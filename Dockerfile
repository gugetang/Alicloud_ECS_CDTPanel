# syntax=docker/dockerfile:1.7
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts
COPY web ./
RUN npm run build

FROM python:3.13-slim AS runtime
ARG VERSION=dev
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    CDT_DATA_DIR=/data CDT_LISTEN_PORT=1999 TZ=Asia/Shanghai
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY --from=frontend /src/app/static ./app/static
COPY scripts ./scripts
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin cdt \
    && mkdir -p /data && chown -R cdt:cdt /data /app \
    && chmod +x /usr/local/bin/entrypoint.sh
# 注意：这里不设 USER——入口脚本必须先以 root 启动，把宿主机挂载的 /data
# （Docker 自动创建时属主是 root）改属主为 cdt 后，再切到 cdt 用户运行服务。
# 这样 docker compose pull && up 不用手动 chown 也能直接起来。
VOLUME ["/data"]
EXPOSE 1999
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:1999/api/health',timeout=4).status==200 else 1)"]
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
