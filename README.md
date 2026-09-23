# Jarvis — голосовой ассистент

Пилот на asus-ноутбуке (Ubuntu 24.04, headless сервер, но есть встроенные
динамик и микрофон — можно тестировать по живому звуку уже сейчас). Дальше
переезд на микрокомпьютер с внешним микрофоном/колонкой — код это не
затрагивает, меняется только источник аудио.

Переиспользует паттерн из `point/backend` (OpenAI-совместимый HTTP-клиент
для STT/LLM, `httpx.AsyncClient`, конфиг через `.env`), но не код напрямую:
там STT/LLM — self-host сервисы на арендованном GPU-боксе, здесь STT крутится
локально на asus, а LLM зовётся по облачному API.

## Архитектура

```
                    ┌─────────────────────────────────────────┐
                    │              app/main.py (FastAPI)        │
                    │  POST /chat/text    POST /chat/audio      │
                    └───────────────┬───────────────────────────┘
                                     │
                    ┌────────────────▼────────────────┐
   аудио-вход ──────▶  stt.py (faster-whisper, CPU)    │
   (пока: файл          локально на asus                │
   через HTTP;         └────────────────┬────────────────┘
   позже: живой                          │ текст
   микрофон)                             ▼
                    ┌───────────────────────────────────┐
                    │  agent/orchestrator.py              │
                    │  цикл: LLM решает — ответить текстом │
                    │  или вызвать инструмент              │
                    └───────┬───────────────┬─────────────┘
                            │               │
                 tool calls │               │ обычный текст
                            ▼               │
        ┌───────────────────────────┐       │
        │ agent/tools/               │       │
        │  • web_search → SearXNG    │       │
        │  • web_fetch  → читает URL │       │
        │  • music → mpv (YouTube/   │       │
        │    локальная библиотека/  │       │
        │    радио)                  │       │
        └───────────────┬─────────────┘       │
                         │ результат          │
                         └───────┬─────────────┘
                                 ▼
                    ┌────────────────────────┐
                    │ services/llm.py          │
                    │ DeepSeek/OpenAI-совместимый │
                    │ /chat/completions с tools   │
                    └────────────┬─────────────┘
                                 │ финальный ответ
                                 ▼
                    ┌────────────────────────┐
                    │ tts.py (Piper, CPU)      │
                    │ текст → wav локально      │
                    └────────────────────────┘
```

Инфраструктура на asus:
- **core** — сам FastAPI-процесс, нативно в venv (не в Docker — проще
  доступ к ALSA для mpv/piper, когда дойдёт до живого звука).
- **searxng** — в Docker (`docker-compose.yml`), self-host поиск, без ключей.
- **mpv** — поднимается лениво плеером (app/music/player.py), играет в реальное
  аудиоустройство ноутбука.

## Статус — что уже готово

- [x] Код агента, инструментов, STT/LLM/TTS-клиентов — написан целиком.
- [x] Код задеплоен на asus (`~/jarvis`), проверен SSH-доступ, снят
      профиль железа (8 ядер, ~5.7GB RAM, GTX 1050 2GB без драйверов,
      Docker уже стоит).
- [ ] **Системные пакеты на asus (`python3-venv`, `pip`, `ffmpeg`, `mpv`) —
      требуют sudo-пароль, которого у меня нет.** Нужно один раз выполнить
      руками (см. «Как запустить» ниже).
- [ ] Голос Piper не скачан (нужен интернет + ~60MB, качается за один curl).
- [ ] `LLM_API_KEY` не задан — нужен ключ DeepSeek (или другого провайдера).

## Как запустить (то, что осталось руками)

1. Поставить системные пакеты на сервере (спросит sudo-пароль):
   ```
   ssh asus 'bash -s' < scripts/setup_asus.sh
   ```
2. Задеплоить код и поставить python-зависимости (без sudo):
   ```
   ./scripts/deploy.sh
   ```
3. Скачать голос Piper (ru_RU) в `data/models/piper/` на сервере — модели
   лежат на HuggingFace (`rhasspy/piper-voices`), например:
   ```
   ssh asus 'cd jarvis/data/models/piper && \
     curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx && \
     curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json'
   ```
   и поправить `TTS_VOICE_PATH` в `.env` под точное имя файла.
4. Вписать ключ LLM в `~/jarvis/.env` на сервере (`LLM_API_KEY=...`,
   `LLM_BASE_URL`/`LLM_MODEL` под выбранного провайдера — по умолчанию
   DeepSeek).
5. Поднять SearXNG: `ssh asus 'cd jarvis && docker compose up -d'` (сначала
   заменить `secret_key` в `docker/searxng/settings.yml` на случайную
   строку).
6. Запустить ядро и listener как systemd --user-юниты (sudo не нужен;
   для старта при загрузке без логина нужен linger — на asus уже включён):
   ```
   ssh asus 'mkdir -p ~/.config/systemd/user && cp ~/jarvis/scripts/systemd/*.service ~/.config/systemd/user/ \
     && systemctl --user daemon-reload && systemctl --user enable --now jarvis-core jarvis-listener'
   ```
   Логи: `journalctl --user -u jarvis-listener -f` (или `-u jarvis-core`).
   Ядро слушает только `127.0.0.1:8000`.
7. Проверить текстом без микрофона: `ssh asus 'cd jarvis && .venv/bin/python scripts/test_chat.py http://127.0.0.1:8000'`.

## Музыка (app/music/)

```
Hermes ──MCP /mcp (streamable HTTP)─┐
builtin-агент (tools/music.py) ─────┼─► player.py ── единственный владелец mpv
listener ──POST /music/duck|unduck ─┘     ├ очередь: трек + YouTube Mix (RD<id>, ~20 похожих)
                                          ├ youtube.py: yt-dlp из .venv через YOUTUBE_PROXY
                                          │   → data/music/cache (LRU, MUSIC_CACHE_MAX_MB)
                                          └ radio.py: Radio Browser API (без ключа)
```

- mpv и aplay listener-а играют через **dmix** (`dmix:CARD=PCH,DEV=0`):
  `plughw` эксклюзивен, и mpv даже на паузе давал aplay «device busy».
- Wake word → `duck` (пауза), после ответа → `unduck`. Трек, включённый во
  время команды, стартует после ответа. Страховка — автоматический unduck через 200 с.
- Ссылку googlevideo mpv сам не откроет (ffmpeg без SOCKS), поэтому трек
  сначала скачивается (~3 с), следующий в очереди качается заранее.
- yt-dlp нужен JS-рантайм (иначе пропадает bestaudio): берётся node/deno из
  PATH или `~/.local/bin`.
- Hermes: `hermes mcp add jarvis --url http://127.0.0.1:8000/mcp` (уже
  сделано). Инструменты у Hermes отложенные (tool_search), поэтому их имена и
  сигнатуры перечислены в `VOICE_INSTRUCTIONS`, чтобы модель вызывала их сразу.
  После рестарта jarvis-core Hermes получает 404 на старую MCP-сессию и сам
  переподключается.
- `/mcp` и `/music/*` отвечают только локальным клиентам: запросы с
  `X-Forwarded-For` (через Caddy) получают 403.
- Голосовой разговор с Hermes начинается заново после
  `HERMES_CONVERSATION_IDLE_MINUTES` (10) минут тишины: бессрочная история
  разрослась до ~370k токенов на реплику.

## Таймеры и объявления (app/timers.py, app/services/speaker.py)

MCP-инструменты `set_timer`, `remind`, `list_timers`, `cancel_timer`, `announce`.
Срабатывание: пауза музыки → сигнал + фраза (TTS) через dmix, дважды → музыка
продолжается. Хранятся в `data/timers.json`, после рестарта ядра
восстанавливаются, пропущенные не старше часа объявляются сразу. Cron Hermes
для этого не годится: доставка задачи из api_server зависает в pending.

## Дальше (не сделано, но архитектура заложена под это)

- Живой аудиовход: сейчас `/chat/audio` принимает файл; для реального
  разговора нужен клиент с wake-word (например `openWakeWord`) + VAD,
  который стримит записанный кусок на этот же эндпоинт. Это и есть шаг
  переезда на микрокомпьютер с колонкой — серверная часть уже готова.
- Больше инструментов (умный дом, таймеры/напоминания, погода) — просто
  новый файл в `app/agent/tools/` и строчка в `agent/tools/__init__.py`.
- STT/LLM-провайдеров можно поменять только через `.env`, код трогать не
  нужно (тот же принцип, что в `point/backend`).
