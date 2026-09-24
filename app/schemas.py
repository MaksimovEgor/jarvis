from __future__ import annotations

from pydantic import BaseModel


class TextChatRequest(BaseModel):
    session_id: str = "default"
    text: str
    speak: bool = False  # веб-интерфейс может попросить озвучить и текстовый ответ
    # Где играть музыку и звенеть таймерам: "asus" или "web:<id>" (браузер).
    device: str = "asus"
    # id хода от клиента — чтобы сопоставить ответ с репликой (их может быть
    # несколько одновременно).
    turn_id: str | None = None
    # Веб: не ждать ответа в запросе — он придёт событиями SSE (app/turns.py).
    detach: bool = False


class AudioMore(BaseModel):
    """Длинный ответ: остальные куски озвучки — GET /tts/chunk/{id}/{n}, n=1..count-1."""
    id: str
    count: int


class TextChatResponse(BaseModel):
    reply: str
    tool_calls: list[str] = []
    audio_base64: str | None = None
    audio_more: AudioMore | None = None
    # Ход отменила более новая реплика («стоп», «нет, включи другое»).
    cancelled: bool = False
    # detach: ход принят, ответ придёт событием.
    accepted: bool = False


class AudioChatResponse(BaseModel):
    transcript: str
    reply: str
    tool_calls: list[str] = []
    audio_base64: str | None = None
    audio_more: AudioMore | None = None
    cancelled: bool = False
    accepted: bool = False


class CancelRequest(BaseModel):
    session_id: str = "default"
    turn_id: str | None = None  # нет — отменить все просьбы сессии


class PushSubscription(BaseModel):
    """PushSubscription.toJSON() из браузера."""
    endpoint: str
    keys: dict[str, str]


class PushSubscribeRequest(BaseModel):
    device: str
    subscription: PushSubscription


class PushUnsubscribeRequest(BaseModel):
    device: str
    endpoint: str


class ClientLog(BaseModel):
    device: str
    message: str


class LocalChatRequest(BaseModel):
    """/local/chat — чат с локальной моделью мимо Hermes (Telegram: /local)."""
    session_id: str = "telegram"
    text: str
