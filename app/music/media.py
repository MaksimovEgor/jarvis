"""Звук для плееров, которые сами до источника не достанут.

    /media/yt/<id>     — YouTube: из кэша (если трек уже скачан) или потоком
                         через туннель с пробросом Range; mpv так играет
                         многочасовые книги, не скачивая их целиком.
    /media/hls/<id>/…  — то же длинное для Safari: звуковой HLS YouTube с
                         переписанными на ядро ссылками сегментов (DASH-m4a
                         iPhone не играет, HLS — родной формат).
    /media/ref/<token> — http-радио (страница по https его не откроет), файлы
                         домашней библиотеки, озвучки объявлений. Токен вместо
                         URL/пути в адресе — чтобы эндпоинт не был открытым
                         прокси и не читал произвольные файлы.

Снаружи доступно через Caddy (basic auth) — браузер телефона ходит сюда.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import AsyncIterator

import aiohttp
from aiohttp_socks import ProxyConnector
from yarl import URL
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.config import settings
from app.music import storage, youtube
from app.music.models import Track

logger = logging.getLogger("jarvis.media")

router = APIRouter(prefix="/media")

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PASS_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")
_CHUNK = 64 * 1024

_refs: dict[str, str] = {}


def register(ref: str) -> str:
    token = hashlib.sha1(ref.encode()).hexdigest()[:16]
    _refs[token] = ref
    return token


async def _relay(url: str, headers: dict[str, str], proxy: str | None) -> tuple[int, dict[str, str], AsyncIterator[bytes]]:
    # Ссылки googlevideo подписаны и привязаны к IP (v4/v6) — иначе 403:
    # rdns — имя резолвит VPS, encoded=True — yarl не перекодирует URL.
    connector = ProxyConnector.from_url(proxy, rdns=True) if proxy else None
    session = aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(sock_connect=10, sock_read=30))
    try:
        resp = await session.get(URL(url, encoded=True), headers=headers)
    except Exception:
        await session.close()
        raise

    async def body() -> AsyncIterator[bytes]:
        # finally сработает и когда браузер оборвал загрузку (перемотка) —
        # иначе соединения к googlevideo копились бы.
        try:
            async for chunk in resp.content.iter_chunked(_CHUNK):
                yield chunk
        finally:
            resp.release()
            await session.close()

    passed = {h: resp.headers[h] for h in _PASS_HEADERS if h in resp.headers}
    if resp.status >= 400:
        resp.release()
        await session.close()
    return resp.status, passed, body()


@router.get("/yt/{video_id}")
async def youtube_audio(video_id: str, request: Request) -> Response:
    if not _VIDEO_ID.match(video_id):
        raise HTTPException(404)
    if path := youtube.cached(Track(title="", source="youtube", ref=video_id)):
        return FileResponse(path, media_type="audio/mp4" if path.suffix == ".m4a" else None)

    headers = {"Range": request.headers["range"]} if "range" in request.headers else {}
    for refresh in (False, True):
        url = await youtube.stream_url(video_id, refresh=refresh)
        status, passed, body = await _relay(url, headers, settings.youtube_proxy or None)
        if status < 400:
            return StreamingResponse(body, status_code=status, headers=passed)
        # 403 — ссылка протухла или привязана к другому IP: берём свежую.
        logger.warning("googlevideo %s ответил %s", video_id, status)
    raise HTTPException(502, "YouTube не отдал звук")


@router.get("/hls/{video_id}/index.m3u8")
async def youtube_hls(video_id: str) -> Response:
    if not _VIDEO_ID.match(video_id):
        raise HTTPException(404)
    segments = await youtube.hls_segments(video_id)
    target = max(float(extinf.split(":")[1].rstrip(",")) for extinf, _ in segments)
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-PLAYLIST-TYPE:VOD", f"#EXT-X-TARGETDURATION:{int(target) + 1}"]
    for n, (extinf, _) in enumerate(segments):
        lines += [extinf, f"seg/{n}"]  # относительно плейлиста → /media/hls/<id>/seg/<n>
    lines.append("#EXT-X-ENDLIST")
    return Response("\n".join(lines) + "\n", media_type="application/vnd.apple.mpegurl")


@router.get("/hls/{video_id}/seg/{n}")
async def youtube_hls_segment(video_id: str, n: int) -> Response:
    if not _VIDEO_ID.match(video_id):
        raise HTTPException(404)
    for refresh in (False, True):
        segments = await youtube.hls_segments(video_id, refresh=refresh)
        if not 0 <= n < len(segments):
            raise HTTPException(404)
        status, passed, body = await _relay(segments[n][1], {}, settings.youtube_proxy or None)
        if status < 400:
            passed["Content-Type"] = "audio/aac"
            return StreamingResponse(body, status_code=status, headers=passed)
        logger.warning("HLS-сегмент %s/%s: googlevideo ответил %s", video_id, n, status)
    raise HTTPException(502, "YouTube не отдал сегмент")


@router.get("/cover/{video_id}")
async def cover(video_id: str) -> Response:
    """Обложка трека (превью YouTube) — через ядро и с кэшем на диске: так
    она с того же origin (цвет фона плеера берётся через canvas) и не зависит от
    доступности i.ytimg.com с телефона. Для песен YouTube Music это обложка альбома."""
    if not _VIDEO_ID.match(video_id):
        raise HTTPException(404)
    path = storage.cover_dir() / f"{video_id}.jpg"
    if not path.exists():
        try:
            data = await youtube.fetch_bytes(f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")
        except Exception as exc:
            logger.warning("Нет обложки %s: %s", video_id, exc)
            raise HTTPException(404)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})


@router.get("/ref/{token}")
async def by_ref(token: str) -> Response:
    ref = _refs.get(token)
    if ref is None:
        raise HTTPException(404)
    if not ref.startswith(("http://", "https://")):
        path = Path(ref)
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)
    status, passed, body = await _relay(ref, {}, None)
    if status >= 400:
        raise HTTPException(502, f"источник ответил {status}")
    # У живого потока нет длины — пусть браузер не ждёт Content-Length.
    passed.pop("Content-Length", None)
    return StreamingResponse(body, status_code=status, headers=passed)
