# SMBU 课表同步 — 发布就绪清单（Launch Readiness）

**最后更新**：2026-09-02（晚间）
**当前状态**：后端闭环 ✅ / 前端动效 ✅ / 压力测试 PASS ✅ / **HTTPS 端到端 15/15 ✅** / 支付防漏单 ✅ → **可发布，仅剩域名·证书·价格三项业务决策**

本清单基于实际代码核对，不是泛泛模板。`✅`=已就绪，`🟡`=需你在发布前处理，`⬜`=待做/后置。

---

## 一、安全（发布前必须处理）

- [x] **密码不在服务端落盘**：`sessions` 纯内存（`infra.py` SessionStore），登录后 `payload.password=""`（`main.py:215`）。✅
- [x] **`.ics` 导出不缓存凭证**：`Cache-Control: no-store` + `Content-Disposition: attachment`（`main.py` export_ics）。✅
- [x] **`/api/debug/raw` 默认关闭**：已把 `debug_raw_endpoint` 默认值由 `True` 翻转为 `False`（`config.py`），生产绝不暴露原始上游报文；开发用 `SMBU_DEBUG_RAW_ENDPOINT=true` 开启。✅（本次修复）
- [x] **CORS 不再非法组合**：原来 `allow_origins="*"` + `allow_credentials=True`（浏览器拒绝）。现已改为：origins 含 `*` 时不发 credentials（`main.py`）。生产为同源单体，CORS 本就不触发。✅（本次修复）
- [🟡] **HTTPS/TLS 强制**：应用本身只跑明文 HTTP，**学生密码必须走 TLS**。由反向代理（nginx/Caddy）或负载均衡终止 TLS。提供 `deploy/nginx.conf`（Let's Encrypt 示例）。**上线前必配。**
- [🟡] **`SMBU_ALLOW_ORIGINS` 设为真实域名**：默认仍是 `*`，发布前在 `.env` 里改成 `https://你的域名`。
- [🟡] **密钥管理**：当前应用不持有长期密钥（用学生自己的 CAS 账密 + 内存随机 token）。**支付启用后**，微信/支付宝网关密钥必须走环境变量/密钥库，**绝不硬编码**。

---

## 二、部署形态（本次补齐了脚手架）

> 架构是**单体**：FastAPI 同时提供 `/api/*` 和构建后的 SPA（`FRONTEND_DIST = <项目>/frontend/dist`，`main.py` 末尾 `StaticFiles` 挂载 + SPA fallback）。前端用**相对路径**调 API（`src/lib/api.ts`），所以生产同源、无需跨域。

- [x] 之前**完全没有部署文件**，现已补齐 `deploy/`：
  - `deploy/Dockerfile` — 多阶段（node 构建前端 → python 运行，含 frontend/dist）。
  - `deploy/nginx.conf` — TLS 终止 + 反代（同源单体，单 `location /` 覆盖）。
  - `deploy/smbu-calendar.service` — systemd 单元（自动重启、`www-data`、`TimeoutStopSec` 防闸门泄漏）。
  - `deploy/.env.example` — 生产环境变量模板。
- [🟡] **选一种运行方式**：
  - 简单单机：`uvicorn app.main:app --host 127.0.0.1 --port 8770 --workers 2` + nginx 反代（推荐，见 service 文件）。
  - 容器：用 `deploy/Dockerfile`，外层再套 nginx/Caddy 做 TLS。
- [🟡] **构建前端**：`cd frontend && npm install && npm run build` → 产出 `frontend/dist`，后端自动挂载。
- [⬜] **无状态化（仅当多实例）**：sessions 在内存，重启即全员掉线（需重登）。v1 单实例可接受；若上负载均衡多实例，需 Redis 共享会话或粘性会话。

---

## 三、运行时加固

- [x] **上游并发闸门**：`max_concurrent_upstream=32` 全局 + 每学生 `6`，压测实测在途峰值恰为 32，校园门户零过载。✅
- [x] **限流**：IP / 学生 / 登录 三重滑动窗口（`infra.py` SlidingWindowLimiter），压测下超额优雅 429 而非雪崩。✅
- [x] **存活探针**：`/api/health` 返回 `status/upstream_in_flight/upstream_limit`，可直接接 Liveness/Readiness。✅
- [🟡] **日志/可观测**：目前仅 `logging`。发布前建议加访问日志 + 错误聚合（Sentry 可选），并配置日志轮转。
- [🟡] **反向代理超时**：nginx 已设 `proxy_read_timeout 30s`（对齐应用 `read_timeout_s=15` + 重试）。

---

## 四、域名与 DNS

- [🟡] **域名**：指向你的服务器（如 `calendar.yourdomain.com`）。注意这是给 SMBU 学生的**第三方工具**，若想用 `*.smbu.edu.cn` 子域需学校授权；建议用自己的域名。
- [🟡] **CORS/origin 与域名一致**（见一）。
- [🟡] **证书**：Let's Encrypt（nginx.conf 已留路径）或云厂商免费证书。

---

## 五、隐私与合规

- [x] 凭证不落盘、会话 15 分钟 TTL（`session_ttl_s=900`）、导出 `no-store`。✅
- [🟡] 建议在页面/仓库补一段简短隐私说明：说明"仅用你的账密临时登录教务系统抓取课表，不在服务端存储密码与课表"。

---

## 六、灰度发布

- [🟡] **分阶段**：内部自测（你）→ 少量可信同学 → 公开。监控错误率与上游闸门饱和度（`/api/health` 的 `upstream_in_flight`）。
- [x] 支付开关默认关闭（`payments_enabled=False`），不影响灰度。✅

---

## 七、聚合支付（一码双付 + 防漏单）

已从"接口骨架"升级为**可防漏单的真实实现**（`app/payments/`：models / ledger / gateway）。默认仍关闭（`payments_enabled=False`），开启即可用。

**防漏单三道防线**（均已实现 + 测试覆盖 9/9）：
- [x] **① 持久化**：订单落 SQLite（`backend/data/payments.db`，WAL + `synchronous=FULL`），
      进程重启/断电都不丢单 —— 原先是内存 dict，重启即漏单（已修）。
- [x] **② 幂等**：按**网关** `trade_no` 全局去重（`processed_trades` 表），重复回调只入账一次，
      不会重复扣款；同一 trade 不能结算两笔订单。
- [x] **③ 对账**：`reconcile()` 定时向网关拉取已结算交易，补记本地漏掉的订单；
      **连"已被我们误判过期、但网关确认已支付"的订单也能补单**（`allow_expired`）。
      已接入 `_sweep_loop`（每分钟），只统计真正的补单（`healed`），无误报噪音。

**安全**
- [x] 回调 HMAC-SHA256 验签（`order_id|trade_no|amount_cents`）；**未配置
      `SMBU_PAY_CALLBACK_SECRET` 时 fail-closed 拒绝所有回调**，宁可漏单也不认伪造。
- [x] 一码双付：`create_aggregate_code` 返回单一聚合码，微信/支付宝扫同一个码，渠道由网关回报。

**仅剩业务侧决策（⬜）**
- [⬜] 定价格：`SMBU_PAYMENT_CENTS`
- [⬜] 提供微信/支付宝聚合网关密钥 → 走 env（`SMBU_PAY_CALLBACK_SECRET`），**绝不硬编码**
- [⬜] 实现真实 adapter：继承 `PaymentGateway`，覆写 `create_aggregate_code` /
      `verify_callback` / `list_settled_trades`（接口已定，无需改业务代码）
- [⬜] 启用后补一轮压测（回调并发 + 幂等）

## 八、HTTPS 端到端验证（已完成，15/15）

在本地以**真实 HTTPS**（自签证书，`_probe/tls/`）起服务并跑通全链路：

- ✅ 明文 HTTP 被拒（证明 TLS 真的生效，`RemoteProtocolError`）
- ✅ `/api/health`、SPA 首页、静态资源（292KB JS）、**深链接 fallback → index.html**
- ✅ 登录 → 20 个学期 → 课表 28 events → `.ics` 下载
- ✅ `.ics` 校验：`text/calendar` + `attachment` 文件名 + 28 个 VEVENT +
      `Asia/Shanghai` 时区 + `VALARM` 提醒
- 脚本：`_probe/e2e_https.py`（含证书生成 `_probe/make_localhost_cert.py`）

> 修复：原先 `StaticFiles` 挂载在 `/` 会拦截所有路径，导致 `/{full_path:path}` 兜底路由
> **永远不可达**（深链接 404）。改为作用域化的 404 处理器：无扩展名且非 `/api/` 的路径
> 才回落到 SPA，缺失的真实资源（如 `.js`）仍正确 404。

---

## 九、Docker 部署（镜像已构建并验证）

- [x] `deploy/Dockerfile`：多阶段（node 编译 SPA → python 运行），镜像 **266MB**，
      构建上下文仅 **~320KB**（新增 `.dockerignore` 排掉 node_modules 82MB + .venv 62MB）。
- [x] 非 root 运行（`uid 10001`）+ `HEALTHCHECK`（`/api/health`，15s 启动宽限）。
- [x] **单 worker**（重要）：会话在内存，多 worker 会让用户随机掉线；扩容靠多容器+粘性会话。
- [x] 容器内端到端 **14/14 通过**：登录 → 20 学期 → 课表 28 events → `.ics`
      （走 `deploy/mock/`，离线可复现，不碰真实账号）。
- [x] `deploy/README.md`：构建/运行/离线自检/运维命令齐备。
- [🟡] **本机限制**：容器访问不到教务 jwapp（校园网基于来源的准入策略；
      宿主机 Windows 可以，WSL2 容器不行）。详见 README 完整证据链。
      → 需跑在校园网内可直连 `10.100.x` 的宿主机，或本机直接 uvicorn。
- [⬜] 上线时：挂卷持久化 `/app/backend/data`；外层 nginx/Caddy 终止 TLS。

## 发布前最小动作（Checklist of checklists）

1. `npm run build` 产出 `frontend/dist`
2. 配 `backend/.env`：`SMBU_ALLOW_ORIGINS=真实域名`、`SMBU_DEBUG_RAW_ENDPOINT=false`
3. 部署 + nginx TLS（或 Caddy），强制 HTTPS 跳转
4. 启动服务（systemd / 容器），`curl /api/health` 探活
5. 用真实账号走一遍：登录 → 选学期 → 导出 `.ics` → 手机日历导入
6. 支付保持关闭，待业务定价格与网关密钥后再开
