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
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin cdt \
    && mkdir -p /data && chown -R cdt:cdt /data /app
USER 10001:10001
VOLUME ["/data"]
EXPOSE 1999
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:1999/api/health',timeout=4).status==200 else 1)"]
ENTRYPOINT ["python", "-m", "app.main"]
