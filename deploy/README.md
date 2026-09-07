# Docker 部署

`smbu-calendar` 单体镜像：容器内由 FastAPI 同时提供 `/api/*` 与构建好的 SPA（同源，无需 CORS）。

## 快速开始

```bash
# 构建（在仓库根执行，拍平后 backend/ frontend/ deploy/ 位于顶层）
docker build -f deploy/Dockerfile -t smbu-calendar:latest .

# 运行 —— 默认即连真实教务系统
docker run -d --name smbu-app -p 8771:8770 --restart unless-stopped \
  -e SMBU_ALLOW_ORIGINS="https://你的域名" \
  -e SMBU_DEBUG_RAW_ENDPOINT=false \
  -e SMBU_UPSTREAM_PROXY="http://host.docker.internal:8899" \
  smbu-calendar:latest

docker inspect --format='{{.State.Health.Status}}' smbu-app   # -> healthy
curl http://127.0.0.1:8771/api/health
```

访问 `http://localhost:8771`。生产请把 TLS 交给外层 nginx/Caddy 终止（见 `nginx.conf`）。

## 接入模式（校内 / 校外）

`SMBU_ACCESS_MODE` 决定后端怎么连教务系统（默认 `auto`）：

| 值 | 行为 |
|---|---|
| `auto` | 先试直连 `jw.smbu.edu.cn`；仅当**传输级失败**（连接被拒/超时）才自动降级 WebVPN 隧道。凭据错误不降级（避免二次烧 CAS 尝试）。 |
| `direct` | 只走直连入口。 |
| `webvpn` | 强制走 `webvpn.smbu.edu.cn` 门户 + 隧道子域（云服务器出口被校园网关拒绝时的保底）。 |

WebVPN 链路细节（握手限频/cookie 域/连接池三个坑的防护）见 `WEBVPN.md`。

> **已验证（2026-09-06）**：镜像内置 headless chromium（playwright 1.62.0，非 root
> 运行），`SMBU_ACCESS_MODE=webvpn` 下真实账号 HTTP e2e 通过——login 5.7s → 20 学期 →
> 28 VEVENT ICS，容器日志确认 `via webvpn`；两次独立登录产出的 ICS 除 DTSTAMP 外
> 逐字节一致（UID 稳定，日历客户端重复导入不会产生重复事件）。

> **`--restart unless-stopped`**：Docker/WSL2 重启（休眠唤醒、开机）后容器自动拉起，
> 不用手动干预。实测某次 Docker 重启导致容器 `Exited(255)`，加上该策略后恢复无忧。

## 镜像特性

| 项 | 说明 |
|---|---|
| 多阶段构建 | node 编译 SPA → python 运行，最终镜像不含 node/npm |
| 构建上下文 | **约 320KB**（`.dockerignore` 排除了 `node_modules` 82MB + `.venv` 62MB） |
| 非 root | 以 `uid 10001 (appuser)` 运行 |
| 健康检查 | 每 30s 探 `/api/health`，含 15s 启动宽限 |
| **单 worker** | 会话在进程内存，`--workers 2` 会让用户随机掉线。扩容请**多起容器 + 粘性会话**，不要加 worker |
| 依赖安装 | `npm ci`（锁文件，可复现） |

## 离线自检（不碰真实教务系统）

`deploy/mock/` 是**仅用于测试**的教务系统替身，回放真实抓包报文。用它可在完全离线、
无需账号的情况下验证整条链路（也避免压测触发验证码锁定）：

```bash
docker build -f deploy/mock/Dockerfile -t smbu-mock:latest deploy/mock
docker network create smbu-net
docker run -d --name smbu-mock --network smbu-net smbu-mock:latest

docker run -d --name smbu-app --network smbu-net -p 8770:8770 \
  -e SMBU_JW_BASE_URL="http://smbu-mock:8799" \
  -e SMBU_CAS_BASE_URL="http://smbu-mock:8799" \
  smbu-calendar:latest

E2E_BASE=http://127.0.0.1:8770 E2E_TLS=0 \
  python _probe/e2e_https.py        # -> 14/14 passed
```

## ⚠️ 校园网准入限制 与 宿主机代理（已解决）

**问题**：容器直连教务 jwapp 会被拒（TLS `SSLV3_ALERT_HANDSHAKE_FAILURE`），
而 Windows 宿主机可以。逐项排除了 MTU（两端均 1500）、ALPN、OpenSSL SECLEVEL 0/1/2、
强制 TLS1.2、OpenSSL 版本（两端同 3.5.7）、`--network host`（仍失败）。
容器访问公网（pypi/baidu）完全正常 → 容器 TLS 与出网本身没问题。

| 来源 | 目标 | 结果 |
|---|---|---|
| 宿主机 | `jw.smbu.edu.cn` → 内网 `10.100.14.12` | ✅ TLSv1.2 握手成功 |
| 容器 | 同上内网 IP | ❌ `SSLV3_ALERT_HANDSHAKE_FAILURE` |
| 宿主机 | jwapp 公网 `121.15.0.124` | ❌ 同样失败（jwapp 只认内网入口） |
| 容器 | 公网任意站点（pypi / baidu） | ✅ 200 |
| 容器 `--network host` | 校园内网 | ❌ 仍失败 |

**结论**：校园网设备按来源做准入（宿主机有线网已认证，WSL2 虚拟网卡未授权），
非镜像或应用缺陷。

**解决方案：宿主机代理**（`deploy/proxy/host_proxy.py`）
让容器经由**跑在宿主机上**的代理出网——代理的连接来自校园网已信任的宿主机地址。
实测：容器经代理后 jwapp 302、CAS 200（能取到 `pwdEncryptSalt`），**真实账号
登录 → 学期 → 课表 → `.ics` 全链路成功**。

```text
浏览器 ──▶ 容器 smbu-app:8770 ──▶ host.docker.internal:8899 ──▶ 教务系统
                                   （跑在 Windows 宿主机上）
```

应用侧已支持：`SMBU_UPSTREAM_PROXY=http://host.docker.internal:8899`（显式配置，
仍不继承环境里的 HTTP_PROXY，避免被无关代理劫持流量）。

**安全**：代理带**目标白名单**（默认仅 `smbu.edu.cn`），非校园目标一律 403，
不会变成局域网里的开放中继（已实测 baidu 被拒）。

**启动代理**（容器能用之前必须先起它）：

```text
双击  deploy\proxy\start_proxy.cmd        ← 最简单，窗口最小化即可
或命令行：
  backend\.venv\Scripts\pythonw.exe deploy\proxy\host_proxy.py \
      --bind 0.0.0.0 --port 8899 --log-file deploy\proxy\proxy.log
```

> 代理必须跑在**宿主机**（不是容器）里，所以它不能被 Docker 托管；
> 开机自启请把它加进启动项或用计划任务（本沙箱禁止创建计划任务，需手动）。

**部署到其他机器时**：若那台机器本身在校园网内可直连 `10.100.x`（如实验室服务器），
容器**不需要**代理，直接 `docker run` 即可；此时不设 `SMBU_UPSTREAM_PROXY`。

## 运维

```bash
docker logs -f smbu-app                 # 日志
docker inspect --format='{{.State.Health.Status}}' smbu-app
docker restart smbu-app
docker rm -f smbu-app                   # 停止并删除
```

### 排障：容器 healthy 但宿主机/浏览器连不上端口

症状：`ERR_CONNECTION_REFUSED` 或 `服务器断开了连接`，容器内 healthcheck 却是 200。

1. 先看容器是不是退了：`docker ps -a`。若显示 `Exited(255)`，是 Docker/WSL2 重启
   （休眠唤醒等）导致，`docker start smbu-app` 即可（加了 `--restart unless-stopped` 会自愈）。
2. 若容器 healthy 但宿主机访问失败：**该宿主机端口的转发中继坏了**（Docker Desktop
   重启后的已知怪癖）。`com.docker.backend` 会 accept 连接然后立刻关闭。
   解决：**换一个宿主机端口重建容器**（实测 8770 坏、8771 正常）：

   ```bash
   docker rm -f smbu-app
   docker run -d --name smbu-app -p 8771:8770 --restart unless-stopped \
     -e SMBU_UPSTREAM_PROXY="http://host.docker.internal:8899" ... smbu-calendar:latest
   ```

3. 代理没起也会导致"页面能开、登录 503"：先 `netstat -ano | findstr :8899` 确认
   `start_proxy.cmd` 在跑。

数据：支付账本写在容器内 `/app/backend/data/payments.db`。正式使用请挂卷持久化：

```bash
-v smbu-data:/app/backend/data
```

> 注意：容器内支付库默认空且 `payments_enabled=false`；启用支付前请配置
> `SMBU_PAYMENT_CENTS` 与 `SMBU_PAY_CALLBACK_SECRET`（见 `LAUNCH_CHECKLIST.md` 第七节）。

---

## 线上部署（阿里云 + 域名 + 开源）

> 方向已定（2026-09-06）：**放弃支付，改为免费开源工具 + 公网部署**。下列为落地清单。

### 会不会被挤爆？

**不会轻易被挤爆。** 真实瓶颈是校园教务系统本身（它按源 IP 限流），而学生端每次课表
抓取很便宜、且按会话缓存。应用内已分层防护（`backend/app/config.py`）：

| 防线 | 值 | 作用 |
|---|---|---|
| 每 IP 请求 | 30/min（滑动窗口） | 防单 IP 刷接口 |
| 每学生 | 10/min | 防单账号滥用 |
| 登录-每 IP | **60/min**（已对齐，原默认 6 是 bug：校园 NAT 下全校共享 1 个源 IP，6/min 会误伤全校） | 防登录洪流 |
| 登录-每学号 | 6/min | 防单账号撞库 |
| 全局上游并发闸 `UpstreamGate` | 32 | **排队机制**：超出即排队，校园门户零过载 |
| WebVPN 登录并发闸 | 2 | 最贵操作（浏览器握手），死死压住 |
| 会话 | 5000 上限 + 900s TTL | 内存有界，不泄漏 |
| `trusted_proxies` | CIDR 白名单 | 防 XFF 伪造 IP 绕过限流 |

超出容量的请求得到 **429 + `Retry-After`**，不是进程崩溃——这就是"排队"。

### 边缘拦截（上阿里云必加，见 `deploy/nginx.conf`）

nginx 在流量抵达小后端之前先挡一层：

- `limit_conn perip_conn 30`：每 IP 并发连接上限，防连接耗尽
- `limit_req`：API 60/min、登录 120/min（边缘宽松，精确限流交给应用层；登录必须宽松，
  因为全校共享 1 个 NAT IP）
- CSP / `X-Frame-Options: DENY` / `nosniff` 等安全头

再往前可挂 **Cloudflare 免费档**（橙云）：L3/L4 洪水由它吃，源站只放行 CF 回源段。

### 一键部署（`deploy/docker-compose.yml` + `deploy/deploy.sh`）

`deploy.sh` 在 ECS 上以 root 运行，自动装 docker、构建、起容器、按备案情况发证书：

```bash
# 阶段1（默认，无需备案）：自签证书 + 高位端口 8443
bash deploy/deploy.sh
# 阶段2（备案完成后）：Let's Encrypt + 标准 443
LETSENCRYPT=1 EMAIL=you@swiftiejerry.xyz bash deploy/deploy.sh
```

或在阿里云机器上手敲：

```bash
git clone <你的仓库> smbu && cd smbu/smbu-calendar
docker compose -f deploy/docker-compose.yml up -d   # nginx:443 + app:8770，均 unless-stopped
docker ps                                        # 两个容器 healthy
```

nginx 默认读 `./certs`。小机型（1 核 1G）足够几百在校生。

### AI 协助 SSH 驱动部署（推荐）

已生成部署专用密钥对，公钥在 `C:/Users/Administrator/.ssh/id_ed25519_smbu_deploy.pub`。
流程：

1. 把该公钥追加进 ECS 的 `~/.ssh/authorized_keys`（阿里云控制台「发送远程命令」或你已有
   的 SSH 会话里执行 `echo '<pubkey>' >> ~/.ssh/authorized_keys`）。
2. 提供 ECS **公网 IP + SSH 用户名**（root 或具备 sudo 的账号）。安全组需放行 22（部署用）
   以及后续的 80/443/8443。
3. 助手在本机 `scp` 一份**已剔除密钥/PII/venv/node_modules** 的源码压缩包到
   `/opt/smbu-calendar`，SSH 上去 `bash deploy/deploy.sh`，全程可见。
4. DNS 在阿里云控制台把 `swiftiejerry.xyz` A 记录指向该 ECS 公网 IP。

> 出于安全，助手**绝不**索要你的服务器密码；用密钥登录。部署完可随时从
> `authorized_keys` 移除该公钥吊销访问。

### 阶段1 / 阶段2 与备案

- **备案状态不确定 → 先阶段1**：`deploy.sh` 默认生成自签证书、nginx 监听 **8443**，
  访问 `https://swiftiejerry.xyz:8443`。境内未备案域名在 80/443 会被阿里云约 24h 后拦截，
  所以阶段1 刻意走高位端口，立即可用、不影响后续切换。
- **备案完成后 → 阶段2**：`LETSENCRYPT=1` 重新跑 `deploy.sh`，certbot 签发 Let's Encrypt
  证书、nginx 切回 443（配置文件 `nginx.conf` ↔ `nginx.portal.conf` 由 `NGINX_CONF` 自动选）。
- **建议现在就去阿里云提交个人备案**（免费，约 1–2 周），备案期间阶段1 照常服务。

### 域名 swiftiejerry.xyz

1. **DNS**：A 记录指向阿里云 EIP（公网 IP）。`swiftiejerry.xyz` 与 `calendar.swiftiejerry.xyz`
   都指向同一 IP（nginx 已配两个 `server_name`）。
2. **TLS**：阶段1 自签（8443）；阶段2 `certbot` 签发 Let's Encrypt 到 `./certs`，或阿里云
   免费证书下载后挂进去。nginx 已配 80→443 跳转 + HTTP/2。
3. **ICP 备案（关键）**：swiftiejerry.xyz 解析到**境内**服务器、走 80/443，依法必须备案。
   未备案阿里云会在约 24h 后拦截 80/443。处置：
   - 现在就去阿里云**个人备案**（免费，约 1–2 周）；备案期间可先用高位端口 8443 自测；
   - 或把服务放**中国香港**节点（免备案，但延迟略高、需付费）；
   - 或 Cloudflare 橙云代理（部分场景可缓冲突执，但境内访问质量不稳定，不保证绕过备案）。

### GitHub 开源准备（已完成，待你确认推送）

- ✅ 根 `.gitignore`：屏蔽 `.env` / `node_modules` / `.venv` / `backend/data/` / `*.db` /
  `deploy/mock/raw/`（**真实学号 1120230636 + 姓名 + 课表已排除**）/ `*.log`
- ✅ `LICENSE`（MIT，© 2026 左典典）
- ✅ `SECURITY.md`：凭据模型（密码仅内存、不落盘）、已知边界、漏洞上报方式
- ✅ `deploy/README.md`：本部署章节
- ⏳ 根目录**还不是 git 仓库**，`git init` + 推送需你确认后执行（公开推送不可逆）
- ⏳ 推送前最后自检：确认无 `.env`、无 `deploy/mock/raw/`、无真实学号入库

```bash
# 推送前自检（应无输出）
grep -rIl "1120230636\|左典典" . --exclude-dir=node_modules --exclude-dir=.venv
# 确认 .env 未被跟踪
git status --porcelain | grep -i "\.env$"   # 应为空
```

