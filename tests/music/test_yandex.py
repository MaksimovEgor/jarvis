import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.music import yandex
from app.music.wave import MOOD_PRESET, spec_mood


def test_ref_roundtrip() -> None:
    assert yandex.ref_of(123) == "ym-123"
    assert yandex.ref_of("123:456") == "ym-123"
    assert yandex.track_id("ym-123") == "123"


def test_decrypt_encraw() -> None:
    key = os.urandom(16)
    plain = b"fLaC" + os.urandom(1000)
    enc = Cipher(algorithms.AES(key), modes.CTR(bytes(16))).encryptor()
    data = enc.update(plain) + enc.finalize()
    assert yandex.decrypt(data, key.hex()) == plain


def test_mood_presets_roundtrip() -> None:
    for mood, spec in MOOD_PRESET.items():
        assert spec_mood(spec) == mood


async def test_download_falls_back_to_mp3_when_lossless_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def lossless(tid, folder, name):  # noqa: ANN001
        raise RuntimeError("403 sign")

    async def mp3(tid, folder, name):  # noqa: ANN001
        path = folder / f"{name}.mp3"
        path.write_bytes(b"ID3")
        return path

    monkeypatch.setattr(yandex, "_download_lossless", lossless)
    monkeypatch.setattr(yandex, "_download_mp3", mp3)
    path = await yandex.download("ym-1", tmp_path, "lossless")
    assert path.suffix == ".mp3"


async def test_feedback_errors_are_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        async def rotor_station_feedback_skip(self, *a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("down")

    monkeypatch.setattr(yandex, "_client", Broken())
    monkeypatch.setattr(yandex, "_status", yandex.Status("on"))
    await yandex.feedback(yandex.WaveCursor(MOOD_PRESET["auto"]), "skip", "ym-1", 3.0)


async def test_wave_session_parses_real_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    # Регрессия: acceptedSeeds — список словарей, волна падала на set().
    from app.music.models import WaveSpec

    async def post(path, body):  # noqa: ANN001
        return {"acceptedSeeds": [{"type": "user", "tag": "onyourwave"}], "radioSessionId": "s1",
                "batchId": "b1", "sequence": [{"track": {"id": "7", "title": "Свеча", "available": True,
                                                         "artists": [{"id": 1, "name": "Танцы Минус"}],
                                                         "albums": [{"id": 9}], "durationMs": 200000}}]}

    monkeypatch.setattr(yandex, "_post", post)
    monkeypatch.setattr(yandex, "_client", yandex.ClientAsync())
    cursor = yandex.WaveCursor(WaveSpec(mood_energy="sad"))
    tracks = await yandex.wave_tracks(cursor)
    assert [(t.ref, t.artist) for t in tracks] == [("ym-7", "Танцы Минус")]
    assert cursor.session_id == "s1" and cursor.queue == ["7:9"]


def test_seeds_only_non_default() -> None:
    from app.music.models import WaveSpec

    assert yandex.seeds(WaveSpec()) == ["user:onyourwave"]
    assert yandex.seeds(WaveSpec(station="activity:run", language="russian")) == ["activity:run", "settingLanguage:russian"]
