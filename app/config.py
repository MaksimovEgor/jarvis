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
    # Столько минут тишины — и голосовой разговор начинается с чистой истории.
    hermes_conversation_idle_minutes: int = 10
    # Дольше этого ход не держит основной план: уходит в фон (app/turns.py).
    foreground_budget_seconds: float = 12.0
    # Контакт в подписи VAPID (Apple требует mailto: или https:) — app/services/webpush.py.
    push_contact: str = "https://point.abrdns.com"
    # Отсюда берутся токен бота и чат для «пришли мне в Telegram».
    hermes_env_file: str = "~/.hermes/.env"

    # STT локально на asus (app/services/stt.py). gigaam — основной (точнее
    # на русском), whisper — откат на faster-whisper.
    stt_engine: Literal["gigaam", "whisper"] = "gigaam"
    stt_device: str = "cpu"
    gigaam_model: str = "gigaam-v3-e2e-rnnt"
    gigaam_model_path: str = "data/models/gigaam-v3"
    # Только для whisper (2GB VRAM у GTX 1050 узковаты для large).
    stt_model_size: str = "small"
    stt_compute_type: str = "int8"
    stt_language: str = "ru"
    # Расшифровки длинного аудио (app/services/transcribe.py) — кэш текстов.
    transcripts_dir: str = "data/transcripts"

    # TTS — Vosk (нейроголос локально на asus, основной), Edge (Microsoft через
    # SOCKS-туннель — нестабилен), Piper — запасной, всегда локально.
    tts_engine: Literal["vosk", "edge", "piper"] = "piper"
    vosk_tts_model_path: str = "data/models/vosk-tts/vosk-model-tts-ru-0.9-multi"
    # Постоянный процесс с моделью (app/tts_server.py, jarvis-tts.service).
    vosk_tts_url: str = "http://127.0.0.1:8001"
    vosk_tts_speaker: int = 0
    vosk_tts_rate: float = 1.0  # speech_rate модели: >1 — быстрее
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

    # Музыка. Играем через dmix, а не plughw: plughw эксклюзивен, и mpv даже
    # на паузе не дал бы listener-у проиграть ответ (aplay → «device busy»).
    audio_output_device: str = "dmix:CARD=PCH,DEV=0"
    music_volume: int = 60
    # YouTube с asus напрямую не открывается — и поиск, и скачивание идут
    # через SOCKS-туннель (тот же, что у Edge TTS).
    youtube_proxy: str = ""
    # Всё хранилище музыки: лайки (liked, не вытесняются) + кэш (LRU,
    # занимает то, что осталось от лимита) — app/music/storage.py.
    music_cache_dir: str = "data/music/cache"
    music_liked_dir: str = "data/music/liked"
    music_storage_max_mb: int = 10240
    # Оценки, прослушивания, зёрна рекомендаций — app/music/library.py.
    music_library_db: str = "data/music/library.db"
    # Профиль Hermes «dj» — музыкальный вкус и рекомендации (app/music/taste.py).
    # Недоступен — тот же запрос уходит в LLM_*, а волна играет и без него.
    # С Hermes 0.21.5 один gateway на хост обслуживает все профили: dj — по префиксу /p/dj
    # на основном API (:8642) со своим ключом (API_SERVER_KEY из ~/.hermes/profiles/dj/.env).
    dj_hermes_url: str = "http://127.0.0.1:8642/p/dj"
    dj_hermes_api_key: str = ""
    # Яндекс Музыка (app/music/yandex.py): токен появляется после входа кодом
    # устройства из «Моей музыки», поэтому в файле, а не в .env.
    yandex_token_file: str = "data/yandex_token.json"
    radio_browser_url: str = "https://de1.api.radio-browser.info"
    # Адрес самого ядра для mpv: длинное с YouTube он берёт потоком из /media/yt.
    core_url: str = "http://127.0.0.1:8000"

    session_history_limit: int = 20

    # Резервная локальная LLM (Ollama, jarvis-llm.service) — app/services/llm.py.
    # jarvis-local — Qwen2.5-3B-Instruct IQ3_XS, контекст 8k, num_gpu 99: целиком
    # в 2 ГБ GTX 1050 (1,6 ГБ), ~19 токенов/с, инструменты зовёт (Modelfile, README).
    local_llm_url: str = "http://127.0.0.1:11434/v1"
    local_llm_model: str = "jarvis-local"
    # auto — облако, при отказе локальная; local — сразу локальная, без Hermes.
    # Меняется на лету голосом («переключись на локальную модель»), не из .env.
    llm_mode: Literal["auto", "local"] = "auto"

    # Вход в веб снаружи (app/web_auth.py): bcrypt-хэш пароля (тот, что был в
    # basic auth Caddy). Пусто — без входа (локальная разработка).
    web_auth_user: str = "egor"
    web_auth_hash: str = ""
    web_session_days: int = 365


settings = Settings()
