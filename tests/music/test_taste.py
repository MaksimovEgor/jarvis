import httpx
import pytest

from app.music import taste


def test_parse_seeds_from_fenced_json() -> None:
    text = 'Вот:\n```json\n{"seeds": [{"query": "Muse - Starlight", "bucket": "similar", "reason": "рок"}]}\n```'
    seeds = taste.parse_seeds(text)
    assert [(s.query, s.bucket) for s in seeds] == [("Muse - Starlight", "similar")]


def test_parse_seeds_ignores_bad_items() -> None:
    text = '{"seeds": [{"query": ""}, "мусор", {"query": "Radiohead", "bucket": "weird"}]}'
    seeds = taste.parse_seeds(text)
    assert [(s.query, s.bucket) for s in seeds] == [("Radiohead", "discover")]


def test_parse_seeds_garbage() -> None:
    assert taste.parse_seeds("не знаю") == []


def test_parse_tags_clamps_energy_1_5() -> None:
    text = '{"tracks": [{"ref": "a", "energy": 9, "tags": ["Rock"]}, {"ref": "b", "energy": "x"}, {"energy": 3}]}'
    assert taste.parse_tags(text) == [("a", None, 5, ["rock"]), ("b", None, None, [])]


async def test_ask_falls_back_to_llm_when_dj_down(monkeypatch: pytest.MonkeyPatch) -> None:
    async def dj(prompt: str) -> str:
        raise httpx.ConnectError("down")

    async def llm(prompt: str) -> str:
        return '{"seeds": [{"query": "Foo Fighters - Everlong", "bucket": "similar"}]}'

    monkeypatch.setattr(taste, "_ask_dj", dj)
    monkeypatch.setattr(taste, "_ask_llm", llm)
    seeds = await taste.ask_seeds("evening", "auto", False)
    assert seeds[0].query == "Foo Fighters - Everlong"


async def test_ask_returns_empty_when_both_down(monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(prompt: str) -> str:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(taste, "_ask_dj", down)
    monkeypatch.setattr(taste, "_ask_llm", down)
    assert await taste.ask_seeds("evening", "auto", False) == []


async def test_digest_is_dj_only(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []

    async def down(prompt: str) -> str:
        raise httpx.ConnectError("down")

    async def llm(prompt: str) -> str:
        called.append(prompt)
        return "ok"

    monkeypatch.setattr(taste, "_ask_dj", down)
    monkeypatch.setattr(taste, "_ask_llm", llm)
    assert await taste.ask("x", dj_only=True) is None
    assert called == []
