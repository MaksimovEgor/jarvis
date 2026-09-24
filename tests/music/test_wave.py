import random
import time
from collections import Counter

from app.music import wave
from app.music.library import ArtistStats
from app.music.models import slot_at
from app.music.wave import Candidate, Context, compose, weight
from tests.conftest import yt


def _at(hour: int, minute: int = 0) -> float:
    return time.mktime((2026, 9, 24, hour, minute, 0, 0, 0, -1))


def test_slot_boundaries() -> None:
    assert slot_at(_at(5, 59)) == "night"
    assert slot_at(_at(6)) == "morning"
    assert slot_at(_at(11)) == "day"
    assert slot_at(_at(17)) == "evening"
    assert slot_at(_at(23)) == "night"


def _pool(n: int = 30) -> list[Candidate]:
    pool = []
    for bucket in ("liked", "similar", "discover"):
        pool += [Candidate(yt(f"{bucket}{i}", f"{bucket}-artist{i}"), bucket) for i in range(n)]
    return pool


def test_compose_never_returns_banned() -> None:
    pool = _pool(3)
    ctx = Context("day", "auto", banned={c.track.ref for c in pool[:8]})
    out = compose(pool, ctx, 9, random.Random(1))
    assert not {t.ref for t in out} & ctx.banned


def test_compose_skips_recent() -> None:
    pool = _pool(3)
    ctx = Context("day", "auto", recent={"liked0", "similar0"})
    out = compose(pool, ctx, 7, random.Random(2))
    assert "liked0" not in {t.ref for t in out} and "similar0" not in {t.ref for t in out}


def test_compose_bucket_proportions_auto() -> None:
    rng = random.Random(3)
    counts: Counter[str] = Counter()
    for _ in range(1000):
        for track in compose(_pool(), Context("day", "auto"), 10, rng):
            counts[track.ref.rstrip("0123456789")] += 1
    total = sum(counts.values())
    assert abs(counts["liked"] / total - 0.3) < 0.05
    assert abs(counts["similar"] / total - 0.5) < 0.05
    assert abs(counts["discover"] / total - 0.2) < 0.05


def test_compose_artist_gap() -> None:
    pool = [Candidate(yt(f"q{i}", "Queen"), "similar") for i in range(5)]
    pool += [Candidate(yt(f"o{i}", f"other{i}"), "similar") for i in range(10)]
    out = compose(pool, Context("day", "auto"), 12, random.Random(4))
    positions = [i for i, t in enumerate(out) if t.artist == "Queen"]
    assert all(b - a >= wave.ARTIST_GAP for a, b in zip(positions, positions[1:]))


def test_compose_falls_back_when_bucket_empty() -> None:
    pool = [c for c in _pool(10) if c.bucket != "discover"]
    out = compose(pool, Context("day", "auto"), 10, random.Random(5))
    assert len(out) == 10


def test_weight_disliked_artist_quartered() -> None:
    c = Candidate(yt("a", "Bad"), "similar")
    plain = weight(c, Context("day", "auto"))
    muted = weight(c, Context("day", "auto", artists={"bad": ArtistStats(dislikes=2)}))
    assert muted == plain * wave.DISLIKED_ARTIST_FACTOR


def test_weight_energy_out_of_slot_zero() -> None:
    loud = Candidate(yt("a"), "liked", energy=5)
    untagged = Candidate(yt("b"), "liked")
    ctx = Context("night", "auto")
    assert weight(loud, ctx) == 0
    assert weight(untagged, ctx) > 0
    assert weight(Candidate(yt("c"), "liked", energy=2), ctx) > 0


def test_weight_mood_overrides_slot() -> None:
    assert weight(Candidate(yt("a"), "liked", energy=5), Context("night", "energetic")) > 0
    assert weight(Candidate(yt("a"), "liked", energy=5), Context("morning", "sleep")) == 0


def test_compose_offline_only_liked() -> None:
    pool = [Candidate(yt(f"l{i}", f"a{i}"), "liked") for i in range(5)]
    assert len(compose(pool, Context("day", "auto"), 5, random.Random(6))) == 5


def test_title_noise_removed() -> None:
    from app.music.models import clean_title

    assert clean_title("Green Day - Basket Case [Official Music Video] (4K Upgrade)") == "Green Day - Basket Case"
    assert clean_title("Everlong (Acoustic Version)") == "Everlong (Acoustic Version)"


def test_artist_from_channel_or_title() -> None:
    from app.music.youtube import _artist

    assert _artist("Foo Fighters - Topic", "Everlong") == "Foo Fighters"
    assert _artist("Linkin Park", "In The End - Linkin Park") == "Linkin Park"
    assert _artist("Kino Official", "Виктор Цой - Группа Крови") == "Виктор Цой"
    assert _artist("NA", "Everlong") is None


def test_split_title() -> None:
    from app.music.models import split_title

    assert split_title("Foo Fighters - Everlong", "Foo Fighters") == ("Everlong", "Foo Fighters")
    assert split_title("In The End - Linkin Park", "Linkin Park") == ("In The End", "Linkin Park")
    assert split_title("Кино - Кукушка", None) == ("Кукушка", "Кино")
    assert split_title("Everlong", "Foo Fighters") == ("Everlong", "Foo Fighters")
