#!/usr/bin/env bash
# SMBU Schedule Sync — 阿里云 ECS 一键部署脚本（在服务器上以 root 运行）
#
# 阶段1（默认，无需 ICP 备案）：自签证书 + 高位端口 8443
#   bash deploy.sh
# 阶段2（ICP 备案完成后）：Let's Encrypt 证书 + 标准 443
#   LETSENCRYPT=1 EMAIL=you@swiftiejerry.xyz bash deploy.sh
#
# 源码应已传到 $APP_DIR（部署机用 scp 传压缩包；开源后也可 --from-git 克隆）。
set -euo pipefail

APP_DIR=/opt/smbu-calendar
DOMAIN=swiftiejerry.xyz
LETSENCRYPT="${LETSENCRYPT:-0}"
EMAIL="${EMAIL:-admin@$DOMAIN}"
PORTAL_PORT=8443

echo "==> [1/6] 安装 docker + compose（若缺失）"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose 插件缺失，请先安装 docker compose 插件" >&2; exit 1
fi

echo "==> [2/6] 定位源码"
if [ "${1:-}" = "--from-git" ] && [ -n "${2:-}" ]; then
  rm -rf "$APP_DIR" && git clone "$2" "$APP_DIR"
fi
cd "$APP_DIR"
[ -f deploy/docker-compose.yml ] || { echo "未找到 $APP_DIR/deploy/docker-compose.yml，请先把源码传到该目录" >&2; exit 1; }

echo "==> [3/6] 准备证书"
mkdir -p deploy/certs/live/$DOMAIN
if [ "$LETSENCRYPT" = "1" ]; then
  echo "    使用 Let's Encrypt（certbot standalone）"
  apt-get update -y >/dev/null 2>&1 && apt-get install -y certbot >/dev/null 2>&1 || \
    (command -v certbot >/dev/null 2>&1 || { echo "certbot 安装失败" >&2; exit 1; })
  docker compose -f deploy/docker-compose.yml stop nginx 2>/dev/null || true
  certbot certonly --standalone -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL"
  cp -f /etc/letsencrypt/live/$DOMAIN/fullchain.pem deploy/certs/live/$DOMAIN/
  cp -f /etc/letsencrypt/live/$DOMAIN/privkey.pem   deploy/certs/live/$DOMAIN/
  export NGINX_CONF=nginx.conf
  LISTEN_PORT=443
else
  echo "    生成自签证书（阶段1；浏览器会提示不安全，属正常）"
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout deploy/certs/live/$DOMAIN/privkey.pem \
    -out    deploy/certs/live/$DOMAIN/fullchain.pem \
    -subj "/CN=$DOMAIN"
  export NGINX_CONF=nginx.portal.conf
  LISTEN_PORT=$PORTAL_PORT
fi

echo "==> [4/6] 构建镜像并启动"
SMBU_ALLOW_ORIGINS="https://$DOMAIN" docker compose -f deploy/docker-compose.yml build
SMBU_ALLOW_ORIGINS="https://$DOMAIN" docker compose -f deploy/docker-compose.yml up -d

echo "==> [5/6] 健康检查"
for i in $(seq 1 10); do
  if curl -fsS "http://127.0.0.1:8770/api/health" >/dev/null 2>&1; then
    echo "    app health OK"; break
  fi
  sleep 3
done

echo "==> [6/6] 完成"
echo "访问地址： https://$DOMAIN:$LISTEN_PORT   （阶段1）"
echo "          https://$DOMAIN          （阶段2，备案 + LETSENCRYPT=1 后）"
echo "后台状态："
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
