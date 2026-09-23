"""Web Push: уведомление на телефон, когда вкладка Джарвиса свёрнута.

Когда шлём (решает вызывающий, см. main.py и speaker.py):
    фоновая задача готова / упала, а экран её не видит  → пуш с ответом
    таймер/напоминание, а экран свёрнут                → пуш (нет подписки — Telegram)

    ядро ──(VAPID-подпись, шифрование aes128gcm)──► push-сервис браузера
          (web.push.apple.com, fcm.googleapis.com…) ──► sw.js ──► уведомление

Протоколы — RFC 8291 (шифрование) и RFC 8292 (VAPID) на `cryptography` и
PyJWT, которые уже стоят в venv: обёртка pywebpush не нужна.
На iPhone пуши работают только у PWA с экрана «Домой» (iOS 16.4+).

Ключи VAPID создаются один раз — data/vapid.pem; сменишь — все подписки
придётся оформить заново. Подписки по устройствам — data/push.json.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings

logger = logging.getLogger("jarvis.webpush")

VAPID_FILE = Path("data/vapid.pem")
SUBSCRIPTIONS_FILE = Path("data/push.json")
# Столько push-сервис держит уведомление, если телефон вне сети.
TTL_SECONDS = 6 * 3600
BODY_MAX = 300

_key: ec.EllipticCurvePrivateKey | None = None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _point(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _vapid_key() -> ec.EllipticCurvePrivateKey:
    global _key
    if _key is None:
        if VAPID_FILE.exists():
            loaded = serialization.load_pem_private_key(VAPID_FILE.read_bytes(), password=None)
            if not isinstance(loaded, ec.EllipticCurvePrivateKey):
                raise ValueError(f"{VAPID_FILE}: не ключ P-256")
            _key = loaded
        else:
            _key = ec.generate_private_key(ec.SECP256R1())
            VAPID_FILE.parent.mkdir(parents=True, exist_ok=True)
            pem = _key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
            )
            fd = os.open(VAPID_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(pem)
            logger.info("Созданы ключи VAPID: %s", VAPID_FILE)
    return _key


def public_key() -> str:
    """applicationServerKey для pushManager.subscribe в браузере."""
    return _b64(_point(_vapid_key().public_key()))


# --- подписки ----------------------------------------------------------------

def _load() -> dict[str, list[dict[str, Any]]]:
    try:
        return json.loads(SUBSCRIPTIONS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _save(data: dict[str, list[dict[str, Any]]]) -> None:
    SUBSCRIPTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUBSCRIPTIONS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(SUBSCRIPTIONS_FILE)


def subscribe(device: str, subscription: dict[str, Any]) -> bool:
    """True — подписка новая (браузер подписывает заново при каждом открытии)."""
    data = _load()
    subs = [s for s in data.get(device, []) if s["endpoint"] != subscription["endpoint"]]
    new = len(subs) == len(data.get(device, []))
    data[device] = [*subs, subscription]
    _save(data)
    return new


def unsubscribe(device: str, endpoint: str) -> None:
    data = _load()
    data[device] = [s for s in data.get(device, []) if s["endpoint"] != endpoint]
    _save(data)


# --- отправка ----------------------------------------------------------------

def _encrypt(payload: bytes, p256dh: str, auth: str) -> bytes:
    """Content-Encoding: aes128gcm (RFC 8291, одна запись)."""
    ua_public = _unb64(p256dh)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    as_private = ec.generate_private_key(ec.SECP256R1())
    as_public = _point(as_private.public_key())
    shared = as_private.exchange(ec.ECDH(), ua_key)

    ikm = HKDF(hashes.SHA256(), 32, _unb64(auth), b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    salt = os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    # \x02 — разделитель последней записи.
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + (4096).to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + ciphertext


def _authorization(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int(time.time()) + 12 * 3600,
        # Apple без sub (mailto: или https:) отклоняет запрос.
        "sub": settings.push_contact,
    }
    token = jwt.encode(claims, _vapid_key(), algorithm="ES256")
    return f"vapid t={token}, k={public_key()}"


async def send(device: str, title: str, body: str, tag: str | None = None) -> int:
    """Уведомление на все подписки устройства; вернёт, скольким ушло.
    Мёртвые подписки (404/410 — удалили приложение, сбросили разрешение) удаляются."""
    subs = _load().get(device, [])
    if not subs:
        return 0
    if len(body) > BODY_MAX:
        body = body[: BODY_MAX - 1].rstrip() + "…"
    payload = json.dumps({"title": title, "body": body, "tag": tag}, ensure_ascii=False).encode()
    sent = 0
    async with httpx.AsyncClient(timeout=15.0) as client:
        for sub in subs:
            endpoint = sub["endpoint"]
            try:
                resp = await client.post(
                    endpoint,
                    content=_encrypt(payload, sub["keys"]["p256dh"], sub["keys"]["auth"]),
                    headers={
                        "Authorization": _authorization(endpoint),
                        "Content-Encoding": "aes128gcm",
                        "Content-Type": "application/octet-stream",
                        "TTL": str(TTL_SECONDS),
                        "Urgency": "high",
                    },
                )
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                logger.warning("Пуш на %s не ушёл: %s", urlsplit(endpoint).netloc, exc)
                continue
            if resp.status_code in (404, 410):
                logger.info("Подписка %s больше не действует — удаляю", urlsplit(endpoint).netloc)
                unsubscribe(device, endpoint)
            elif resp.status_code >= 400:
                logger.warning("Пуш на %s: %s %s", urlsplit(endpoint).netloc, resp.status_code, resp.text[:200])
            else:
                # Успех тоже в журнал: иначе «пуш не пришёл» нечем проверить.
                logger.info("Пуш ушёл на %s: %s", urlsplit(endpoint).netloc, resp.status_code)
                sent += 1
    return sent


async def notify(device: str, title: str, body: str, tag: str | None = None) -> int:
    """send без исключений — пуш не должен ронять ход или таймер. Вернёт,
    скольким подпискам ушло (0 — не ушло никуда)."""
    try:
        return await send(device, title, body, tag)
    except Exception:
        logger.exception("Пуш на %s упал", device)
        return 0
