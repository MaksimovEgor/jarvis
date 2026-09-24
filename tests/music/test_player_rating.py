from dataclasses import replace

import pytest

from app.music import taste, wave, youtube
from app.music.library import library
from app.music.player import Player
from tests.conftest import FakeOutput, yt


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    # Без сети и фоновых задач dj.
    monkeypatch.setattr(taste, "tag_soon", lambda ref: None)
    monkeypatch.setattr(taste, "refresh_seeds_soon", lambda slot, mood: None)

    async def no_energy(tracks, timeout: float = 30):
        return {}

    monkeypatch.setattr(taste, "energies_for", no_energy)


async def _player(*refs: str) -> tuple[Player, FakeOutput]:
    output = FakeOutput()
    player = Player("web:test-device", output)
    if refs:
        await player._start_queue([yt(r, f"artist-{r}") for r in refs])
    return player, output


async def _no_download(track, timeout: float = 0):
    return None


def _outcomes() -> dict[str, str]:
    return {r[0]: r[1] for r in library.db.execute("SELECT ref, outcome FROM plays")}


async def test_dislike_skips_current_within_same_call() -> None:
    player, output = await _player("a", "b", "c")
    reply = await player.rate(-1)
    assert player.current.ref == "b"
    assert "не включу" in reply
    assert _outcomes()["a"] == "disliked"
    assert library.rating("a") == -1


async def test_dislike_removes_track_from_upcoming() -> None:
    player, _ = await _player("a", "b", "c")
    await player.rate(-1, ref="c")
    assert [t.ref for t in player._queue] == ["a", "b"]
    assert player.current.ref == "a"


async def test_like_sets_meta_and_rating() -> None:
    player, output = await _player("a")
    await player.rate(1)
    assert library.rating("a") == 1
    assert output.meta["rating"] == 1


async def test_like_previous() -> None:
    player, _ = await _player("a", "b")
    await player.next()
    await player.rate(1, which="previous")
    assert library.rating("a") == 1 and library.rating("b") is None


async def test_next_before_30s_records_skipped() -> None:
    player, output = await _player("a", "b")
    output.pos = 12
    await player.next()
    assert _outcomes()["a"] == "skipped"


async def test_next_after_half_records_finished() -> None:
    player, output = await _player("a", "b")
    output.pos = 150
    await player.next()
    assert _outcomes()["a"] == "finished"


async def test_stall_advances_and_records_stalled() -> None:
    player, output = await _player("a", "b")
    await output._ended("stall")
    assert player.current.ref == "b"
    assert _outcomes()["a"] == "stalled"


async def test_mix_filters_banned(monkeypatch: pytest.MonkeyPatch) -> None:
    library.upsert(yt("bad"))
    library.rate("bad", -1)

    async def mix(seed):
        return [yt("bad"), yt("ok")]

    monkeypatch.setattr(youtube, "mix", mix)
    player, _ = await _player("a")
    await player._extend_with_mix(player.current, player._generation)
    assert [t.ref for t in player._queue] == ["a", "ok"]


async def test_refill_when_less_than_3_ahead(monkeypatch: pytest.MonkeyPatch) -> None:
    async def more(self, queued, n=8):
        return [replace(yt(f"w{i}", f"wa{i}"), origin="wave") for i in range(n)]

    monkeypatch.setattr(wave.Wave, "more", more)
    player, _ = await _player("a")
    player.wave = wave.Wave(player.device)
    await player._refill(player._generation)
    assert len(player._queue) == 1 + wave.AHEAD


async def test_wave_starts_when_only_like_was_just_played(monkeypatch: pytest.MonkeyPatch) -> None:
    # Регрессия: единственный лайк звучал < 3 ч назад — волна молчала.
    library.upsert(yt("liked1", "SOAD"))
    library.rate("liked1", 1)
    library.start_play(yt("liked1", "SOAD"), "web:x", None, "liked", 0)
    radio_calls = []

    async def radio(ref: str, limit: int = 25):
        radio_calls.append(ref)
        return [yt(f"r{i}", f"ra{i}") for i in range(6)]

    async def search(query: str, limit: int = 8):
        return []

    monkeypatch.setattr(youtube, "music_radio", radio)
    monkeypatch.setattr(youtube, "music_search", search)
    monkeypatch.setattr(youtube, "download", _no_download)
    player, _ = await _player()
    reply = await player.play_wave("auto")
    assert radio_calls == ["liked1"]
    assert player.current is not None and player.current.origin == "wave"
    assert "волну" in reply


async def test_wave_more_filters_by_tagged_energy(monkeypatch: pytest.MonkeyPatch) -> None:
    async def radio(ref: str, limit: int = 25):
        return [yt("loud", "RATM"), yt("soft", "Nutshell")]

    async def energies(tracks, timeout: float = 30):
        return {"loud": 5, "soft": 2}

    library.upsert(yt("seed", "AiC"))
    library.rate("seed", 1)
    monkeypatch.setattr(youtube, "music_radio", radio)
    monkeypatch.setattr(taste, "energies_for", energies)
    refs = {t.ref for t in await wave.Wave("web:x", "calm").more([], 5)}
    assert "loud" not in refs and "soft" in refs


async def test_strict_mood_start_skips_like_radio(monkeypatch: pytest.MonkeyPatch) -> None:
    library.upsert(yt("seed", "SOAD"))
    library.rate("seed", 1)
    radio_calls, searches = [], []

    async def radio(ref: str, limit: int = 25):
        radio_calls.append(ref)
        return [yt(f"r{ref}{i}", f"a{ref}{i}") for i in range(3)]

    async def search(query: str, limit: int = 8):
        searches.append(query)
        return [yt("calm1", "Piano")]

    monkeypatch.setattr(youtube, "music_radio", radio)
    monkeypatch.setattr(youtube, "music_search", search)
    await wave.Wave("web:x", "sleep").more([], 3, tag=False)
    assert "seed" not in radio_calls
    assert searches == [wave.COLD_START["sleep"]]


async def test_watchdog_ignores_track_that_never_started(monkeypatch: pytest.MonkeyPatch) -> None:
    # Регрессия: iOS заблокировал play() — позиция не росла, сторож листал треки.
    from app.music.outputs import WebOutput

    output = WebOutput("web:watchdog-test")
    monkeypatch.setattr(WebOutput, "STALL_SECONDS", 0.0)
    monkeypatch.setattr(WebOutput, "STALL_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(WebOutput, "_busy", lambda self: False)
    monkeypatch.setattr(WebOutput, "watching", property(lambda self: True))
    player = Player("web:watchdog-test", output)
    monkeypatch.setattr(output, "_src", _fake_src)
    monkeypatch.setattr(output, "prefetch", lambda track: None)
    await player._start_queue([yt("a", "A"), yt("b", "B")])
    import asyncio
    await asyncio.sleep(0.1)
    assert player.current.ref == "a"
    await output.report({"seq": output._state["seq"], "position": 5.0})
    await asyncio.sleep(0.1)
    assert player.current.ref == "b"
    output._watchdog.cancel()


async def _fake_src(track):
    return f"media/yt/{track.ref}"


async def test_meta_has_display_fields() -> None:
    player, output = await _player("a", "b")
    assert output.meta["song"] == "song a" and output.meta["artist"] == "artist-a"
    assert output.meta["cover"] == "media/cover/a"
    assert [u["ref"] for u in output.meta["upcoming"]] == ["b"]
