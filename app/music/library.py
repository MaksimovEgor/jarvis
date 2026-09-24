"""«Моя музыка»: оценки, журнал прослушиваний, заметки о вкусе, зёрна волны.

SQLite data/music/library.db, пишет только ядро. Синхронный sqlite3: запросы
на тысячи строк — доли миллисекунды, event loop не заметит.

    tracks   — что вообще звучало (исполнитель, энергия и теги от dj)
    ratings  — лайк +1 / дизлайк -1
    plays    — каждое прослушивание: слот суток, откуда трек, чем кончилось
    notes    — «я не люблю рэп»: для профиля dj (app/music/taste.py)
    seeds    — поисковые запросы от dj под слот и настроение

Исполнитель сравнивается без регистра («КИНО» = «Кино»).
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.config import settings
from app.music.models import FromWhere, Mood, Outcome, Rating, Slot, Track, clean_title, is_weekend, slot_at

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    ref TEXT PRIMARY KEY, title TEXT NOT NULL, artist TEXT, duration REAL,
    energy INTEGER, tags TEXT, added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ratings (ref TEXT PRIMARY KEY, value INTEGER NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY, ref TEXT NOT NULL, device TEXT, started_at REAL NOT NULL,
    slot TEXT NOT NULL, weekend INTEGER NOT NULL, origin TEXT NOT NULL, mood TEXT,
    outcome TEXT, listened_s REAL, from_where TEXT, start_ms INTEGER
);
CREATE INDEX IF NOT EXISTS plays_started ON plays(started_at);
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, text TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS seeds (
    id INTEGER PRIMARY KEY, query TEXT NOT NULL, bucket TEXT NOT NULL, slot TEXT NOT NULL,
    mood TEXT NOT NULL, reason TEXT, at REAL NOT NULL
);
"""

DAY = 86400.0
SeedBucket = Literal["similar", "discover"]


@dataclass(frozen=True)
class TrackInfo:
    ref: str
    title: str
    artist: str | None
    duration: float | None
    energy: int | None
    tags: list[str]
    rating: Rating | None
    added_at: float

    def track(self, origin: Any = "liked") -> Track:
        return Track(title=self.title, source="youtube", ref=self.ref, duration=self.duration,
                     artist=self.artist, origin=origin)


@dataclass
class ArtistStats:
    dislikes: int = 0
    likes: int = 0
    skips_30d: int = 0
    finished_30d: int = 0
    slot_finished: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Stats:
    days: int
    wave_finished: int
    wave_skipped: int
    wave_disliked: int
    wave_share: float | None
    stalls: int
    errors: int
    from_cache_share: float | None
    median_start_ms: int | None


@dataclass(frozen=True)
class Seed:
    query: str
    bucket: SeedBucket
    reason: str = ""


def artist_key(artist: str | None) -> str:
    return (artist or "").strip().casefold()


class Library:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None

    @property
    def db(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            self._conn = conn
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # --- треки и оценки ---------------------------------------------------

    def upsert(self, track: Track) -> None:
        """Новые поля не затирают известные: у выдачи поиска нет исполнителя,
        а у того же трека из радио YouTube Music — есть."""
        self.db.execute(
            """INSERT INTO tracks (ref, title, artist, duration, added_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(ref) DO UPDATE SET
                 title = excluded.title,
                 artist = COALESCE(tracks.artist, excluded.artist),
                 duration = COALESCE(excluded.duration, tracks.duration)""",
            (track.ref, clean_title(track.title), track.artist, track.duration, time.time()),
        )

    def set_artist(self, ref: str, artist: str | None, duration: float | None) -> None:
        self.db.execute(
            "UPDATE tracks SET artist = COALESCE(?, artist), duration = COALESCE(duration, ?) WHERE ref = ?",
            (artist, duration, ref),
        )

    def rate(self, ref: str, value: Rating | None, now: float | None = None) -> None:
        if value is None:
            self.db.execute("DELETE FROM ratings WHERE ref = ?", (ref,))
        else:
            self.db.execute(
                "INSERT INTO ratings (ref, value, at) VALUES (?, ?, ?) "
                "ON CONFLICT(ref) DO UPDATE SET value = excluded.value, at = excluded.at",
                (ref, value, now or time.time()),
            )

    def rating(self, ref: str) -> Rating | None:
        row = self.db.execute("SELECT value FROM ratings WHERE ref = ?", (ref,)).fetchone()
        return row["value"] if row else None

    def _infos(self, where: str, params: tuple[Any, ...], order: str, limit: int, offset: int = 0) -> list[TrackInfo]:
        rows = self.db.execute(
            f"""SELECT t.ref, t.title, t.artist, t.duration, t.energy, t.tags, r.value AS rating,
                       COALESCE(r.at, t.added_at) AS at
                FROM tracks t LEFT JOIN ratings r ON r.ref = t.ref
                WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        return [
            TrackInfo(ref=r["ref"], title=r["title"], artist=r["artist"], duration=r["duration"],
                      energy=r["energy"], tags=json.loads(r["tags"]) if r["tags"] else [],
                      rating=r["rating"], added_at=r["at"])
            for r in rows
        ]

    def track(self, ref: str) -> TrackInfo | None:
        found = self._infos("t.ref = ?", (ref,), "t.ref", 1)
        return found[0] if found else None

    def liked(self, offset: int = 0, limit: int = 50, query: str | None = None) -> list[TrackInfo]:
        where, params = "r.value = 1", ()
        if query:
            where += " AND (t.title LIKE ? OR t.artist LIKE ?)"
            params = (f"%{query}%", f"%{query}%")
        return self._infos(where, params, "r.at DESC", limit, offset)

    def liked_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM ratings WHERE value = 1").fetchone()[0]

    def disliked(self) -> list[TrackInfo]:
        return self._infos("r.value = -1", (), "r.at DESC", 500)

    def banned_refs(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT ref FROM ratings WHERE value = -1")}

    def played_from_cache(self, limit: int = 200) -> list[TrackInfo]:
        """Дослушанное и не забаненное — запас волны на случай без интернета."""
        return self._infos(
            "t.ref IN (SELECT ref FROM plays WHERE outcome = 'finished') AND COALESCE(r.value, 0) >= 0",
            (), "t.added_at DESC", limit,
        )

    def artist_stats(self, now: float | None = None) -> dict[str, ArtistStats]:
        now = now or time.time()
        stats: dict[str, ArtistStats] = {}
        for row in self.db.execute(
            "SELECT t.artist, r.value FROM ratings r JOIN tracks t ON t.ref = r.ref WHERE t.artist IS NOT NULL"
        ):
            s = stats.setdefault(artist_key(row["artist"]), ArtistStats())
            if row["value"] < 0:
                s.dislikes += 1
            else:
                s.likes += 1
        for row in self.db.execute(
            """SELECT t.artist, p.outcome, p.slot, COUNT(*) AS n FROM plays p JOIN tracks t ON t.ref = p.ref
               WHERE t.artist IS NOT NULL AND p.started_at > ? GROUP BY t.artist, p.outcome, p.slot""",
            (now - 30 * DAY,),
        ):
            s = stats.setdefault(artist_key(row["artist"]), ArtistStats())
            if row["outcome"] == "skipped":
                s.skips_30d += row["n"]
            elif row["outcome"] == "finished":
                s.finished_30d += row["n"]
                s.slot_finished[row["slot"]] = s.slot_finished.get(row["slot"], 0) + row["n"]
        return stats

    def disliked_artists(self, min_dislikes: int = 2) -> list[tuple[str, int]]:
        # Группируем в Python: LOWER() в SQLite не знает кириллицы («КИНО» ≠ «кино»).
        counts: dict[str, tuple[str, int]] = {}
        for row in self.db.execute(
            "SELECT t.artist FROM ratings r JOIN tracks t ON t.ref = r.ref WHERE r.value = -1 AND t.artist IS NOT NULL"
        ):
            name, n = counts.get(artist_key(row[0]), (row[0], 0))
            counts[artist_key(row[0])] = (name, n + 1)
        return sorted((c for c in counts.values() if c[1] >= min_dislikes), key=lambda c: -c[1])

    def unmute_artist(self, artist: str) -> int:
        """«Вернуть» исполнителя: снять все его дизлайки."""
        refs = [r[0] for r in self.db.execute(
            "SELECT r.ref, t.artist FROM ratings r JOIN tracks t ON t.ref = r.ref WHERE r.value = -1"
        ).fetchall() if artist_key(r[1]) == artist_key(artist)]
        self.db.executemany("DELETE FROM ratings WHERE ref = ?", [(r,) for r in refs])
        return len(refs)

    def recent_refs(self, hours: float, now: float | None = None) -> set[str]:
        since = (now or time.time()) - hours * 3600
        return {r[0] for r in self.db.execute("SELECT ref FROM plays WHERE started_at > ?", (since,))}

    def set_tags(self, ref: str, artist: str | None, energy: int | None, tags: list[str]) -> None:
        self.db.execute(
            "UPDATE tracks SET artist = COALESCE(?, artist), energy = ?, tags = ? WHERE ref = ?",
            (artist, energy, json.dumps(tags, ensure_ascii=False), ref),
        )

    def energies(self, refs: list[str]) -> dict[str, int]:
        if not refs:
            return {}
        marks = ",".join("?" * len(refs))
        rows = self.db.execute(f"SELECT ref, energy FROM tracks WHERE energy IS NOT NULL AND ref IN ({marks})", refs)
        return {r[0]: r[1] for r in rows}

    def untagged_liked(self, limit: int = 20) -> list[TrackInfo]:
        return self._infos("r.value = 1 AND t.energy IS NULL", (), "r.at DESC", limit)

    # --- журнал прослушиваний --------------------------------------------

    def start_play(
        self, track: Track, device: str, mood: Mood | None, where: FromWhere,
        start_ms: int | None, now: float | None = None,
    ) -> int:
        now = now or time.time()
        self.upsert(track)
        cur = self.db.execute(
            """INSERT INTO plays (ref, device, started_at, slot, weekend, origin, mood, from_where, start_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (track.ref, device, now, slot_at(now), int(is_weekend(now)), track.origin, mood, where, start_ms),
        )
        return int(cur.lastrowid or 0)

    def finish_play(self, play_id: int, outcome: Outcome, listened_s: float | None) -> None:
        self.db.execute(
            "UPDATE plays SET outcome = ?, listened_s = ? WHERE id = ? AND outcome IS NULL",
            (outcome, listened_s, play_id),
        )

    # --- заметки и зёрна для dj --------------------------------------------

    def add_note(self, text: str) -> None:
        self.db.execute("INSERT INTO notes (text, at) VALUES (?, ?)", (text.strip(), time.time()))

    def notes(self, limit: int = 30) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT text FROM notes ORDER BY at DESC LIMIT ?", (limit,))]

    def save_seeds(self, seeds: list[Seed], slot: Slot, mood: Mood, now: float | None = None) -> None:
        now = now or time.time()
        self.db.execute("DELETE FROM seeds WHERE slot = ? AND mood = ?", (slot, mood))
        self.db.executemany(
            "INSERT INTO seeds (query, bucket, slot, mood, reason, at) VALUES (?, ?, ?, ?, ?, ?)",
            [(s.query, s.bucket, slot, mood, s.reason, now) for s in seeds],
        )

    def seeds(self, slot: Slot, mood: Mood, max_age_h: float | None = None, now: float | None = None) -> list[Seed]:
        since = (now or time.time()) - max_age_h * 3600 if max_age_h is not None else 0.0
        rows = self.db.execute(
            "SELECT query, bucket, reason FROM seeds WHERE slot = ? AND mood = ? AND at > ?", (slot, mood, since)
        ).fetchall()
        return [Seed(query=r["query"], bucket=r["bucket"], reason=r["reason"] or "") for r in rows]

    def taste_summary(self, slot: Slot | None = None, days: float = 30, now: float | None = None) -> dict[str, Any]:
        """Компактно для dj: кого любит, кого нет, что пропускает в этот слот."""
        now = now or time.time()
        stats = self.artist_stats(now)
        names = {artist_key(r[0]): r[0] for r in self.db.execute("SELECT DISTINCT artist FROM tracks WHERE artist IS NOT NULL")}

        def top(key: Any, n: int) -> list[str]:
            ranked = sorted(((key(s), a) for a, s in stats.items() if key(s) > 0), reverse=True)
            return [names.get(a, a) for _, a in ranked[:n]]

        summary: dict[str, Any] = {
            "liked_artists": top(lambda s: s.likes * 3 + s.finished_30d, 25),
            "disliked_artists": [a for a, _ in self.disliked_artists(1)][:25],
            "often_skipped_artists": top(lambda s: s.skips_30d - s.finished_30d, 10),
            "recent_likes": [f"{t.artist or '?'} — {t.title}" for t in self.liked(limit=15)],
            "recent_dislikes": [f"{t.artist or '?'} — {t.title}" for t in self.disliked()[:10]],
            "notes": self.notes(15),
        }
        if slot:
            summary["slot_favorites"] = top(lambda s: s.slot_finished.get(slot, 0), 10)
        return summary

    def day_digest(self, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        rows = self.db.execute(
            """SELECT p.slot, p.outcome, p.mood, t.artist, t.title FROM plays p JOIN tracks t ON t.ref = p.ref
               WHERE p.started_at > ? AND p.outcome IS NOT NULL ORDER BY p.started_at""",
            (now - DAY,),
        ).fetchall()
        return {
            "plays": [f"{r['slot']}/{r['mood'] or '-'}: {r['artist'] or '?'} — {r['title']} → {r['outcome']}" for r in rows][-150:],
            "summary": self.taste_summary(now=now),
        }

    # --- метрика ------------------------------------------------------------

    def stats(self, days: int = 7, now: float | None = None) -> Stats:
        since = (now or time.time()) - days * DAY
        counts = {
            (r["origin"] == "wave", r["outcome"]): r["n"]
            for r in self.db.execute(
                "SELECT origin, outcome, COUNT(*) AS n FROM plays WHERE started_at > ? GROUP BY origin = 'wave', outcome",
                (since,),
            )
        }
        finished = counts.get((True, "finished"), 0)
        skipped = counts.get((True, "skipped"), 0)
        disliked = counts.get((True, "disliked"), 0)
        judged = finished + skipped + disliked
        stalls = sum(n for (_, outcome), n in counts.items() if outcome == "stalled")
        errors = sum(n for (_, outcome), n in counts.items() if outcome == "error")
        where = [r[0] for r in self.db.execute(
            "SELECT from_where FROM plays WHERE started_at > ? AND from_where IS NOT NULL", (since,))]
        starts = [r[0] for r in self.db.execute(
            "SELECT start_ms FROM plays WHERE started_at > ? AND start_ms IS NOT NULL", (since,))]
        return Stats(
            days=days, wave_finished=finished, wave_skipped=skipped, wave_disliked=disliked,
            wave_share=round(finished / judged, 3) if judged else None,
            stalls=stalls, errors=errors,
            from_cache_share=round(sum(w in ("liked", "cache") for w in where) / len(where), 3) if where else None,
            median_start_ms=int(statistics.median(starts)) if starts else None,
        )


library = Library(Path(settings.music_library_db))
