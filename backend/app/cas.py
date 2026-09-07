"""
CAS client for authserver.smbu.edu.cn.

The campus CAS posts the password AES-encrypted with a per-page salt. The
scheme is reproduced here exactly as the browser does it, otherwise the
server silently rejects the credential:

    plaintext = randomString(64) + password
    key       = utf8(pwdEncryptSalt)      # 16 bytes -> AES-128
    iv        = utf8(randomString(16))
    cipher    = AES-128-CBC, PKCS7 padding
    payload   = base64(ciphertext)
"""

from __future__ import annotations

import asyncio
import base64
import random
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .config import settings

AES_CHARS = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _random_string(n: int) -> str:
    return "".join(AES_CHARS[random.randrange(len(AES_CHARS))] for _ in range(n))


def encrypt_password(password: str, salt: str) -> str:
    """Mirror `encryptAES()` from the campus authserver front-end."""
    if not salt:
        return password
    key = salt.encode("utf-8")
    iv = _random_string(16).encode("utf-8")
    plaintext = (_random_string(64) + password).encode("utf-8")

    pad_len = 16 - (len(plaintext) % 16)
    plaintext += bytes([pad_len]) * pad_len

    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii")


_RE_SALT = re.compile(r'id="pwdEncryptSalt"\s+value="([^"]*)"')
_RE_EXECUTION = re.compile(r'id="execution"\s+name="execution"\s+value="([^"]*)"')
_RE_LT = re.compile(r'name="lt"\s+id="lt"\s+value="([^"]*)"')
_RE_ERROR = re.compile(
    r'id="(?:showErrorTip|showWarnTip)"[^>]*>(.*?)</span>', re.S
)
_RE_CAPTCHA_IMG = re.compile(r'<img[^>]*id="[^"]*[Cc]aptcha[^"]*"[^>]*src="([^"]+)"')
_RE_TAG = re.compile(r"<[^>]+>")


@dataclass
class LoginOutcome:
    ok: bool
    message: Optional[str] = None
    needs_captcha: bool = False
    captcha_url: Optional[str] = None
    ticket_url: Optional[str] = None
    # Only ever kept in memory, keyed by a random opaque session handle.
    cookies: Dict[str, str] = field(default_factory=dict)


class CasError(Exception):
    """Upstream/transport level failure (retryable)."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def _strip_tags(html: str) -> str:
    return _RE_TAG.sub("", html).strip()


def _extract_error(html: str) -> Optional[str]:
    for raw in _RE_ERROR.findall(html):
        text = _strip_tags(raw)
        if text:
            return text
    return None


def _extract_captcha(html: str) -> Optional[str]:
    m = _RE_CAPTCHA_IMG.search(html)
    if not m:
        return None
    src = m.group(1)
    return urljoin(settings.cas_base_url, src)


def extract_login_fields(html: str) -> Tuple[Dict[str, str], Optional[str]]:
    """
    Pull the login-form fields (lt / execution / salt) and the captcha image
    URL out of a CAS login page.

    Public so the WebVPN chain — a different CAS *entry point* but the exact
    same authserver form — reuses this parsing instead of copying the regexes.
    """
    salt_m = _RE_SALT.search(html)
    lt_m = _RE_LT.search(html)
    exec_m = _RE_EXECUTION.search(html)
    fields = {
        "salt": salt_m.group(1) if salt_m else "",
        "lt": lt_m.group(1) if lt_m else "",
        "execution": exec_m.group(1) if exec_m else "e1s1",
    }
    return fields, _extract_captcha(html)


class CasClient:
    """Authenticates against CAS and returns the resulting cookie jar."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def fetch_login_form(self) -> Tuple[str, Dict[str, str], Optional[str]]:
        """Return (form_html, hidden_fields, captcha_url).

        The campus regenerates ``pwdEncryptSalt`` on every page load and,
        on a cold first visit, occasionally serves a shell page whose salt
        field is still empty. We re-fetch up to a couple of times so the
        caller always gets a usable salt for password encryption.
        """
        params = {"service": settings.jw_entry_url}
        url = f"{settings.cas_base_url}{settings.cas_login_path}"

        html = ""
        fields: Dict[str, str] = {"lt": "", "execution": "e1s1", "salt": ""}
        for attempt in range(3):
            try:
                resp = await self._client.get(
                    url, params=params, headers={"User-Agent": USER_AGENT}
                )
            except httpx.HTTPError as exc:  # transport failure
                raise CasError(f"CAS unreachable: {exc}") from exc

            if resp.status_code != 200:
                raise CasError(f"CAS login page returned HTTP {resp.status_code}")

            html = resp.text
            fields, _ = extract_login_fields(html)
            if fields["salt"]:
                break
            # Empty salt on a cold load: brief pause and retry.
            await asyncio.sleep(0.3)

        return html, fields, _extract_captcha(html)

    async def login(
        self, username: str, password: str, captcha: str = ""
    ) -> LoginOutcome:
        _, fields, _ = await self.fetch_login_form()

        salt = fields.get("salt") or ""
        # The campus login form keeps the encrypted value in a hidden input
        # (id="saltPassword") whose *name* attribute is "password". The browser
        # therefore posts it as `password=...`; submit under that name too.
        payload = {
            "username": username,
            "password": encrypt_password(password, salt),
            "captcha": captcha,
            "_eventId": "submit",
            "cllt": "userNameLogin",
            "dllt": "generalLogin",
            "lt": fields.get("lt", ""),
            "execution": fields.get("execution", "e1s1"),
        }
        if not captcha:
            payload.pop("captcha", None)

        url = f"{settings.cas_base_url}{settings.cas_login_path}"
        try:
            resp = await self._client.post(
                url,
                params={"service": settings.jw_entry_url},
                data=payload,
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": url,
                    "Origin": settings.cas_base_url,
                },
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise CasError(f"CAS login failed: {exc}") from exc

        final_url = str(resp.url)
        body = resp.text

        # A successful CAS round-trip always ends on the service URL carrying
        # a ticket. Anything still on /authserver/login is a rejection.
        if "authserver" in final_url:
            message = _extract_error(body)
            captcha_url = _extract_captcha(body)
            return LoginOutcome(
                ok=False,
                message=message or "CAS rejected the credentials",
                needs_captcha=bool(captcha_url) or "验证码" in (message or ""),
                captcha_url=captcha_url,
            )

        if resp.status_code >= 400:
            raise CasError(f"Service returned HTTP {resp.status_code}")

        # Collect the session cookies. The campus sometimes emits a bare
        # `Set-Cookie: HttpOnly` header that http.cookies materialises as a
        # cookie *named* "HttpOnly"; iterating the jar directly (instead of
        # httpx's items(), which raises KeyError on such artefacts) and
        # dropping attribute-only names keeps both the returned dict and the
        # outbound Cookie header clean.
        _ATTR_NAMES = {"httponly", "secure", "samesite", "path", "domain", "expires", "max-age"}
        jar = self._client.cookies.jar
        cookies: Dict[str, str] = {}
        for morsel in list(jar):
            name = (morsel.name or "").strip()
            if not name or name.lower() in _ATTR_NAMES:
                if name:
                    jar.clear(morsel.domain, morsel.path, name)
                continue
            cookies[name] = morsel.value or ""

        return LoginOutcome(
            ok=True,
            ticket_url=final_url,
            cookies=cookies,
        )
