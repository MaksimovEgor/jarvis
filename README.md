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

Полная схема работы (кто что делает от «Джарвис» до ответа, все ветки и
ошибки) — `docs/architecture.html`, открыть в браузере.

```
телефон / Mac (web/, PWA)                         asus (сервер, звука нет)
  «Джарвис» — Web Speech API ─┐
  запись — MediaRecorder ─────┼─ POST /chat/* (detach) ─► jarvis-core (app/main.py)
  плеер <audio> ◄─ SSE /player/events ◄─┐                 ├ stt.py  faster-whisper (GPU)
  голос ◄─ GET /tts/chunk/<id>/<n> ◄────┤                 ├ router.py  быстрые команды
                                        │                 ├ Hermes (/v1/responses) ─MCP /mcp─┐
                                        │                 │   или builtin: orchestrator.py    │
                                        └─ события turn ──┤ player.py ◄───────────────────────┘
                                                          └ speech.py → tts.py → jarvis-tts (Vosk)
```

Инфраструктура на asus:
- **core** — сам FastAPI-процесс, нативно в venv (jarvis-core, 127.0.0.1:8000).
- **jarvis-tts** — процесс с моделью Vosk (127.0.0.1:8001).
- **searxng** — в Docker (`docker-compose.yml`), self-host поиск, без ключей.
- **mpv** — только для выключенного listener: веб-устройства играют сами.

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
                                          │   → data/music/cache + liked (MUSIC_STORAGE_MAX_MB)
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

## Устройства: музыка звучит там, где попросили

asus — только сервер: ничего не слушает и не воспроизводит (jarvis-listener
выключен, `systemctl --user disable jarvis-listener`; код оставлен для
будущей колонки на микрокомпьютере). Нет активной реплики (Telegram, cron) —
звук на последнем браузере, где был Джарвис; таймер без открытого экрана —
пуш, без подписки — сообщение в Telegram.

```
веб (телефон/Mac) ─ /chat/* device=web:<id> ─┐
listener (asus) ── /chat/audio (device=asus) ─┼─► devices.turn(): «чей ход»
Telegram/cron Hermes ── хода Jarvis нет ──────┘        │
Hermes ─MCP─► devices.current_player() ◄───────────────┘
   asus     → Player(MpvOutput)  — mpv; короткое из кэша, длинное потоком /media/yt
   web:<id> → Player(WebOutput)  — SSE /player/events → <audio> в браузере,
                                   браузер шлёт /player/report (позиция, конец трека)
```

## Моя волна, лайки, профиль dj

Полное описание — `docs/architecture.html#wave`, план и решения — `docs/plans/music-library/`.

```
«включи музыку» ─► Wave (app/music/wave.py) — решает мгновенно, без LLM
                    ├ лайки + радио YouTube Music + поиск по зёрнам dj
                    └ compose(): время суток, настроение, баны, штрафы
                 ◄─ taste.py (фоном) ◄─► Hermes профиль «dj» :8643 (своя память)
лайк/дизлайк ─► library.db (SQLite) + storage: liked/ навсегда, cache/ LRU, всего 10 ГБ
```

- Профиль dj ставится один раз: `ssh asus 'bash -s' < scripts/hermes-dj/setup.sh`
  (код уже должен быть выкачен). Скрипт впишет `DJ_HERMES_API_KEY` в `.env` ядра.
- Метрика качества: `curl http://127.0.0.1:8000/library/stats?days=7` — доля
  дослушанных треков волны (цель ≥ 0.7) и число зависаний (цель 0).
- Тесты: `.venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest`.

- `/media/yt/<id>` — из кэша или потоком googlevideo через туннель с Range
  (Safari без Range не играет и не перематывает). `/media/ref/<token>` —
  http-радио, локальные файлы, объявления.
- Книги/подкасты (`kind`, или трек длиннее 20 минут) продолжаются с места
  остановки минус 10 с, позиции общие для всех устройств: `data/positions.json`.
- Веб сам ставит музыку на паузу от начала записи до конца озвученного ответа
  и на время объявления таймера. На iPhone первый `play()` разблокируется
  тапом, громкость меняется только кнопками, в фоне Safari рвёт SSE —
  таймер в этом случае приходит пушем (без подписки — в Telegram).
- Длинное с YouTube Safari получает как HLS (`/media/hls/<id>/index.m3u8`):
  DASH-m4a, который отдаёт YouTube, iPhone не играет (код 4). Короткое для веба
  сначала скачивается в кэш: исправленный yt-dlp файл играет везде. Запросы
  к googlevideo — с `rdns=True` и `URL(..., encoded=True)`, иначе 403.
- «Джарвис» при открытой вкладке — Web Speech API браузера (на iPhone
  распознавание Apple), `web/src/composables/useWakeWord.ts`. «Джарвис, включи
  …» одной фразой → команда текстом; просто «Джарвис» → сигнал и запись с
  автостопом по тишине (`useRecorder`). Слушает, только пока Джарвис свободен.
- `index.html` отдаётся с `Cache-Control: no-cache`, иначе Safari держит
  старую версию страницы.

## Веб-интерфейсы снаружи (без Tailscale)

```
iPhone/Mac ─https─► Caddy на point ─► 127.0.0.1:<порт> ─ssh -R (jarvis-tunnel)─► asus
  :8446 Jarvis  (вход — app/web_auth.py)   18000 ─────────────────────► 127.0.0.1:8000
  :8447 Hermes  (логин самого Hermes)      19119 ─────────────────────► 127.0.0.1:9119
```

- Вход в Jarvis — страница `/login` самого ядра и cookie `jarvis_auth` на год
  (продлевается при заходах). Пароль — bcrypt-хэш в `.env` (`WEB_AUTH_HASH`,
  тот же, что раньше был в basic auth Caddy). Basic auth в Caddy убран:
  PWA «На экран Домой» его окно не показывала — был чёрный экран.
- Дашборд Hermes слушает только loopback: drop-in
  `scripts/systemd/hermes-dashboard.service.d/public.conf` (копируется в
  `~/.config/systemd/user/hermes-dashboard.service.d/`). В `~/.hermes/config.yaml`
  стоит `dashboard.public_url: https://point.abrdns.com:8447`: без него Hermes
  отклоняет чужой Host, а с ним вход обязателен (`dashboard.basic_auth`).
- «На экран Домой» на iPhone: iOS не берёт SVG, нужны PNG и манифест. У Jarvis они
  лежат в `web/public/`, и Caddy отдаёт их без пароля. В Hermes теги дописывает
  `scripts/hermes-pwa/build_dist.sh` (ExecStartPre): он копирует `web_dist` в
  `~/.hermes/web_dist_pwa`, и дашборд работает из копии (`HERMES_WEB_DIST`).
  `hermes update` пересобирает оригинал и перезапускает дашборд, после чего копия
  создаётся заново.

## Голос (app/services/tts.py)

- Основной — Vosk-TTS локально на asus (`TTS_ENGINE=vosk`, модель
  `data/models/vosk-tts/vosk-model-tts-ru-0.9-multi`, ~900 МБ). Голоса:
  0–2 женские, 3–4 мужские (`VOSK_TTS_SPEAKER`). Модель держит отдельный
  процесс `jarvis-tts` (грузится ~60–70 с с холодного диска, пока — Piper),
  синтез ~0,3 от длительности звука. Журнал: `journalctl --user -u jarvis-tts`.
  Качать модель только через прокси: напрямую alphacephei.com отдаёт ~1 КБ/с.
- Edge (Microsoft) оставлен как вариант, но через туннель нестабилен
  (NoAudioReceived, таймауты). Запасной всегда — Piper.
- Перед синтезом текст чистится от эмодзи и markdown, длинный режется по фразам.
- Длинный ответ озвучивается потоком (`app/services/speech.py`): первый кусок
  (~100 символов) приходит в ответе `/chat/*`, остальные клиент (веб и
  listener) забирает `GET /tts/chunk/<id>/<n>`. Каждый кусок не больше чем
  вдвое длиннее предыдущего — успевает синтезироваться, пока тот звучит.
- Образцы голосов: `/voice-samples/` (лежат только на asus в web/dist).

## Быстрые команды, история, Telegram

- `app/agent/router.py` — «включи X / включи радио X / пауза / дальше /
  громче / что играет / таймер на N» выполняются без Hermes (0,1–4 с).
  Не подошло под правила или ничего не нашлось — в Hermes.
- Hermes выполняет ходы одного разговора по очереди: если ход уже идёт,
  новая реплика уходит в параллельный разговор (`services/hermes.py`).
- История для экрана — `data/chat/<session>.jsonl`, `GET /chat/history`.
- Прогресс просьб (какой инструмент работает) — событие `turn` в SSE плеера;
  ✕ в чате — `POST /chat/cancel {turn_id}`.
- «Пришли в Telegram» — MCP `send_telegram` (`services/telegram.py`): у
  api_server Hermes нет send_message, он слал через одноразовый cron.
- HLS для Safari выбирается по протоколу, а не по itag: у многоязычных видео
  форматы `234-0/234-1`; запасной — HLS с видео.

## Параллельные просьбы и отмена (app/turns.py, app/agent/conflicts.py)

```
«Джарвис»/кнопка — в любой момент (думает / говорит → голос замолкает)
новая реплика ─► сразу в работу ─┐
                 диспетчер ──────┴► какие выполняющиеся она отменяет:
                   «стоп/хватит/передумал» → все; «включи…» → прошлые «включи…»;
                   с LLM_API_KEY — DeepSeek понимает «нет, другую» и т.п.
отмена = cancel задачи → закрыт поток к Hermes (stream=true) → Hermes прерывает агента
```

- Ход включает и синтез речи — «стоп» отменяет и долгую озвучку.
- Реплика целиком «стоп/хватит/отмена/замолчи» обрабатывается ядром без
  Hermes (~0,7 с): отменить всё, музыку на паузу, «Хорошо.».
- Оборвался запрос клиента (iPhone свернул вкладку) — ход продолжается.
- Веб не ждёт ответа в POST (`detach`): ядро сразу отвечает «принято», а
  расшифровка, прогресс, ответ, ошибка и отмена приходят событиями `turn` по
  SSE плеера. События пронумерованы (`<запуск ядра>:<n>`): после обрыва SSE
  ядро досылает пропущенное (`since` / `Last-Event-ID`) и событием `turns`
  сообщает, какие ходы ещё идут. Принятый ход пропал — ядро перезапускалось,
  экран так и говорит. Озвучка — только по id (`/tts/chunk/<id>/0…`), в
  событиях нет base64. Listener asus по-прежнему ждёт ответ синхронно.
- Основной план и фон: ход, не уложившийся в `FOREGROUND_BUDGET_SECONDS`
  (12 с), или с «в фоне / потом расскажи / не спеши» уходит в фон. Сразу
  звучит «Скажу, когда будет готово», «думаю» и музыка отпускаются, задача
  видна полоской под шапкой (✕ — отменить). Готово — сигнал и ответ голосом
  (веб) или объявление через `speaker` (listener asus → пуш/Telegram). Фоновые ходы не отменяются
  «стопом» и новыми репликами — только ✕.

## Выкатка и перезапуск

- `./scripts/deploy.sh` (с Mac, из корня репозитория): сборка фронта, rsync,
  юниты, pip (`SKIP_PIP=1` — без него), перезапуск ядра. rsync не трогает
  `.env`, `data/chat`, `data/*.json`, `web/dist/voice-samples` и модели.
- Перезапуск ядра ждёт, пока оно доделает ходы: `ExecStop` →
  `scripts/wait_idle.sh` опрашивает `GET /admin/busy` (до 120 с).
  `--timeout-graceful-shutdown 5` — открытые SSE браузеров иначе держали
  остановку 90 с до SIGKILL.
- Hermes отвечает 503 (`gateway_draining`), пока перезагружается: запрос,
  который он не начал выполнять (503/502/504, нет соединения), повторяется
  через 2, 4, 8 с. Оборвавшийся поток не повторяется — инструменты могли сработать.
- Ход всё-таки потерялся (ядро упало) — экран ищет его ответ в истории по
  `turn_id`.
- Модель Vosk живёт в отдельном процессе `jarvis-tts` (127.0.0.1:8001,
  `app/tts_server.py`): её загрузка держала GIL, и ядро после старта минуту не
  отвечало. `deploy.sh` перезапускает jarvis-tts, только если изменился его код
  или юнит.

## Уведомления (Web Push, app/services/webpush.py)

```
фоновая задача готова/упала ─┐  вкладка свёрнута (visible=false в /player/report)
таймер/напоминание (speaker) ─┴─► или SSE нет ─► VAPID + aes128gcm ─► Apple/FCM ─► sw.js
```

- Включаются кнопкой «Уведомления» в шапке. На iPhone — только у PWA с
  экрана «Домой» (iOS 16.4+), в обычном Safari кнопки нет.
- Открытый экран сам покажет и озвучит ответ — пуш не приходит.
- Ключи VAPID — `data/vapid.pem` (создаётся сам, права 600; пересоздашь — все
  подписки оформлять заново), подписки — `data/push.json`. Шифрование и
  подпись — на `cryptography`/PyJWT, сверено с тестовым вектором RFC 8291.
- Caddy отдаёт `/sw.js` без basic auth, как манифест и иконки.
- Веб: ответы озвучиваются по очереди и только когда пользователь не говорит;
  отменённые реплики в ленте помечены «отменено».

## Таймеры и объявления (app/timers.py, app/services/speaker.py)

MCP-инструменты `set_timer`, `remind`, `list_timers`, `cancel_timer`, `announce`.
Срабатывание: экран открыт — браузеру событие `announce` (сигнал + фраза
дважды одним wav, музыку браузер приглушает сам); свёрнут — пуш; без подписки
или устройство неизвестно — Telegram. Хранятся в `data/timers.json`, после рестарта ядра
восстанавливаются, пропущенные не старше часа объявляются сразу. Cron Hermes
для этого не годится: доставка задачи из api_server зависает в pending.

## Дальше

- Колонка на микрокомпьютере: `app/listener.py` (openWakeWord + VAD →
  `/chat/audio`) готов, но сейчас выключен. Перед включением — вернуть звук
  на устройство `asus` (сейчас его ходы играют на последнем браузере, а
  `/music/duck` приглушает mpv, который ничего не играет).
- STT/LLM-провайдеров можно поменять только через `.env`, код трогать не
  нужно (тот же принцип, что в `point/backend`).
