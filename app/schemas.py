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


class TextChatResponse(BaseModel):
    reply: str
    tool_calls: list[str] = []
    audio_base64: str | None = None
    # Ход отменила более новая реплика («стоп», «нет, включи другое»).
    cancelled: bool = False


class AudioChatResponse(BaseModel):
    transcript: str
    reply: str
    tool_calls: list[str] = []
    audio_base64: str | None = None
    cancelled: bool = False


class CancelRequest(BaseModel):
    session_id: str = "default"
    turn_id: str | None = None  # нет — отменить все просьбы сессии


class ClientLog(BaseModel):
    device: str
    message: str
