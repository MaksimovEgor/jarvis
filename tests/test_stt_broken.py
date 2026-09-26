from pathlib import Path

from app.services import stt


def test_broken_recording_is_silence(tmp_path: Path) -> None:
    broken = tmp_path / "in.webm"
    broken.write_bytes(b"\x1aE\xdf\xa3 not really webm")
    assert stt._transcribe_sync(broken) == ""
