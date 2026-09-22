from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM-оркестратор — любой OpenAI-совместимый чат-эндпоинт с tool calling.
    # DeepSeek по умолчанию; смена провайдера — это смена трёх переменных.
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"

    # Кто отвечает на реплику: "hermes" — основной профиль Hermes Agent на
    # этом же сервере (память, скиллы, Telegram), "builtin" — свой агентный цикл
    # через LLM_* выше (запасной вариант, если Hermes недоступен).
    agent_backend: Literal["hermes", "builtin"] = "builtin"
    hermes_url: str = "http://127.0.0.1:8642"
    hermes_api_key: str = ""
    hermes_timeout: float = 120.0  # агентный ход с инструментами бывает долгим

    # STT — faster-whisper локально на asus (2GB VRAM у GTX 1050 узковаты
    # для large, поэтому small/int8; CPU-фоллбек тоже тянет small в реальном
    # времени на 8 ядрах).
    stt_model_size: str = "small"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    stt_language: str = "ru"

    # TTS — Edge (нейроголоса Microsoft) через SOCKS-туннель на VPS, Piper —
    # локальный запасной вариант (и основной при TTS_ENGINE=piper).
    tts_engine: Literal["edge", "piper"] = "piper"
    edge_tts_voice: str = "ru-RU-DmitryNeural"
    edge_tts_rate: str = "+0%"
    edge_tts_pitch: str = "+0Hz"
    edge_tts_proxy: str = ""  # socks5://127.0.0.1:1080 — см. jarvis-tunnel.service

    # Голос Piper качается вручную один раз, см. README.
    tts_voice_path: str = "data/models/piper/ru_RU-voice.onnx"

    # Инструменты агента.
    searxng_url: str = "http://127.0.0.1:8080"
    music_library_dir: str = "data/music"
    mpv_socket: str = "/tmp/jarvis-mpv.sock"

    session_history_limit: int = 20


settings = Settings()
