import os
from pathlib import Path

from app.music import storage

MB = 1024 * 1024


def _file(folder: Path, ref: str, mb: float, age: float = 0) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{ref}.m4a"
    path.write_bytes(b"x" * int(mb * MB))
    if age:
        stamp = path.stat().st_mtime - age
        os.utime(path, (stamp, stamp))
    return path


def test_pin_moves_cache_to_liked_and_prune_never_deletes_liked(music_env: Path) -> None:
    _file(storage.cache_dir(), "a", 6)
    assert storage.pin("a")
    assert storage.where("a") == "liked"
    _file(storage.cache_dir(), "b", 6)
    storage.prune()
    assert storage.where("a") == "liked"
    assert storage.where("b") == "net"


def test_prune_limit_is_total_minus_liked(music_env: Path) -> None:
    _file(storage.liked_dir(), "l", 6)
    _file(storage.cache_dir(), "old", 2, age=300)
    _file(storage.cache_dir(), "mid", 1.5, age=200)
    _file(storage.cache_dir(), "new", 1.5, age=100)
    storage.prune()
    assert storage.where("old") == "net"
    assert storage.where("mid") == "cache"
    assert storage.where("new") == "cache"


def test_pin_without_file_returns_false() -> None:
    assert storage.pin("nope") is False


def test_unpin_returns_to_cache(music_env: Path) -> None:
    _file(storage.liked_dir(), "a", 1)
    storage.unpin("a")
    assert storage.where("a") == "cache"


def test_drop_removes_cache_only(music_env: Path) -> None:
    _file(storage.cache_dir(), "c", 1)
    _file(storage.liked_dir(), "l", 1)
    storage.drop("c")
    storage.drop("l")
    assert storage.where("c") == "net"
    assert storage.where("l") == "liked"


def test_path_for_prefers_liked(music_env: Path) -> None:
    _file(storage.cache_dir(), "a", 1)
    liked = _file(storage.liked_dir(), "a", 1)
    assert storage.path_for("a") == liked


def test_usage(music_env: Path) -> None:
    _file(storage.liked_dir(), "l", 2)
    _file(storage.cache_dir(), "c", 1)
    usage = storage.usage()
    assert (usage.liked_mb, usage.cache_mb, usage.limit_mb) == (2.0, 1.0, 10)
