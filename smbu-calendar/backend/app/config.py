"""
Central configuration.

Everything tunable lives here so the service can be re-pointed at a different
campus deployment without touching business logic.
"""

from __future__ import annotations

import os
from typing import Dict

from pydantic_settings import BaseSettings


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseSettings):
    # ---------------------------------------------------------------- campus
    # Direct (non-WebVPN) entry point. Reachable from off-campus networks, so
    # the WebVPN tunnel is never required.
    jw_base_url: str = "https://jw.smbu.edu.cn"
    jw_context_path: str = "/jwapp"
    cas_base_url: str = "https://authserver.smbu.edu.cn"
    cas_login_path: str = "/authserver/login"

    # ------------------------------------------------------------- transport
    connect_timeout_s: float = 5.0
    read_timeout_s: float = 15.0
    max_retries: int = 2
    retry_backoff_s: float = 0.4

    # Bound how many upstream calls one request may make, and how many
    # upstream calls the whole process may have in flight. These are the two
    # knobs that keep the service alive when traffic spikes.
    max_concurrent_upstream: int = 32
    per_student_upstream_limit: int = 6

    # ---------------------------------------------------------- session mgmt
    # Sessions are held in RAM only. Nothing is written to disk, ever.
    session_ttl_s: int = 900
    session_max_entries: int = 5_000

    # ------------------------------------------------------------- rate limit
    # Sliding-window limiter. Applied per client IP and per student id.
    rate_limit_per_minute_ip: int = 30
    rate_limit_per_minute_student: int = 10
    login_rate_limit_per_minute_ip: int = 60

    # Second login bucket keyed by *username*. Docker bridge / campus NAT
    # collapse every student onto one source IP, so an IP-only bucket makes
    # 6/min the budget for the whole school. The per-user bucket keeps one
    # account's retries from starving — or being starved by — everyone else,
    # and still caps brute-force on any single account.
    login_rate_limit_per_minute_user: int = 6

    # Comma-separated CIDRs allowed to set X-Forwarded-For (your nginx / ALB).
    # Empty = trust nobody: XFF from a raw client is ignored, so a direct
    # connection cannot spoof its way past the per-IP limiter.
    trusted_proxies: str = ""

    # ------------------------------------------------------------ hardening
    # Cap inbound bodies so a flood cannot exhaust memory (this service only
    # ever receives a username/password or a small JSON payload).
    max_body_bytes: int = 64 * 1024

    # HSTS: enable ONLY once TLS terminates in front of the app, otherwise a
    # browser pins https for a server that may still speak plain HTTP.
    security_hsts_enabled: bool = False

    # CSP. 'unsafe-inline' is needed for the Vite bundle's inline bootstrap;
    # tighten to a nonce-based policy if the SPA ever stops emitting it.
    security_csp: str = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )

    # Optional CA bundle for verifying the campus TLS chain. Empty keeps the
    # permissive default (campus chains are routinely broken); pointing this at
    # the real root certificate is the real defence against a hijacked DNS
    # answer, because a forged host with no valid chain then fails closed.
    upstream_ca_bundle: str = ""

    # ------------------------------------------------------------ webvpn
    # How the academic system is reached. Students are NOT all on campus, so
    # the Srun WebVPN tunnel is a first-class path, not an afterthought:
    #   direct — the public jw.smbu.edu.cn entry only
    #   webvpn — force the webvpn.smbu.edu.cn portal + tunnel
    #   auto   — direct first; on a TRANSPORT-level failure fall back to the
    #            tunnel. Wrong credentials are never a fallback trigger (they
    #            are wrong on every path and retrying burns CAS attempts).
    access_mode: str = "auto"

    webvpn_portal: str = "https://webvpn.smbu.edu.cn"
    webvpn_entry_id: int = 1

    # source host → Srun tunnel authority, "host=target[:port]" pairs. The
    # tunnel subdomain encodes the origin host; `-s` marks the HTTPS service.
    webvpn_tunnel_hosts: str = "jw.smbu.edu.cn=jw-smbu-edu-cn-s.webvpn.smbu.edu.cn:8118"

    # The WebVPN gateway throttles TLS handshakes PER SOURCE IP (measured:
    # SSLV3_ALERT_HANDSHAKE_FAILURE; ~45 s of silence restores one), and every
    # user of this service shares ONE cloud egress IP. A browser login costs
    # ~3 handshakes and the data plane one per session, so concurrent logins
    # are capped process-wide.
    webvpn_handshake_limit: int = 2

    # Browser-assisted login (see app/webvpn.py): a headless system browser
    # walks the student SSO chain, then hands the cookie jar to the httpx
    # data plane. Channel: "msedge" | "chrome" | "" (bundled chromium).
    webvpn_browser_channel: str = "msedge"
    webvpn_login_timeout_s: float = 60.0

    # --------------------------------------------------------------- payload
    max_ics_events: int = 2_000
    ics_reminder_minutes: int = 15
    ics_product_id: str = "-//SMBU//Schedule Sync//CN"

    # ------------------------------------------------------------------ misc
    allow_origins: str = "*"
    debug_raw_endpoint: bool = False
    upstream_verify_tls: bool = False  # campus certs are frequently mis-chained

    # Explicit outbound proxy (e.g. "http://host.docker.internal:8899").
    # Empty = connect directly. Only used when set; ambient HTTP_PROXY vars are
    # still ignored, so a stray env var cannot silently redirect campus traffic.
    upstream_proxy: str = ""

    # ------------------------------------------------------ payments (deferred)
    # Priority is explicitly low in the brief. The order model + idempotent
    # ledger exist; real channel wiring stays off until the business says go.
    payments_enabled: bool = False
    payment_cents: int = 0  # TODO(business): set the real price

    # Orders are written to SQLite so a restart cannot drop a paid-but-
    # un-settled order (漏单). Empty = <project>/data/payments.db
    payment_db_path: str = ""

    # HMAC key the aggregator signs callbacks with. When payments are enabled
    # and this is empty the gateway FAILS CLOSED (rejects every callback)
    # rather than trusting an unsigned "paid" message.
    payment_callback_secret: str = ""

    # Reconciliation sweep: ask the gateway what actually settled and heal any
    # order whose callback never arrived.
    payment_reconcile_enabled: bool = True
    payment_reconcile_window_s: int = 3_600

    @property
    def jw_home_root(self) -> str:
        """Absolute base of the homeapp REST surface."""
        return f"{self.jw_base_url}{self.jw_context_path}/sys/homeapp"

    @property
    def jw_entry_url(self) -> str:
        """The URL that triggers the CAS handshake."""
        return f"{self.jw_base_url}{self.jw_context_path}/sys/homeapp/index.do"

    @property
    def webvpn_tunnel_map(self) -> Dict[str, str]:
        """Parsed `host=target[:port]` pairs from `webvpn_tunnel_hosts`."""
        mapping: Dict[str, str] = {}
        for pair in self.webvpn_tunnel_hosts.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            src, dst = pair.split("=", 1)
            if src.strip() and dst.strip():
                mapping[src.strip().lower()] = dst.strip()
        return mapping

    class Config:
        env_prefix = "SMBU_"
        case_sensitive = False


settings = Settings()
