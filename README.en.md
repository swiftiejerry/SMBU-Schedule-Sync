<div align="center">

# 📅 SMBU Schedule Sync

**Turn the SMBU academic-system timetable into your phone calendar — in one click.**

Enter your CAS credentials → the server fetches any semester's timetable → download an `.ics` file and import it into iOS / Android / desktop calendar.

[简体中文](README.md) · [English](README.en.md) · [Русский](README.ru.md)

</div>

---

## ✨ Screenshots

| Light | Dark |
|---|---|
| ![Timetable preview — light](docs/screenshots/timetable-light.png) | ![Timetable preview — dark](docs/screenshots/timetable-dark.png) |

Pick a semester → preview the full timetable → choose a pre-class reminder → download the `.ics` and import it. **Every semester since freshman year** is supported; re-importing a semester deduplicates and refreshes events automatically.

## 🚀 Features

- **Genuinely one-click** — CAS login + QiangZhi academic-system scraping happen entirely server-side; nothing to configure on your phone
- **Full semester history** — fetch any term since you enrolled
- **Trilingual UI** — 简体中文 / English / Русский
- **Dark mode** — follows the system automatically, manual override included
- **RFC 5545-compliant `.ics`** — Asia/Shanghai timezone, pre-class reminders (off/10/15/20/30 min), exact week + section mapping
- **Your password stays yours** — credentials live only in an in-memory session; never written to disk, never logged, never sent anywhere else, wiped on logout

## 🏗️ How it works

```
Browser ──HTTPS──▶ FastAPI ──httpx──▶ authserver.smbu.edu.cn (CAS)
                             └─httpx──▶ jw.smbu.edu.cn/jwapp  (QiangZhi REST)
```

1. The frontend posts credentials to `/api/auth/login` (the password is used once, in memory, then discarded)
2. The backend performs the CAS login (username + AES-CBC-encrypted password) and obtains the jwapp session cookie
3. It pulls the term list, week dates, section times and the timetable, normalizing everything into one model
4. It generates the `.ics`: one VEVENT per weekly section, with timezone and reminders

> Off campus, the backend automatically tunnels through WebVPN (Srun + headless-browser handshake) — see [WEBVPN.md](WEBVPN.md) for details.

## 📦 Quick start (self-hosted Docker)

```bash
docker build -f deploy/Dockerfile -t smbu-calendar:latest .
docker run -d --name smbu-app -p 8771:8770 --restart unless-stopped \
  smbu-calendar:latest
# open http://localhost:8771
```

Full deployment (TLS / edge rate-limiting with nginx / WebVPN access mode / offline mock load-testing) is documented in [deploy/README.md](deploy/README.md).

### Local development

```bash
# backend
cd backend && pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8770

# frontend
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Environment variables (prefixed `SMBU_`) are documented in [backend/app/config.py](backend/app/config.py).

## 🔒 Security & privacy

- The password appears exactly once, in the memory of the login request, and is gone right after; there is no database — sessions live in memory and expire automatically
- A global upstream-concurrency gate plus sliding-window rate limits per IP/student return graceful 429s under burst traffic instead of hammering the academic system
- Load-test report: [docs/STRESS_REPORT.md](docs/STRESS_REPORT.md)

## ⚠️ Disclaimer

This project is not affiliated with Shenzhen MSU-BIT University. It is intended for personal timetable management only. Please do not abuse the fetching features; you are solely responsible for any consequences of using this tool.

## 📄 License

[MIT](LICENSE) © 2026 D Zuo ([swiftiejerry](https://github.com/swiftiejerry))
