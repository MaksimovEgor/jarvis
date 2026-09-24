"""Поиск по каталогам и запуск найденного — экран «Поиск».

    вкладка      откуда
    yandex       Яндекс: лучший результат, треки, исполнители, альбомы, плейлисты
    youtube      YouTube Music + обычный YouTube (видео, лайвы, сеты)
    soundcloud   SoundCloud (yt-dlp)
    books        аудиокниги Яндекса (album.type=audiobook), запасной — YouTube
    podcasts     подкасты Яндекса (album.type=podcast), запасной — YouTube
    kids         детские станции Яндекса + детское из поиска (по жанрам альбома)

Яндекс уже отвечал 429 на серию запросов — ответы кэшируются на 10 минут.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

from app.music import positions, yandex, youtube
from app.music.library import library
from app.music.models import Entity, SearchTab, Track, split_title

logger = logging.getLogger("jarvis.catalog")

CACHE_SECONDS = 600
KIDS_GENRES = {"childrensliterature", "children", "fairytales", "kids", "forchildren", "childrensmusic", "lullaby"}
KIDS_STATIONS = ("editorial:station-1", "editorial:station-4", "editorial:station-5",
                 "editorial:station-17", "editorial:station-13")
SectionKind = Literal["best", "tracks", "artists", "albums", "playlists", "books", "podcasts", "stations", "videos"]

_cache: dict[tuple[str, str], tuple[float, list["Section"]]] = {}


@dataclass(frozen=True)
class Section:
    kind: SectionKind
    title: str
    items: list[Entity]


def _cover(uri: str | None, size: str = "200x200") -> str | None:
    return f"https://{uri.replace('%%', size)}" if uri else None


def _clock(seconds: float | None) -> str:
    if not seconds:
        return ""
    s = int(seconds)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def track_entity(track: Track) -> Entity:
    song, artist = split_title(track.title, track.artist)
    if track.cover or track.source == "youtube":
        library.upsert(track)  # обложка /media/cover/<ref> — из библиотеки или превью YouTube
    label = {"ytmusic": "YT Music", "youtube": "YouTube", "soundcloud": "SoundCloud", "yandex": ""}[track.service]
    subtitle = " · ".join(x for x in (artist, _clock(track.duration), label) if x)
    return Entity(source="yandex" if track.source == "yandex" else "soundcloud" if track.source == "soundcloud" else "youtube",
                  type="track", id=track.ref, title=song, subtitle=subtitle, cover=f"media/cover/{track.ref}",
                  cover_crop=track.source == "youtube", track=track)


def _album_entity(album: Any, kind: Literal["album", "audiobook", "podcast"]) -> Entity:
    artists = ", ".join(a.name for a in album.artists or [] if a.name)
    what = {"album": str(album.year or ""), "audiobook": "Аудиокнига", "podcast": "Подкаст"}[kind]
    count = f"{album.track_count} {'глав' if kind == 'audiobook' else 'выпусков' if kind == 'podcast' else 'треков'}" \
        if album.track_count and kind != "album" else ""
    return Entity(source="yandex", type=kind, id=str(album.id), title=album.title or "?",
                  subtitle=" · ".join(x for x in (artists, what, count) if x), cover=_cover(album.cover_uri))


def _artist_entity(artist: Any) -> Entity:
    return Entity(source="yandex", type="artist", id=str(artist.id), title=artist.name or "?",
                  subtitle="Исполнитель", cover=_cover(artist.cover.uri if artist.cover else None))


def _playlist_entity(playlist: Any) -> Entity:
    uri = playlist.cover.uri if playlist.cover and playlist.cover.uri else \
        (playlist.cover.items_uri[0] if playlist.cover and playlist.cover.items_uri else None)
    count = f"{playlist.track_count} треков" if playlist.track_count else ""
    return Entity(source="yandex", type="playlist", id=f"{playlist.owner.uid}:{playlist.kind}",
                  title=playlist.title or "?", subtitle=" · ".join(x for x in ("Плейлист", count) if x),
                  cover=_cover(uri))


def _tracks(results: Any, limit: int) -> list[Entity]:
    tracks = [yandex.to_track(x) for x in (results.results if results else [])[:limit * 2]]
    return [track_entity(t) for t in tracks if t is not None][:limit]


def _is_kids(album: Any) -> bool:
    return (album.genre or "") in KIDS_GENRES


async def _yandex_tab(query: str) -> list[Section]:
    r = await yandex.search_all(query)
    sections: list[Section] = []
    best = r.best.result if r.best else None
    if best is not None:
        entity = {"artist": _artist_entity, "playlist": _playlist_entity}.get(r.best.type)
        if entity is not None:
            sections.append(Section("best", "Лучший результат", [entity(best)]))
        elif r.best.type == "album":
            sections.append(Section("best", "Лучший результат", [_album_entity(best, "album")]))
        elif r.best.type == "track" and (t := yandex.to_track(best)) is not None:
            sections.append(Section("best", "Лучший результат", [track_entity(t)]))
    if tracks := _tracks(r.tracks, 10):
        sections.append(Section("tracks", "Треки", tracks))
    if r.artists and r.artists.results:
        sections.append(Section("artists", "Исполнители", [_artist_entity(a) for a in r.artists.results[:8]]))
    if r.albums and r.albums.results:
        sections.append(Section("albums", "Альбомы", [_album_entity(a, "album") for a in r.albums.results[:10]]))
    if r.playlists and r.playlists.results:
        sections.append(Section("playlists", "Плейлисты", [_playlist_entity(p) for p in r.playlists.results[:6]]))
    return sections


async def _youtube_tab(query: str) -> list[Section]:
    music, videos = await asyncio.gather(youtube.music_search(query, limit=8), youtube.search(query), return_exceptions=True)
    sections = []
    if isinstance(music, list) and music:
        sections.append(Section("tracks", "YouTube Music", [track_entity(t) for t in music]))
    if isinstance(videos, list) and videos:
        known = {t.ref for t in music} if isinstance(music, list) else set()
        sections.append(Section("videos", "Видео", [track_entity(t) for t in videos if t.ref not in known]))
    return sections


async def _soundcloud_tab(query: str) -> list[Section]:
    found = await youtube.soundcloud_search(query, limit=8)
    return [Section("tracks", "SoundCloud", [track_entity(t) for t in found])] if found else []


async def _long_tab(query: str, kind: Literal["audiobook", "podcast"]) -> list[Section]:
    sections = []
    if yandex.is_on():
        r = await yandex.search_all(query, type_="podcast")
        albums = [a for a in (r.podcasts.results if r and r.podcasts else []) if a.type == kind]
        if albums:
            title = "Аудиокниги Яндекса" if kind == "audiobook" else "Подкасты Яндекса"
            sections.append(Section("books" if kind == "audiobook" else "podcasts", title,
                                    [_album_entity(a, kind) for a in albums[:12]]))
    found = await youtube.search(query, kind)
    if found:
        entities = [Entity(source="youtube", type=kind, id=t.ref, title=t.title,
                           subtitle=" · ".join(x for x in (_clock(t.duration), "YouTube") if x),
                           cover=f"media/cover/{t.ref}", cover_crop=True, track=t) for t in found]
        sections.append(Section("videos", "YouTube", entities))
    return sections


async def _kids_tab(query: str) -> list[Section]:
    names = await yandex.stations()
    stations = [Entity(source="yandex", type="station", id=sid, title=names.get(sid, sid), subtitle="Станция")
                for sid in KIDS_STATIONS if sid in names]
    sections = [Section("stations", "Станции для детей", stations)] if stations else []
    if not query or not yandex.is_on():
        return sections
    general, long = await asyncio.gather(yandex.search_all(query), yandex.search_all(query, type_="podcast"))
    books = [a for a in (long.podcasts.results if long and long.podcasts else []) if _is_kids(a)]
    if books:
        sections.append(Section("books", "Детские книги", [_album_entity(a, "audiobook") for a in books[:10]]))
    albums = [a for a in (general.albums.results if general and general.albums else []) if _is_kids(a)]
    if albums:
        sections.append(Section("albums", "Детская музыка", [_album_entity(a, "album") for a in albums[:10]]))
    return sections


async def search(query: str, tab: SearchTab) -> list[Section]:
    query = " ".join(query.split())
    key = (tab, query.casefold())
    if (hit := _cache.get(key)) and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    if not query and tab != "kids":
        return []
    if tab in ("yandex",) and not yandex.is_on():
        return []
    handlers = {
        "yandex": _yandex_tab, "youtube": _youtube_tab, "soundcloud": _soundcloud_tab,
        "books": lambda q: _long_tab(q, "audiobook"), "podcasts": lambda q: _long_tab(q, "podcast"),
        "kids": _kids_tab,
    }
    sections = await handlers[tab](query)
    _cache[key] = (time.time(), sections)
    return sections


async def resolve_entity(entity: Entity) -> tuple[list[Track], int]:
    """Треки для очереди и с какого начинать."""
    if entity.track is not None:
        return [entity.track], 0
    if entity.type == "track" or entity.source != "yandex":
        # Трек любого источника и видео YouTube (книга/подкаст) — уже в библиотеке (track_entity).
        info = library.track(entity.id)
        return ([info.track("query")], 0) if info else ([], 0)
    if entity.type == "artist":
        return await yandex.artist_tracks(entity.id), 0
    if entity.type == "playlist":
        owner, _, kind = entity.id.partition(":")
        return await yandex.playlist_tracks(owner, kind), 0
    _, tracks = await yandex.album_tracks(entity.id)
    if entity.type == "audiobook":
        return tracks, positions.resume_index(tracks) if tracks else 0
    return tracks, 0  # альбом по порядку; подкаст — как отдаёт Яндекс (новые первыми)
