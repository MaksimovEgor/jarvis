"""Vosk-TTS отдельным постоянным процессом (jarvis-tts, 127.0.0.1:8001).

Модель (~750 МБ) грузится ~60 с, и в процессе ядра загрузка держала GIL:
каждый перезапуск ядра (выкатка) — минута, когда ядро не отвечало вовсе.
Теперь модель живёт здесь, а ядро перезапускается за секунды:

    jarvis-core ──POST /synth {text, speaker, rate}──► этот процесс ──► wav
                  не отвечает (грузится, упал) → ядро озвучивает Piper'ом

Сервис нарочно минимальный: текст к синтезу готовит ядро (speakable, clean,
нарезка по фразам), здесь только синтез — меняется редко, перезапускается
редко (deploy.sh — только если изменился этот файл или юнит).

Порт открывается после загрузки модели: до этого ядро получает отказ в
соединении сразу, а не ждёт минуту.

Память (asus — 5,8 ГБ на всё): штатный vosk_tts.Model занимал 1,5 ГБ сразу
и дорастал до 2,1 ГБ. Модель собирается здесь сама — 0,54 ГБ при той же
скорости синтеза:

    словарь произношений  dict на миллионы строк  → sqlite на диске
    BERT (просодия)       fp32, 654 МБ            → int8, 164 МБ (если есть файл)
    арена onnxruntime     держит пиковые буферы   → выключена
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI
from pydantic import BaseModel
from starlette.responses import Response

from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jarvis.tts_server")

_synth: Any = None
# Синтез по одному: onnx и так занимает все ядра.
_lock = asyncio.Lock()


class _Dictionary:
    """Словарь произношений в sqlite рядом с исходным файлом: vosk_tts
    обращается к нему только через `in` и `[]`. Строится один раз (~1 мин)."""

    def __init__(self, source: Path) -> None:
        db = source.with_suffix(".sqlite")
        if not db.exists():
            logger.info("Строю %s (один раз)", db)
            tmp = db.with_suffix(".tmp")
            tmp.unlink(missing_ok=True)
            con = sqlite3.connect(tmp)
            con.execute("CREATE TABLE d (w TEXT PRIMARY KEY, p TEXT, prob REAL) WITHOUT ROWID")
            with open(source, encoding="utf-8") as f:
                rows = ((i[0], i[2], float(i[1])) for i in (line.split(maxsplit=2) for line in f))
                # Как в vosk_tts: из вариантов произношения слова — самый вероятный.
                con.executemany(
                    "INSERT INTO d VALUES (?, ?, ?) ON CONFLICT(w) DO UPDATE"
                    " SET p = excluded.p, prob = excluded.prob WHERE excluded.prob > d.prob",
                    rows,
                )
            con.commit()
            con.close()
            tmp.rename(db)
        self._con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, check_same_thread=False)

    def _get(self, word: str) -> str | None:
        row = self._con.execute("SELECT p FROM d WHERE w = ?", (word,)).fetchone()
        return row[0] if row else None

    def __contains__(self, word: object) -> bool:
        return isinstance(word, str) and self._get(word) is not None

    def __getitem__(self, word: str) -> str:
        phonemes = self._get(word)
        if phonemes is None:
            raise KeyError(word)
        return phonemes


class _Model:
    """То же, что vosk_tts.Model (onnx, dic, config, tokenizer, bert_onnx), но
    экономнее по памяти. Только CPU: на GTX 1050 синтез вдвое медленнее
    (0,68 от длительности звука против 0,3 на 8 ядрах), а видеокарта нужна STT."""

    def __init__(self, path: Path) -> None:
        import onnxruntime
        from tokenizers.implementations import BertWordPieceTokenizer

        opts = onnxruntime.SessionOptions()
        opts.enable_cpu_mem_arena = False

        def session(file: Path) -> Any:
            return onnxruntime.InferenceSession(str(file), sess_options=opts, providers=["CPUExecutionProvider"])

        self.onnx = session(path / "model.onnx")
        self.dic = _Dictionary(path / "dictionary")
        self.config = json.loads((path / "config.json").read_text())
        self.tokenizer = BertWordPieceTokenizer(vocab=str(path / "bert/vocab.txt"), unk_token="[UNK]", lowercase=False)
        # int8-копия делается один раз вручную (README): рантайму нужен пакет onnx.
        bert = path / "bert/model.int8.onnx"
        if not bert.exists():
            logger.warning("Нет %s — BERT в fp32 (+0,5 ГБ памяти)", bert)
            bert = path / "bert/model.onnx"
        self.bert_onnx = session(bert)


def _load() -> Any:
    from vosk_tts import Synth  # тяжёлый импорт

    return Synth(_Model(Path(settings.vosk_tts_model_path)))


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _synth
    _synth = await asyncio.to_thread(_load)
    logger.info("Vosk TTS загружен")
    yield


app = FastAPI(title="Jarvis TTS", lifespan=_lifespan)


class SynthRequest(BaseModel):
    text: str
    speaker: int = 0
    rate: float = 1.0


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/synth")
async def synth(req: SynthRequest) -> Response:
    with tempfile.NamedTemporaryFile(suffix=".wav") as out:
        async with _lock:
            await asyncio.to_thread(_synth.synth, req.text, out.name, speaker_id=req.speaker, speech_rate=req.rate)
        return Response(Path(out.name).read_bytes(), media_type="audio/wav")
