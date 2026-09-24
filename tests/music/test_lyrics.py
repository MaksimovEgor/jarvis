import pytest

from app.music import lyrics, yandex
from app.music.models import Track


def test_parse_lrc_basic() -> None:
    assert lyrics.parse_lrc("[ar:КИНО]\n[00:31.80] Тёплое место\n[00:40.41] Звёздная пыль") == [
        (31.8, "Тёплое место"), (40.41, "Звёздная пыль")]


def test_parse_lrc_multiple_timestamps_and_blank() -> None:
    assert lyrics.parse_lrc("[00:50.00][00:10.00]припев\n[00:20.00]") == [(10.0, "припев"), (20.0, ""), (50.0, "припев")]


YM = Track(title="Группа крови", source="yandex", ref="ym-1", artist="КИНО", duration=286, service="yandex")
YT = Track(title="Old Data in a Dead Machine", source="soundcloud", ref="sc-1", artist="Vein", service="soundcloud")


async def test_get_prefers_yandex_for_ym(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ym_lyrics(ref):  # noqa: ANN001
        return "[00:01.00]Тёплое место", "Тёплое место"

    async def lrclib(*a):  # noqa: ANN002
        raise AssertionError("LRCLIB не нужен")

    monkeypatch.setattr(yandex, "is_on", lambda: True)
    monkeypatch.setattr(yandex, "lyrics", ym_lyrics)
    monkeypatch.setattr(lyrics, "_lrclib", lrclib)
    found = await lyrics.get(YM)
    assert found.source == "yandex" and found.synced == [(1.0, "Тёплое место")]


async def test_get_falls_back_to_lrclib(monkeypatch: pytest.MonkeyPatch) -> None:
    async def req(path, params):  # noqa: ANN001
        return {"syncedLyrics": "[00:04.93] Words create lies", "plainLyrics": "Words create lies"} if path == "get" else []

    monkeypatch.setattr(lyrics, "_lrclib_request", req)
    found = await lyrics.get(YT)
    assert found.source == "lrclib" and found.synced == [(4.93, "Words create lies")]


async def test_get_none_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def req(path, params):  # noqa: ANN001
        calls.append(path)
        return None if path == "get" else []

    monkeypatch.setattr(lyrics, "_lrclib_request", req)
    assert (await lyrics.get(YT)).source is None
    assert (await lyrics.get(YT)).source is None
    assert calls == ["get", "search"]
