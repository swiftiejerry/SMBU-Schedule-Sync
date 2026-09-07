# WebVPN 接入蓝图（校外访问 · 真实账号全链路验证通过）

> 回答"凭什么假设学生都在校内"——你说得对。本文是 2026-09-06 用真实账号
> 实测出来的完整技术方案。**最终架构：浏览器只做登录，httpx 做数据面。**

## 〇、最终架构（2026-09-06 定案，真实账号验证）

```
浏览器（系统 Edge/Chrome headless，~16s，只做这一件事）
    隧道入口 → 门户弹回(redirect_uri) → CAS(AES 密码, Enter 提交)
    → cas_validate → 隧道会话建立 → currentUser 200
cookie 交接
    ctx.cookies() 导出 → 逐条注入全新 httpx client（host-only 域保留）
数据面（httpx keep-alive）
    JwClient(home_root=隧道根) —— 解析/三语/ICS 与直连模式 100% 共用
```

**为什么必须用浏览器登录（实测结论，三条硬证据）：**

1. 网关按 **TLS 指纹分流**：python(httpx) 裸访问隧道子域，拿到的是 jwapp
   **原生 CAS 流**——其票据回跳指向**直连域** `jw.smbu.edu.cn`，而直连域对
   校外 IP 直接 `SSLV3_ALERT_HANDSHAKE_FAILURE`（实测 A/B：冷却 50s 后门户
   200 / authserver 200 / 直连 jw 仍被拒）。死路。
2. 浏览器拿到的是**门户弹回流**，且由 **JS + 深澜 window.name 中继**
   （`sf_ssl_ms_...`）驱动；门户对"已登录 + redirect_uri"只回
   `{"msg":"User has logged in."}` 数据页——无法用 HTTP 库可靠重放。
3. 浏览器走完 SSO 后落下的会话 cookie 对**任何客户端**有效：实测 32 条
   cookie 交给 httpx，`currentUser.do` 直接 200 JSON。

三坑在浏览器架构下的形态：
- 坑 1 握手限频 → 登录 ~3 次握手/用户 + 数据面 1 次/会话；`_LoginGate`
  进程级并发登录上限（`SMBU_WEBVPN_HANDSHAKE_LIMIT`，默认 2）
- 坑 2 cookie 域/SSO 链 → 浏览器原生处理，**消失**
- 坑 3 连接池污染 → 登录与数据面 client 天然分离，**由构造消除**

## 一、结论（已用真实账号验证到哪一步）

| 步骤 | 状态 |
|---|---|
| ① 门户入口定位 | ✅ `webvpn.smbu.edu.cn/public/cas_login?entry_id=1` |
| ② 跳转到学校 CAS | ✅ 同一套 CAS（authserver.smbu.edu.cn，AES 复用 cas.py 逻辑） |
| ③ headless CAS 登录 | ✅ 浏览器版：表单 Enter 提交一次过（需验证码时截图 data-URI 返还前端） |
| ④ 隧道访问 jwapp | ✅ **真实账号全链路**：登录 16.1s → currentUser → 20 学期 → 28 VEVENT ICS |
| ⑤ 产品代码 + 测试 | ✅ `app/webvpn.py` 浏览器版；pytest 全量 34/34 |
| ⑥ Docker 容器内验证 | ✅ 镜像内置 chromium（playwright 1.62.0，非 root uid 10001）；`SMBU_ACCESS_MODE=webvpn` 下真实账号 HTTP e2e：login 5.7s → 20 学期 → 28 VEVENT (13,561B)；日志 `login ok ... via webvpn`；两次独立登录的 ICS 除 DTSTAMP 外逐字节一致 |

**架构结论：全程上云可行**（你同学的路子是对的）——云服务器经 WebVPN 访问教务，
不需要任何校内机器。我之前"找台校内机器"的建议是绕远路，收回。

## 二、完整流程（实现即照此写）

```
1. GET  webvpn.smbu.edu.cn/public/cas_login?entry_id=1
        → 302 → authserver.../login?service=https://webvpn.smbu.edu.cn/auth/cas_validate?entry_id=1
2. POST CAS 登录（复用 cas.py 的 AES 加密 + 表单字段）
        → 302 → webvpn.smbu.edu.cn/auth/cas_validate?ticket=ST-xxx
        → 门户页 /portal/?data=base64({"code":1,"msg":"authenticationSuccess"})
        → 会话 cookie：TWFID（深澜）、CASTGC（CAS TGT）、JSESSIONID、route
3. GET  jw-smbu-edu-cn-s.webvpn.smbu.edu.cn:8118/jwapp/...
        → 未带会话时 302 弹回门户（cas_login?redirect_uri=...）
        → 跟随整条 SSO 跳转链（门户→CAS→cas_validate→隧道）后即认证
        → 之后直接调 jwapp 的 REST API（与直连模式相同的接口族）
```

子域名规则：`jw.smbu.edu.cn` → `jw-smbu-edu-cn-s.webvpn.smbu.edu.cn:8118`
（`-s` = server；端口 8118）。

## 三、三个必须处理的坑（httpx 直连方案时代的实测；浏览器方案下已消解/演化）

> 以下为第一代（纯 httpx 重放 SSO）方案的历史记录，保留作诊断背景。
> 浏览器辅助架构下的最终形态见第〇、四节。

### 坑 1：网关按来源 IP 限制 TLS 握手频率（最重要）
- 现象：短时间多次握手后 `SSLV3_ALERT_HANDSHAKE_FAILURE`（服务器主动拒）；
  静默 ~45s 后单次握手恢复
- 实测：调试期间连续几十次握手 → 全部被拒；静默 45s → 一次成功
- **影响**：所有用户共享云服务器一个出口 IP，握手预算是全局的
- **解法**（写进 WebVpnClient 设计）：
  - **每用户会话复用一条 keep-alive 连接**，把握手次数压到最低（登录时 1 次握手
    走完整条 SSO 链，之后所有 jwapp 调用走同一条连接）
  - 全局握手信号量（如 2/s）+ `ConnectError` 指数退避重试（1.5s/3s/6s）
  - 会话过期重建时优先复用连接，避免重新握手

### 坑 2：cookie 域与 SSO 跳转链
- `TWFID` 等 cookie 的 domain 绑定在 `webvpn.smbu.edu.cn`，隧道子域
  `jw-smbu-edu-cn-s.webvpn.smbu.edu.cn` **收不到**（已实测：带 cookie 直访问隧道仍 302）
- **解法**：不要手动拼 cookie，**让 follow_redirects 跑完整条 SSO 链**
  （隧道 302 → 门户 cas_login → CAS（有 CASTGC 直接发票）→ cas_validate → 隧道落下自己的会话）
  —— 一个 cookie jar 走完全程，httpx 自动按域分发

### 坑 3：连接池污染（httpx 特有，已定位）
- 现象：同一 client 完成 CAS 登录后，**该 client** 对隧道域的 TLS 握手稳定失败；
  换全新 client（甚至裸 socket）立刻成功 —— 与 keep-alive 旧连接共存相关
- **解法**：登录链与隧道数据面**分开管理连接**：
  - 方案 a（推荐）：登录完成后 `aclose()` 登录 client，用携带全部 cookie 的
    **新 client** 做隧道数据面（一次握手，之后 keep-alive）
  - 方案 b：全程 `Connection: close`（牺牲 keep-alive，握手数上升，与坑 1 冲突，不推荐）
- 已验证：新 client + 全 cookie 隧道 TLS 立即成功

## 四、产品化设计（已实现 · 浏览器辅助版，2026-09-06）

```
app/webvpn.py   WebVpnClient（浏览器辅助）
  ├─ login(username, password, captcha="")   # _browser_cookies(): playwright 驱动真实 SSO 链
  ├─ client_from_cookies(cookies)            # 导出 jar → httpx 数据面（host-only 保留）
  ├─ refresh_tunnel_session(client)          # 无凭据重认证：旧 jar 种回浏览器 → SSO 自动重放
  └─ login_gate                              # 进程级并发登录上限（坑 1）

config（SMBU_ 前缀）:
  ACCESS_MODE=auto|direct|webvpn        # auto: 直连优先，仅传输级失败降级 WebVPN；
                                        #   凭据错误绝不降级（避免二次烧 CAS 尝试）
  WEBVPN_TUNNEL_HOSTS=jw.smbu.edu.cn=jw-smbu-edu-cn-s.webvpn.smbu.edu.cn:8118
  WEBVPN_HANDSHAKE_LIMIT=2              # 并发浏览器登录上限
  WEBVPN_BROWSER_CHANNEL=msedge         # msedge|chrome|""(bundled chromium；容器内置空)
  WEBVPN_LOGIN_TIMEOUT_S=60
```

- `jw.py` 的 `JwClient(home_root=...)`：解析层 100% 复用，前端零改动
- 验证码流程：CAS 弹验证码时浏览器截图 → data-URI 作为 `captcha_url` 返还前端
  → 用户提交后携带 captcha 二次 login（浏览器填入 `#captchaResponse`）
- 测试：`tests/test_webvpn.py` 12/12 —— 浏览器层 patch、数据面对 **cookie 驱动**
  的 MockTransport（与真实网关判定一致）；覆盖登录成功/验证码/密码错误/浏览器
  异常包装/并发门/cookie 交接/refresh/FastAPI e2e(webvpn)/auto 直连故障降级
- 真实账号集成验证：`_probe/webvpn_browser_handoff.py`（架构证明）、
  `_probe/webvpn_product_check.py`（产品码全链路）、诊断族
  `webvpn_diag/ab_probe/portal_hunt/csp_hunt/portal_bounce.py`
- 部署：Dockerfile 已内置 playwright + chromium；容器环境
  `SMBU_WEBVPN_BROWSER_CHANNEL` 默认空（用内置 chromium），宿主机默认 msedge

## 五、验证脚本

- `_probe/webvpn_probe.py`   — 端到端探针（登录→隧道→currentUser）
- `_probe/webvpn_decisive.py` — 坑 1/2/3 的定位实验（四组对照）
- 复现：`SMBU_USER=xxx SMBU_PASS=xxx python _probe/webvpn_probe.py`
