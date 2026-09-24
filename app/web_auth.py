"""Вход в веб-интерфейс снаружи: своя страница логина и cookie на год.

Раньше пароль спрашивал Caddy (basic auth). Приложение «На экран Домой» на
iPhone окно basic auth не показывает — PWA открывалась чёрным экраном, а
Safari регулярно забывал пароль. Теперь:

    запрос через Caddy (есть X-Forwarded-For)
      ├ публичное: /login, манифест, иконки, sw.js     → как есть
      ├ cookie jarvis_auth подписана и не истекла      → как есть (+ продление)
      ├ страница (GET, text/html)                      → 303 на /login
      └ API                                             → 401 (фронт уйдёт на /login)

Локальные запросы (Hermes → /mcp, curl на asus) идут без X-Forwarded-For и
не проверяются — как в _LocalOnly. Пароль — тот же bcrypt-хэш, что был в
Caddy (WEB_AUTH_HASH). Пуст — вход выключен (разработка).
"""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, quote

import bcrypt
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings

logger = logging.getLogger("jarvis.auth")

COOKIE = "jarvis_auth"
SECRET_FILE = Path("data/web_secret")
DAY = 86400
# Продлеваем cookie, когда до конца осталось меньше этого: заходишь хоть
# раз в полгода — не разлогинит никогда.
RENEW_BELOW = 300 * DAY
PUBLIC = {
    "/login", "/manifest.webmanifest", "/apple-touch-icon.png", "/icon.svg",
    "/icon-192.png", "/icon-512.png", "/sw.js",
}
# Перебор пароля: столько ошибок с одного адреса за окно — дальше 429.
MAX_FAILS = 8
FAIL_WINDOW = 15 * 60

_fails: dict[str, list[float]] = {}
_secret: bytes | None = None


def _key() -> bytes:
    global _secret
    if _secret is None:
        if not SECRET_FILE.exists():
            SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
            SECRET_FILE.write_text(secrets.token_hex(32))
            SECRET_FILE.chmod(0o600)
        _secret = SECRET_FILE.read_text().strip().encode()
    return _secret


def _sign(expires: int) -> str:
    mac = hmac.new(_key(), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{mac}"


def issue() -> tuple[str, int]:
    max_age = settings.web_session_days * DAY
    return _sign(int(time.time()) + max_age), max_age


def valid(token: str | None, now: float | None = None) -> int | None:
    """Срок действия токена или None, если он чужой/протух."""
    if not token or "." not in token:
        return None
    expires = token.partition(".")[0]
    if not expires.isdigit() or not hmac.compare_digest(token, _sign(int(expires))):
        return None
    return int(expires) if int(expires) > (now or time.time()) else None


def check_password(user: str, password: str) -> bool:
    if not settings.web_auth_hash:
        return False
    ok_user = hmac.compare_digest(user.strip().lower(), settings.web_auth_user.lower())
    try:
        ok_pass = bcrypt.checkpw(password.encode(), settings.web_auth_hash.encode())
    except ValueError:
        logger.error("WEB_AUTH_HASH не похож на bcrypt")
        return False
    return ok_user and ok_pass


def _cookie_header(token: str, max_age: int) -> str:
    return f"{COOKIE}={token}; Max-Age={max_age}; Path=/; HttpOnly; Secure; SameSite=Lax"


def _cookies(scope: Scope) -> dict[str, str]:
    raw = next((v.decode() for k, v in scope["headers"] if k == b"cookie"), "")
    pairs = (part.strip().split("=", 1) for part in raw.split(";") if "=" in part)
    return {k: v for k, v in pairs}


def _client_ip(scope: Scope) -> str:
    forwarded = next((v.decode() for k, v in scope["headers"] if k == b"x-forwarded-for"), "")
    return forwarded.split(",")[0].strip() or "?"


def _safe_next(target: str) -> str:
    # Только свои относительные пути — не открытый редирект.
    return target if target.startswith("/") and not target.startswith("//") else "/"


class WebAuth:
    """Чистый ASGI, как _LocalOnly в main.py: не буферизует SSE и аудио."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        external = scope["type"] == "http" and any(k == b"x-forwarded-for" for k, _ in scope["headers"])
        if not external or not settings.web_auth_hash:
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path == "/login":
            await self._login(scope, receive, send)
            return
        if path in PUBLIC:
            await self.app(scope, receive, send)
            return

        expires = valid(_cookies(scope).get(COOKIE))
        if expires is None:
            await self._deny(scope, receive, send)
            return
        if expires - time.time() < RENEW_BELOW:
            token, max_age = issue()
            await self.app(scope, receive, self._with_cookie(send, token, max_age))
            return
        await self.app(scope, receive, send)

    @staticmethod
    def _with_cookie(send: Send, token: str, max_age: int) -> Send:
        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"set-cookie", _cookie_header(token, max_age).encode()))
                message = {**message, "headers": headers}
            await send(message)

        return wrapped

    async def _deny(self, scope: Scope, receive: Receive, send: Send) -> None:
        accept = next((v.decode() for k, v in scope["headers"] if k == b"accept"), "")
        response: Response
        if scope["method"] == "GET" and "text/html" in accept:
            query = scope.get("query_string", b"").decode()
            target = scope["path"] + (f"?{query}" if query else "")
            response = RedirectResponse(f"/login?next={quote(target)}", status_code=303)
        else:
            response = JSONResponse({"detail": "нужен вход"}, status_code=401)
        await response(scope, receive, send)

    async def _login(self, scope: Scope, receive: Receive, send: Send) -> None:
        query = parse_qs(scope.get("query_string", b"").decode())
        target = _safe_next((query.get("next") or ["/"])[0])
        if scope["method"] != "POST":
            await HTMLResponse(login_page(target))(scope, receive, send)
            return

        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
        ip = _client_ip(scope)
        now = time.time()
        recent = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
        if len(recent) >= MAX_FAILS:
            page = login_page(target, "Слишком много попыток — подожди пятнадцать минут.")
            await HTMLResponse(page, status_code=429)(scope, receive, send)
            return
        if not check_password(form.get("username", ""), form.get("password", "")):
            _fails[ip] = recent + [now]
            logger.warning("Неверный вход с %s", ip)
            await HTMLResponse(login_page(target, "Неверный логин или пароль."), status_code=401)(scope, receive, send)
            return
        _fails.pop(ip, None)
        token, max_age = issue()
        response = RedirectResponse(_safe_next(form.get("next", target)), status_code=303)
        response.headers.append("set-cookie", _cookie_header(token, max_age))
        await response(scope, receive, send)


def login_page(target: str, error: str | None = None) -> str:
    err = f'<p class="err" role="alert">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b0f14">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Джарвис">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="manifest" href="manifest.webmanifest">
<title>Джарвис — вход</title>
<style>
  :root {{ --bg:#0b0f14; --surface:#161c24; --surface-2:#232b36; --border:#2a3440; --text:#e7ecf2; --dim:#8a96a3; --accent:#4fd1c5; --danger:#ff6369; color-scheme: dark; }}
  * {{ box-sizing: border-box }}
  html, body {{ margin:0; height:100%; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif }}
  body {{ display:grid; place-items:center; padding:24px 16px calc(24px + env(safe-area-inset-bottom)); overflow:hidden }}
  .glow {{ position:fixed; width:520px; height:520px; border-radius:50%; background:radial-gradient(circle, rgb(79 209 197 / 18%), transparent 65%); top:-180px; left:50%; transform:translateX(-50%); pointer-events:none }}
  form {{ position:relative; width:100%; max-width:360px; display:flex; flex-direction:column; gap:12px }}
  .logo {{ width:72px; height:72px; margin:0 auto 8px; border-radius:20px; background:var(--surface); display:grid; place-items:center; box-shadow:0 12px 40px rgb(0 0 0 / 40%) }}
  .logo span {{ width:22px; height:22px; border-radius:50%; background:var(--accent); box-shadow:0 0 24px var(--accent); animation:pulse 2.4s ease-in-out infinite }}
  h1 {{ margin:0; text-align:center; font-size:26px; letter-spacing:.01em }}
  .sub {{ margin:0 0 12px; text-align:center; color:var(--dim); font-size:14px }}
  input {{ width:100%; padding:14px 16px; border-radius:14px; border:1px solid var(--border); background:var(--surface); color:var(--text); font:inherit; font-size:16px }}
  input:focus {{ outline:2px solid var(--accent); outline-offset:-1px }}
  button {{ margin-top:4px; padding:14px; border:none; border-radius:14px; background:var(--accent); color:var(--bg); font:inherit; font-size:16px; font-weight:600; cursor:pointer }}
  button:active {{ opacity:.85 }}
  .err {{ margin:0; padding:10px 14px; border-radius:12px; background:rgb(255 99 105 / 12%); color:var(--danger); font-size:14px; text-align:center }}
  .note {{ margin:8px 0 0; text-align:center; color:var(--dim); font-size:12px }}
  @keyframes pulse {{ 50% {{ opacity:.45 }} }}
</style>
</head>
<body>
<div class="glow"></div>
<form method="post" action="login?next={quote(target)}">
  <div class="logo"><span></span></div>
  <h1>Джарвис</h1>
  <p class="sub">Войди один раз — на этом устройстве запомню на год</p>
  {err}
  <input name="username" autocomplete="username" autocapitalize="none" autocorrect="off" placeholder="Логин" required>
  <input name="password" type="password" autocomplete="current-password" placeholder="Пароль" required>
  <input type="hidden" name="next" value="{html.escape(target)}">
  <button type="submit">Войти</button>
  <p class="note">Тот же логин и пароль, что раньше спрашивал браузер</p>
</form>
</body>
</html>"""
