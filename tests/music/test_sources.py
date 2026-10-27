import pytest

from app.music import sources, yandex, youtube
from app.music.models import Service, Track


def ym(title: str, artist: str) -> Track:
    return Track(title=title, source="yandex", ref=f"ym-{abs(hash(title)) % 999}", artist=artist, service="yandex")


def yt(title: str, artist: str | None = None, service: Service = "ytmusic") -> Track:
    return Track(title=title, source="youtube", ref=f"yt{abs(hash(title)) % 99999:09d}", artist=artist, service=service)


def test_match_score_exact_artist_title() -> None:
    assert sources.match_score("Кино Группа крови", ym("Группа крови", "КИНО")) >= 0.9


def test_match_score_penalizes_cover_live_remix() -> None:
    assert sources.match_score("кино кукушка", ym("Кукушка (cover)", "Кино")) < sources.MATCH_THRESHOLD


def test_match_score_keeps_live_when_asked() -> None:
    assert sources.match_score("кино кукушка live", ym("Кукушка (Live)", "Кино")) >= sources.MATCH_THRESHOLD


def _mock(monkeypatch: pytest.MonkeyPatch, ym_: list[Track], ytm: list[Track], yt_: list[Track], sc: list[Track]) -> None:
    async def f(result):  # noqa: ANN001
        return result

    monkeypatch.setattr(yandex, "search", lambda q, limit=3: f(ym_))
    monkeypatch.setattr(youtube, "music_search", lambda q, limit=3: f(ytm))
    monkeypatch.setattr(youtube, "search", lambda q, kind="music": f(yt_))
    monkeypatch.setattr(youtube, "soundcloud_search", lambda q, limit=3: f(sc))


async def test_resolve_prefers_yandex_when_good(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock(monkeypatch, [ym("Группа крови", "КИНО")], [yt("Группа крови", "Кино")], [], [])
    assert (await sources.resolve("кино группа крови"))[0].source == "yandex"


async def test_resolve_falls_back_to_youtube_when_poor(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock(monkeypatch, [ym("Другая песня", "Кто-то")], [yt("Редкий трек", "Андеграунд")], [], [])
    assert (await sources.resolve("андеграунд редкий трек"))[0].service == "ytmusic"


async def test_resolve_soundcloud_last(monkeypatch: pytest.MonkeyPatch) -> None:
    sc = Track(title="Bootleg Remix", source="soundcloud", ref="sc-1", artist="DJ X", service="soundcloud")
    _mock(monkeypatch, [], [], [], [sc])
    assert (await sources.resolve("dj x bootleg remix"))[0].ref == "sc-1"


async def test_resolve_works_when_yandex_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock(monkeypatch, [], [yt("Everlong", "Foo Fighters")], [], [])

    async def boom(q, limit=3):  # noqa: ANN001
        raise RuntimeError("401")

    monkeypatch.setattr(yandex, "search", boom)
    assert (await sources.resolve("foo fighters everlong"))[0].title == "Everlong"


def test_is_song() -> None:
    assert sources.is_song(ym("a", "b"))
    assert sources.is_song(yt("a"))
    assert not sources.is_song(Track(title="r", source="radio", ref="http://x"))
    assert not sources.is_song(Track(title="book", source="youtube", ref="abcdefghijk", kind="audiobook"))


async def test_upgrade_swaps_youtube_for_same_yandex_song(monkeypatch: pytest.MonkeyPatch) -> None:
    same = Track(title="Everlong", source="yandex", ref="ym-1", artist="Foo Fighters", duration=250, service="yandex")
    live = Track(title="Everlong (Live)", source="yandex", ref="ym-2", artist="Foo Fighters", duration=320,
                 service="yandex")

    async def search(query: str, limit: int = 3) -> list[Track]:
        return [live, same] if "Everlong" in query else [live]

    monkeypatch.setattr(yandex, "is_on", lambda: True)
    monkeypatch.setattr(yandex, "search", search)
    song = Track(title="Foo Fighters - Everlong (Official Video)", source="youtube", ref="eBG7P-K-r1Y",
                 artist="Foo Fighters", duration=252, origin="wave")
    other = Track(title="Unknown jam", source="youtube", ref="aaaaaaaaaaa", artist="Nobody", duration=200)
    out = await sources.upgrade([song, other])
    assert [t.ref for t in out] == ["ym-1", "aaaaaaaaaaa"]
    assert out[0].origin == "wave"


async def test_upgrade_rejects_different_length(monkeypatch: pytest.MonkeyPatch) -> None:
    edit = Track(title="Everlong", source="yandex", ref="ym-1", artist="Foo Fighters", duration=180, service="yandex")

    async def search(query: str, limit: int = 3) -> list[Track]:
        return [edit]

    monkeypatch.setattr(yandex, "is_on", lambda: True)
    monkeypatch.setattr(yandex, "search", search)
    song = Track(title="Everlong", source="youtube", ref="eBG7P-K-r1Y", artist="Foo Fighters", duration=250)
    assert (await sources.upgrade([song]))[0].ref == "eBG7P-K-r1Y"
