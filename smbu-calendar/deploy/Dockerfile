# Build from the project root (smbu-calendar/):
#   docker build -f deploy/Dockerfile -t smbu-calendar:latest .
#   docker run -d --name smbu -p 8770:8770 --env-file deploy/.env smbu-calendar:latest
#
# 国内源默认值（阿里云 ECS 同厂商内网，速度可达数十~上百 MB/s，且大概率不计费）：
#   PYPI_INDEX     阿里云 PyPI 镜像
#   NPM_REGISTRY   npmmirror
#   APT_MIRROR     阿里云 Debian 镜像（playwright --with-deps 装系统库用）
#   PLAYWRIGHT_HOST npmmirror 的 Playwright 浏览器二进制镜像（替代默认境外 CDN）
# 非中国环境可覆盖：--build-arg PYPI_INDEX=https://pypi.org/simple 等。

# ---- Stage 1: build the SPA -------------------------------------------------
FROM node:22-slim AS fe
ARG NPM_REGISTRY=https://registry.npmmirror.com
WORKDIR /app/frontend
# Lockfile first so the dependency layer caches independently of source changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --registry=$NPM_REGISTRY
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: runtime -------------------------------------------------------
FROM python:3.13-slim AS runtime
ARG PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple
ARG APT_MIRROR=mirrors.aliyun.com
ARG PLAYWRIGHT_HOST=https://cdn.npmmirror.com/binaries/playwright
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Debian apt 源切到阿里云（playwright --with-deps 装系统库用）。
# 注意：Debian 12(bookworm) 容器用 DEB822 格式 /etc/apt/sources.list.d/debian.sources，
# 而非传统 /etc/apt/sources.list —— 两种格式都要覆盖，否则 apt 仍走境外 deb.debian.org。
RUN for f in /etc/apt/sources.list /etc/apt/sources.list.d/*; do \
      [ -f "$f" ] && sed -i "s#deb.debian.org#${APT_MIRROR}#g; s#security.debian.org#${APT_MIRROR}#g" "$f"; \
    done; true

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -i ${PYPI_INDEX} -r requirements.txt

# Run unprivileged — the app never needs root.
RUN useradd --create-home --uid 10001 appuser

# WebVPN login runs a headless Chromium (browser = login only, httpx = data
# plane — see WEBVPN.md). The container has no msedge/chrome, so the bundled
# browser is installed and SMBU_WEBVPN_BROWSER_CHANNEL is blanked by default.
ENV SMBU_WEBVPN_BROWSER_CHANNEL="" \
    PLAYWRIGHT_BROWSERS_PATH=/home/appuser/.cache/ms-playwright \
    PLAYWRIGHT_DOWNLOAD_HOST=${PLAYWRIGHT_HOST}
# Pin the version: the downloaded browser build is version-locked, so an
# unpinned bump silently changes which Chromium lands in the image.
RUN mkdir -p /home/appuser/.cache/ms-playwright \
    && chown -R appuser:appuser /home/appuser \
    && pip install --no-cache-dir -i ${PYPI_INDEX} "playwright==1.62.0" \
    && python -m playwright install --with-deps chromium

COPY backend/ ./backend/
# FRONTEND_DIST resolves to <project>/frontend/dist; matches this layout.
COPY --from=fe /app/frontend/dist ./frontend/dist

# The ledger writes backend/data/payments.db at runtime.
RUN mkdir -p /app/backend/data && chown -R appuser:appuser /app
USER appuser

WORKDIR /app/backend
EXPOSE 8770

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8770/api/health', timeout=4).status == 200 else 1)"

# ONE worker on purpose: sessions live in process memory, so two workers would
# silently log users out depending on which one answered the request. Scale by
# running more containers behind a sticky-session proxy, not more workers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8770", "--workers", "1"]
