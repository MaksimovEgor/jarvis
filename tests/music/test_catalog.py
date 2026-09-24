from types import SimpleNamespace as NS

import pytest

from app.music import catalog, positions, yandex, youtube
from app.music.models import Entity, Track


def album(id_: int, type_: str, genre: str = "", title: str = "X") -> NS:
    return NS(id=id_, title=title, artists=[NS(name="Автор")], year=2020, cover_uri=None,
              type=type_, genre=genre, track_count=3)


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog, "_cache", {})
    monkeypatch.setattr(yandex, "is_on", lambda: True)

    async def nothing(q, kind="music"):  # noqa: ANN001
        return []

    monkeypatch.setattr(youtube, "search", nothing)


async def test_books_and_podcasts_split_by_album_type(monkeypatch: pytest.MonkeyPatch) -> None:
    async def search_all(q, type_="all"):  # noqa: ANN001
        return NS(podcasts=NS(results=[album(1, "audiobook"), album(2, "podcast")]))

    monkeypatch.setattr(yandex, "search_all", search_all)
    books = await catalog.search("мастер", "books")
    pods = await catalog.search("мастер", "podcasts")
    assert [e.id for e in books[0].items] == ["1"] and books[0].items[0].type == "audiobook"
    assert [e.id for e in pods[0].items] == ["2"]


async def test_kids_filters_by_genre_and_adds_stations(monkeypatch: pytest.MonkeyPatch) -> None:
    async def stations():
        return {"editorial:station-1": "Сказки для сна", "editorial:station-17": "Колыбельные"}

    async def search_all(q, type_="all"):  # noqa: ANN001
        if type_ == "podcast":
            return NS(podcasts=NS(results=[album(1, "audiobook", "childrensliterature"), album(2, "audiobook", "fantasy")]))
        return NS(albums=NS(results=[album(3, "", "children"), album(4, "", "rock")]))

    monkeypatch.setattr(yandex, "stations", stations)
    monkeypatch.setattr(yandex, "search_all", search_all)
    sections = {s.kind: s for s in await catalog.search("незнайка", "kids")}
    assert [e.title for e in sections["stations"].items] == ["Сказки для сна", "Колыбельные"]
    assert [e.id for e in sections["books"].items] == ["1"]
    assert [e.id for e in sections["albums"].items] == ["3"]


async def test_search_cached_10_min(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def sc(q, limit=8):  # noqa: ANN001
        calls.append(q)
        return [Track(title="T", source="soundcloud", ref="sc-1", service="soundcloud")]

    monkeypatch.setattr(youtube, "soundcloud_search", sc)
    await catalog.search("vein", "soundcloud")
    await catalog.search("  Vein ", "soundcloud")
    assert calls == ["vein"]


async def test_resolve_audiobook_starts_at_first_unfinished(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setattr(positions, "STORE", tmp_path / "positions.json")
    chapters = [Track(title=f"Часть {i}", source="yandex", ref=f"ym-{i}", duration=1600, kind="audiobook",
                      service="yandex") for i in range(4)]

    async def album_tracks(album_id):  # noqa: ANN001
        return None, chapters

    monkeypatch.setattr(yandex, "album_tracks", album_tracks)
    positions.save(chapters[0], 1600, finished=True)
    positions.save(chapters[1], 1600, finished=True)
    _, start = await catalog.resolve_entity(Entity(source="yandex", type="audiobook", id="9", title="Незнайка"))
    assert start == 2
    positions.save(chapters[3], 300)
    _, start = await catalog.resolve_entity(Entity(source="yandex", type="audiobook", id="9", title="Незнайка"))
    assert start == 3
