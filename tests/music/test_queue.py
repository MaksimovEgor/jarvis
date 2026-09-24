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
