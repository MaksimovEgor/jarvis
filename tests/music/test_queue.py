from app.music.player import Player
from tests.conftest import FakeOutput, yt


async def _player(*refs: str) -> Player:
    player = Player("web:queue-test", FakeOutput())
    await player._start_queue([yt(r, f"a-{r}") for r in refs])
    return player


def _refs(player: Player) -> list[str]:
    return [t.ref for t in player._queue]


async def test_queue_move_keeps_current() -> None:
    player = await _player("a", "b", "c", "d")
    await player.queue_move(3, 1)
    assert _refs(player) == ["a", "d", "b", "c"]
    await player.queue_move(0, 2)  # текущий не двигается
    assert _refs(player) == ["a", "d", "b", "c"]


async def test_queue_remove_future_only() -> None:
    player = await _player("a", "b", "c")
    await player.queue_remove(0)
    await player.queue_remove(2)
    assert _refs(player) == ["a", "b"]


async def test_play_at() -> None:
    player = await _player("a", "b", "c")
    await player.play_at(2)
    assert player.current.ref == "c"


async def test_add_next_inserts_after_current() -> None:
    player = await _player("a", "b")
    await player.add_next(yt("n", "N"))
    assert _refs(player) == ["a", "n", "b"]
    assert player.queue_view()["pos"] == 0


async def test_mix_refills_near_end_without_repeats(monkeypatch) -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.music import sources

    similar = AsyncMock(return_value=[yt("b", "B"), yt("x", "X"), yt("y", "Y")])
    monkeypatch.setattr(sources, "similar", similar)
    player = await _player("a", "b")
    await asyncio.sleep(0)
    # Впереди 1 трек (< 2) — досыпали похожих, «b» не повторился.
    assert _refs(player) == ["a", "b", "x", "y"]
    assert similar.await_count == 1


async def test_play_query_nothing_loads_is_an_answer(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from app.music import sources

    class Broken(FakeOutput):
        async def load(self, track, start, paused):
            raise RuntimeError("YouTube не отдал")

    monkeypatch.setattr(sources, "resolve", AsyncMock(return_value=[yt("a", "A"), yt("b", "B")]))
    player = Player("web:queue-test", Broken())
    reply = await player.play_query("что-то")
    assert reply.startswith("Нашёл «что-то», но не получилось загрузить")


def test_catalog_book_must_match_query() -> None:
    from app.music.models import Entity
    from app.music.player import _matches

    def book(title: str, subtitle: str = "") -> Entity:
        return Entity(source="yandex", type="album", id="1", title=title, subtitle=subtitle)

    assert not _matches("сказка автобус", book("Двенадцать месяцев. Часть 1", "Самуил Маршак"))
    assert _matches("сказка автобус который боялся темноты", book("Про малютку-автобус, который боялся темноты", "Дональд Биссет"))
    assert _matches("мастер и маргарита", book("Мастер и Маргарита", "Михаил Булгаков"))
