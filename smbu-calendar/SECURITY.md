# 安全模型（Security Model）

> 你定的死命令：作为付费服务，必须扛住 2026 年的经典网络攻击。本文逐项对账：
> 哪些已落地、哪些靠外层设施、哪些是上线前 checklist。

## 一、攻击面与防线总览

```
学生手机 ──HTTPS──▶ CDN/WAF（Cloudflare，抗 DDoS 第一层）
                        │
                        ▼
                 nginx（TLS 终止 + 限速 + XFF）
                        │
                        ▼
                 smbu-app 容器（本应用）
                        │  ← 上游闸门 32 并发 + 每学生 6 并发
                        ▼
                 教务系统（经 CA Bundle 校验 / 校园网）
```

## 二、逐项攻击对账

### 1. 泛洪 / DDoS ✅ 已落地 + 外层建议
**应用内（已实现）**
- 全局上游闸门 `max_concurrent_upstream=32`：300 并发压测实测被死死压在 32，校园门户零过载
- 每学生并发闸门 6；IP 30/min、学生 10/min、登录 60/min + **按学号 6/min**
- **请求体上限 64KB**（`BodySizeLimitMiddleware`，超限即刻 413，不进业务逻辑）——
  防大包打爆内存
- 会话 LRU 上限 5000 + 15min TTL；限流桶 idle 300s 自动清扫（防 map 无限增长）

**必须外层解决（应用层挡不住流量型 DDoS）**
- **Cloudflare 免费版套在前面**：L3/L4 洪水由它吃，源站 IP 用 A 记录+仅允许 CF 回源
  （nginx 防火墙白名单 CF 网段，防止绕过 CF 直连源站）
- nginx 层再加 `limit_req`（按 IP 秒级限速）

### 2. 域名劫持 / DNS 污染 ✅ 分层防御
- **对学生的劫持**：全站 HTTPS + HSTS（`SMBU_SECURITY_HSTS_ENABLED=true`，TLS 上线即开）。
  DNS 被污染指向假站时，浏览器会因证书不匹配直接报警
- **对教务上游的劫持**（**这个最阴险**：攻击者污染你的 DNS，让你把学生的账密发给假教务站）：
  - 应用默认 `verify=False`（校园证书链常年残缺），这等于**不防上游劫持**
  - **正解已实现**：导出校园真根证书 → `SMBU_UPSTREAM_CA_BUNDLE=/path/ca.pem` →
    httpx 用它校验上游证书。伪造主机没有合法链，**连接直接失败**（fail-closed）
  - DNS 侧再加一道：域名注册商开 **DNSSEC** + DNS 服务商配 **CAA 记录**
    （限定只有你的 CA 能给该域名签证书）
- 上游响应里的字段校验（`weeksAndTeachers` 解析、长度上限）防止恶意响应注入异常数据

### 3. 凭证填充 / 撞库 ✅
- 登录三重限流：IP 60/min + **学号 6/min**（同学号不互相饿死，单账号撞库被死锁）
- XFF 只信任 `SMBU_TRUSTED_PROXIES` 网段，直连伪造头**无法**轮换 IP 绕限流（已实测）
- 校园侧触发验证码时，应用原样透传 `AUTH_CAPTCHA_REQUIRED`（不吞不绕）

### 4. XSS / 点击劫持 / 注入 ✅
- CSP：`default-src 'self'; frame-ancestors 'none'`（本轮上线，容器内已验证）
- `X-Frame-Options: DENY`、`nosniff`、`Referrer-Policy: no-referrer`、COOP
- 前端不使用 `dangerouslySetInnerHTML`；ICS 内容全部转义（`_escape`）
- 无 SQL 注入面：用户数据不落盘；支付账本用参数化 SQL（`?` 占位）

### 5. 会话劫持 / CSRF / 重放 ✅
- token 256-bit 随机（`secrets.token_urlsafe(32)`），15 分钟 TTL，仅存 `sessionStorage`
- 自定义头 `X-Session-Token` —— 浏览器跨域无法凭空携带该头（天然 CSRF 免疫）
- 登出即销毁会话并关闭上游连接
- 密码用后即清（`payload.password=""`），全程只在内存，**从不落盘/不进日志**

### 6. 支付类攻击（接入支付后生效）⏳ 设计就绪
- 回调 **HMAC-SHA256 验签**，密钥未配置时 **fail-closed 拒绝全部回调**
- **金额以服务端为准**（订单创建时锁定 `amount_cents`，回调金额不符即拒）
- 网关 `trade_no` 全局去重（一笔回调只能结算一笔，防重放/防复用）
- 对账兜底：网关为准补单，幂等防重复入账（详见 PAYMENT.md 第三节）

### 7. 供应链 / 依赖 ⚠️ 待办
- `frontend/package-lock.json` 锁版本；Docker 构建里 `npm ci`
- `npm audit`：**已清零（2026-09-06）**——`vite` 5→7 升级清掉 esbuild 漏洞，当前 0 个
  已知漏洞。建议接入 Dependabot/Renovate 保持自动升级
- Python 侧依赖全部钉版本

### 8. 信息泄露 ✅
- `/api/debug/raw` 默认关闭（`debug_raw_endpoint=False`）
- 错误响应不吐堆栈，只给错误码；日志不含账密/凭证
- 容器非 root（uid 10001）运行；支付已放弃，`/app/backend/data` 卷现为可选（会话 RAM-only，不落盘）

## 三、上线前安全 Checklist

- [ ] Cloudflare 接入 + 源站只允许 CF 回源 IP
- [ ] nginx TLS（Let's Encrypt）+ `SMBU_SECURITY_HSTS_ENABLED=true`
- [ ] `SMBU_TRUSTED_PROXIES=<CF/nginx 网段>`
- [ ] 导出校园根证书 → `SMBU_UPSTREAM_CA_BUNDLE`（**上游劫持唯一硬防线，必做**）
- [x] `npm audit fix` 已清零（0 漏洞，2026-09-06）
- [ ] nginx：`limit_req`（见 `deploy/nginx.conf`）、`client_max_body_size 1m`、禁用不必要 HTTP 方法
- [ ] 开源：根 `.gitignore` 已屏蔽 `.env` / `deploy/mock/raw/` / `*.db`；确认无学号/姓名入库
- [ ] **上线境内服务器前完成 ICP 备案**（域名 swiftiejerry.xyz），否则 80/443 被云厂商拦截
