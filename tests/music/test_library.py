import time
from dataclasses import replace

from app.music.library import library
from tests.conftest import yt

NOW = time.time()


def test_rate_like_then_none_clears_rating() -> None:
    library.upsert(yt("a"))
    library.rate("a", 1)
    assert library.rating("a") == 1
    assert [t.ref for t in library.liked()] == ["a"]
    library.rate("a", None)
    assert library.rating("a") is None
    assert library.liked() == []


def test_banned_refs_contains_only_disliked() -> None:
    for ref, value in (("a", 1), ("b", -1), ("c", -1)):
        library.upsert(yt(ref))
        library.rate(ref, value)
    assert library.banned_refs() == {"b", "c"}


def test_artist_stats_counts_dislikes_case_insensitive() -> None:
    library.upsert(yt("a", "Кино"))
    library.upsert(yt("b", "КИНО"))
    library.rate("a", -1)
    library.rate("b", -1)
    stats = library.artist_stats()
    assert stats["кино"].dislikes == 2
    assert library.disliked_artists(2)[0][1] == 2


def test_upsert_keeps_known_artist() -> None:
    library.upsert(yt("a", "Queen"))
    library.upsert(yt("a", None))
    assert library.track("a").artist == "Queen"


def test_recent_refs_respects_window() -> None:
    library.start_play(yt("old"), "web:x", None, "net", None, now=NOW - 4 * 3600)
    library.start_play(yt("new"), "web:x", None, "net", None, now=NOW - 2 * 3600)
    assert library.recent_refs(3, now=NOW) == {"new"}


def _play(ref: str, outcome: str, origin: str = "wave", when: float = NOW - 60, where: str = "net") -> None:
    track = replace(yt(ref), origin=origin)
    play_id = library.start_play(track, "web:x", None, where, 1000, now=when)
    library.finish_play(play_id, outcome, 10)


def test_stats_wave_share() -> None:
    for i in range(7):
        _play(f"f{i}", "finished")
    for i in range(2):
        _play(f"s{i}", "skipped")
    _play("d", "disliked")
    for i in range(5):
        _play(f"q{i}", "skipped", origin="query")
    stats = library.stats(7, now=NOW)
    assert stats.wave_share == 0.7
    assert stats.wave_skipped == 2


def test_stats_ignores_older_than_days() -> None:
    _play("old", "skipped", when=NOW - 10 * 86400)
    _play("new", "finished")
    assert library.stats(7, now=NOW).wave_share == 1.0


def test_stats_counts_stalls_and_cache_share() -> None:
    _play("a", "stalled", where="cache")
    _play("b", "finished", where="liked")
    _play("c", "finished", where="net")
    _play("d", "finished", where="net")
    stats = library.stats(7, now=NOW)
    assert stats.stalls == 1
    assert stats.from_cache_share == 0.5


def test_finish_play_only_once() -> None:
    play_id = library.start_play(yt("a"), "web:x", None, "net", None)
    library.finish_play(play_id, "finished", 100)
    library.finish_play(play_id, "stopped", 120)
    assert library.stats(7).wave_finished == 0  # origin=query
    row = library.db.execute("SELECT outcome FROM plays WHERE id = ?", (play_id,)).fetchone()
    assert row[0] == "finished"


def test_unmute_artist_clears_all_dislikes() -> None:
    library.upsert(yt("a", "Кино"))
    library.upsert(yt("b", "КИНО"))
    library.rate("a", -1)
    library.rate("b", -1)
    assert library.unmute_artist("кино") == 2
    assert library.banned_refs() == set()
