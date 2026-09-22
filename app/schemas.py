from __future__ import annotations

from pydantic import BaseModel


class TextChatRequest(BaseModel):
    session_id: str = "default"
    text: str
    speak: bool = False  # веб-интерфейс может попросить озвучить и текстовый ответ


class TextChatResponse(BaseModel):
    reply: str
    tool_calls: list[str] = []
    audio_base64: str | None = None


class AudioChatResponse(BaseModel):
    transcript: str
    reply: str
    tool_calls: list[str] = []
    audio_base64: str | None = None
