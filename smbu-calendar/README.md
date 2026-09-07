# SMBU Schedule Sync

把深圳北理莫斯科大学教务系统的课表，按学期（含入学以来全部学期）抓出来，生成可导入手机日历的 `.ics` 文件。

- **后端**：FastAPI + httpx，纯服务端完成 CAS 登录与课表抓取（无需浏览器 GUI）
- **前端**：React + Vite + TypeScript + Tailwind + Framer Motion，中/英/俄三语，自动 & 手动深色模式
- **凭证**：仅存于内存会话，服务端不落盘、不记录、不回传
- **压力**：Locust 压测脚本，含全局并发闸门与限流
- **支付**：聚合支付（微信+支付宝合一码）仅做接口与状态机骨架，默认关闭，优先级最低

## 架构

```
浏览器 ──HTTPS──▶ FastAPI ──httpx──▶ authserver.smbu.edu.cn (CAS)
                            └─httpx──▶ jw.smbu.edu.cn/jwapp  (强智 REST)
```

1. 前端把账密发到 `/api/auth/login`（密码仅在登录的那一次请求内存中使用，随即丢弃）
2. 后端完成 CAS 登录（用户名 + AES-CBC 加密密码），拿到 jwapp 会话 Cookie
3. 拉取学期列表、周次日期、节次时间表、课表，归一化为标准模型
4. 生成 RFC5545 `.ics`（每周每节一节课 = 一个 VEVENT，带 Asia/Shanghai 时区与课前提醒）

## 本地运行

```bash
# 后端
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8770

# 前端（开发）
cd frontend
npm install
npm run dev     # http://localhost:5173，/api 已代理到 8770

# 生产构建
npm run build   # 产物在 frontend/dist，FastAPI 自动托管
```

环境变量（`SMBU_` 前缀）见 `backend/app/config.py`。

## 字段校准（首次接真实教务系统）

不同校区 / 版本的强智字段命名不同。`backend/app/jw.py` 的归一化器已内置多套候选键名，
并配套离线单测 `backend/tests/test_parser.py`（7/7 通过）。

要拿到 SMBU 真实字段，跑 headless 抓取（走线上服务完全相同的代码路径，无需浏览器）：

```bash
SMBU_USER=2024xxxx SMBU_PASS='***' \
  python capture.py --out _probe/raw
```

输出 `_probe/raw/raw_<term>.json` 即接口的未处理原样 JSON。若字段名不在候选列表内，
按它调整 `jw.py` 中的 `normalize_*` 即可，无需改动其它部分。

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | CAS 登录，返回会话令牌 |
| POST | `/api/auth/logout` | 注销并销毁会话 |
| GET  | `/api/terms` | 学期列表（与教务系统对齐） |
| GET  | `/api/schedule?term=` | 课表预览（归一化 JSON） |
| GET  | `/api/export.ics?term=&lang=&reminder=` | 下载 .ics |
| GET  | `/api/health` | 健康检查 |
| GET  | `/api/debug/raw?term=` | 原始接口 JSON（字段校准用，可关闭） |
| POST | `/api/pay/order` | 创建支付订单（默认关闭） |

所有鉴权接口需 `X-Session-Token` 头。

## 压力测试

```bash
# 带预登录 token
SMBU_TOKEN=xxxx locust -f tests/locustfile.py --host http://127.0.0.1:8770 -u 200 -r 40 -t 3m
# 仅公开接口（无线缆）
locust -f tests/locustfile.py --host http://127.0.0.1:8770 -u 500 -r 80
```

关键保护：全局上游并发闸门（`max_concurrent_upstream`）、按 IP 与学号的滑动窗口限流、
会话 TTL/LRU 淘汰。突发流量会让延迟升高并优雅返回 429，而非击穿教务系统。

## 支付（骨架）

`backend/app/payments/`：幂等订单（`idempotency_key`）、状态机
（`CREATED→PAID/EXPIRED/REFUNDED`）、按网关交易号去重的回调（`mark_paid` 对重复回调是安全幂等），
防止漏单。真实渠道（聚合支付聚合码）通过 `PaymentGateway` 适配器接入，业务确定价格后开启
`payments_enabled`。
