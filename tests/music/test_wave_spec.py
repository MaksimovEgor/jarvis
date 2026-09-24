import pytest

from app.music.models import WaveSpec
from app.music.wave_spec import parse

STATIONS = {"genre:rusrock": "Русский рок", "genre:rock": "Рок", "mood:dark": "Мрачное", "genre:jazz": "Джаз"}


def test_bare_is_default_wave() -> None:
    assert parse("музыку") == WaveSpec()


@pytest.mark.parametrize("query, station", [
    ("музыку для бега", "activity:run"),
    ("что-нибудь для сна", "activity:fall-asleep"),
    ("музыку для работы", "activity:work-background"),
    ("музыку в дорогу", "activity:road-trip"),
    ("музыку 90-х", "epoch:nineties"),
    ("что-нибудь из восьмидесятых", "epoch:eighties"),
])
def test_stations_by_activity_and_epoch(query: str, station: str) -> None:
    spec = parse(query)
    assert spec is not None and spec.station == station


def test_combined_settings() -> None:
    spec = parse("что-нибудь весёлое на английском незнакомое")
    assert spec is not None
    assert (spec.mood_energy, spec.language, spec.diversity) == ("fun", "not-russian", "discover")


def test_genre_station_by_name_only_with_catalog() -> None:
    spec = parse("русский рок", STATIONS)
    assert spec is not None and spec.station == "genre:rusrock"
    assert spec.language == "any"  # «русский» — часть названия, а не язык
    assert parse("русский рок") is None  # без Яндекса — это поиск


@pytest.mark.parametrize("query", ["Queen", "Кино группа крови", "что-нибудь из Кино", "рок музыку"])
def test_search_queries_are_not_waves(query: str) -> None:
    assert parse(query) is None
