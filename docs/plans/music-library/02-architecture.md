# Architecture: Музыкальная библиотека и «Моя волна»

## Принцип
Рекомендации — **гибрид**: быстрый детерминированный движок в ядре Jarvis решает «какой трек
следующим» за миллисекунды, а отдельный профиль Hermes **«dj»** в фоне думает о вкусе:
придумывает «зёрна» для поиска под время/настроение, размечает треки (энергия, жанр),
копит долгую память о вкусе. Волна **никогда не ждёт LLM**: dj опоздал или лёг — играем
из лайков, кэша и YouTube Mix.

```
                 ┌──────────── jarvis-core ─────────────────────────────────────┐
голос/кнопка ──► │ router (быстрые фразы) ─┐                                     │
Hermes main ─MCP►│ mcp_server ─────────────┼─► Player ◄── Wave (очередь-генератор)│
веб ── /library ►│ library_api ────────────┘     │           │   ▲               │
                 │                               ▼           ▼   │ зёрна, теги    │
                 │                         Library (SQLite) ◄─ Taste ─────────────┼─► Hermes «dj»
                 │                          ├ tracks/ratings/plays      (фоном)   │   :8643 /v1/responses
                 │                          └ Storage: liked/ + cache/ ≤ 10 ГБ    │   своя MEMORY/SOUL
                 └───────────────────────────────────────────────────────────────┘
```

## Fit
- `app/music/player.py` — `Player` получает «источник очереди»: обычный (как сейчас) или
  `Wave`, который досыпает треки, когда впереди < 3. Плеер сообщает в `Library` события
  трека: старт / дослушан / пропущен / ошибка / застрял.
- `app/music/youtube.py` — `_list` дополнительно печатает канал (для исполнителя);
  кэш перестаёт быть единственным хранилищем: `Storage` решает, где файл (liked/ или cache/)
  и чистит кэш с учётом лимита `10 ГБ − лайки`. Загрузка для волны — таймаут 25 с, не 180.
- `app/music/outputs.py` (`WebOutput`) — в SSE-снимок добавляются `ref`, `rating`,
  `origin` («волна · вечер»), `from` (cache | net | liked). Сторож: хотим играть, а позиция
  не растёт > 10 с → событие `stall` и переход на следующий.
- `app/music/web_player.py` — `/player/report` принимает `stalled`; `/player/control` —
  действия `like`, `dislike`.
- `app/mcp_server.py` — новые инструменты для основного Hermes (см. ниже).
- `app/agent/router.py` — быстрые фразы без LLM: лайк/дизлайк, «включи музыку/мою волну»,
  «включи что-нибудь бодрое», «включи мою музыку». Сейчас «включи музыку» ищет на YouTube
  слово «музыку» — это исправляется.
- `app/agent/prompts.py` — `VOICE_INSTRUCTIONS`: новые инструменты и когда звать.
- `web/` — кнопки ♡/👎 и метки в `PlayerBar`, новый экран «Моя музыка».
- Hermes на asus (вне репо): профиль `dj` + свой gateway (systemd --user), см. External.

## Endpoints
Снаружи через Caddy (basic auth), как `/player/*`:
- `GET  /library/likes?offset&limit` — лайки, новые сверху.
- `GET  /library/hidden` — дизлайкнутые треки и приглушённые исполнители.
- `POST /library/rate` `{device, ref?, value: like|dislike|none}` — без ref = текущий трек на
  устройстве; `dislike` текущего сразу переключает; `none` — снять оценку («Отменить»/«Вернуть»).
- `POST /library/play` `{device, mode: wave|liked|track, mood?, ref?}` — запуск с экрана.
- `GET  /library/storage` — занято лайками / кэшем / лимит.
- `GET  /library/stats?days=7` — метрика: доля дослушанных в волне, пропуски, дизлайки,
  зависания, доля из кэша, медиана старта.

MCP (для основного Hermes, голос сложнее быстрых фраз):
- `play_wave(mood?)` — mood: auto|energetic|calm|focus|sleep|discover.
- `rate_track(value, which=current|previous)` — «лайкни прошлую песню».
- `play_liked(query?)` — лайки, перемешанные / отфильтрованные.
- `music_taste_note(text)` — «я не люблю рэп», «утром хочу русский рок» → заметка для dj.
- `now_playing` — дополнительно «это из твоих лайков».

## Data
SQLite `data/music/library.db` (stdlib `sqlite3`, WAL, один писатель — ядро):

```
tracks   ref PK (youtube id) | title | artist | duration | energy 1-5 NULL | tags JSON NULL | added_at
ratings  ref PK → tracks     | value (+1 | -1) | at
plays    id PK | ref | device | started_at | slot (morning|day|evening|night) | weekend bool
         | origin (wave|liked|query|mix) | mood NULL | outcome (finished|skipped|disliked|error|stalled)
         | listened_s | from (cache|net|liked) | start_ms (от команды до звука)
notes    id PK | text | at                         -- «я не люблю рэп» для dj
seeds    id PK | query | bucket (similar|discover) | slot | mood | reason | at | used_count
```

Запросы:
- выбор лайков под слот: `ratings(+1) ⋈ tracks`, вес = сглаженная доля `finished` этого
  исполнителя в текущем `slot`, минус сыгранное за последние 3 ч (`plays.started_at`).
- штраф исполнителя: `COUNT(ratings -1) GROUP BY artist` (≥2 → ×0.25) и доля `skipped` за 30 дней.
- бан: `ratings.value = -1` — фильтр перед постановкой в очередь (и в миксах тоже).
- метрика: `plays WHERE origin='wave' AND started_at > now-7d GROUP BY outcome`.

Файлы (лимит `MUSIC_STORAGE_MAX_MB=10240`):
```
data/music/liked/<id>.m4a   — лайки, не вытесняются; лайк = перенос из cache/ (или докачка)
data/music/cache/<id>.m4a   — всё прочее, LRU; лимит = 10 ГБ − размер liked/
снятие лайка → файл обратно в cache/;  дизлайк → удалить из cache/
```

## Flow
**«Включи музыку» (главный путь)**
```
router «включи музыку» ─► player.play_wave(mood=auto)
  Wave.start(slot=evening, weekend=false)
    ├ сразу: 1 лайк под слот (из liked/ → звук < 1 с) или, если лайков нет, зерно dj/USER.md
    ├ фоном: Wave.fill() до 8 треков вперёд:
    │     30% лайки под слот   ─ из Library
    │     50% похожее         ─ youtube.mix(лайк-зерно) → фильтр бан/недавнее/штрафы
    │     20% новое           ─ seeds(bucket=discover) → youtube.search → фильтр
    │     правило: один исполнитель не чаще раза в 4 трека
    └ фоном: Taste.refresh_seeds(slot, mood), если зёрна слота старше 6 ч
          → POST dj /v1/responses {сводка вкуса + слот + mood + заметки} → JSON зёрен → seeds
player: трек кончился/пропущен ─► Library.record_play(outcome) ─► Wave.fill() при < 3 впереди
```

**Лайк** — `router|MCP|кнопка → Library.rate(ref,+1) → Storage.pin(ref)` (перенос cache→liked,
или докачка фоном) `→ Taste.tag(ref)` фоном (dj размечает энергию/жанр) `→ SSE rating=like`.

**Дизлайк** — `Library.rate(ref,-1) → player.next() (<1 с) → Storage.drop(ref) →
Wave.purge(artist-штраф пересчитан, из очереди убраны треки этого трека/артиста при ≥2)`.

**Сторож** — браузер шлёт позицию раз в 10 с и `stalled` по событиям `<audio>`; ядро: хотим
играть, позиция стоит > 10 с (или 3 ошибки подряд) → `plays.outcome=stalled` → следующий.
Нет интернета (поиск/микс падают) → Wave берёт только liked/ и cache/.

**Dj учится** — раз в сутки (таймер ядра, 04:00) `Taste.digest()` шлёт dj сводку за день
(лайки, дизлайки, пропуски по слотам, заметки); dj сохраняет устойчивые выводы в свою
память (`MEMORY.md` профиля). Следующие зёрна он генерирует уже с этой памятью.

## External
- **Hermes профиль `dj`** на asus: `hermes profile create dj --clone` (ключи и модель как у
  основного), свой `SOUL.md` («музыкальный куратор, отвечает только JSON»), в память
  скопирован музыкальный кусок `USER.md` основного (холодный старт: Foo Fighters, рок, радио).
  Отдельный gateway `hermes -p dj gateway` как systemd --user юнит `hermes-dj.service`,
  API на `127.0.0.1:8643` — **основной gateway и его конфиг не трогаем** (мультиплексор
  не включаем: отдельный процесс = отдельный домен падения).
- env (имена): `DJ_HERMES_URL` (http://127.0.0.1:8643), `DJ_HERMES_API_KEY`,
  `MUSIC_STORAGE_MAX_MB` (10240, заменяет смысл `MUSIC_CACHE_MAX_MB`).
- Запасной путь для dj: тот же промпт в `LLM_*` (DeepSeek), если dj-gateway недоступен;
  если и он недоступен — только эвристики.
- YouTube (yt-dlp через `YOUTUBE_PROXY`) — как сейчас; новых внешних API нет.

## Дополнение после Gate 4 (2026-09-24)
Источник музыки для волны — **YouTube Music** через тот же yt-dlp (проверено на asus):
- поиск песен `https://music.youtube.com/search?q=<q>#songs` — студийные треки, без клипов;
  в плоском режиме нет artist/duration → добираем при загрузке/разметке;
- «похожее» — радио YT Music `https://music.youtube.com/watch?v=<id>&list=RDAMVM<id>`
  (channel ≈ исполнитель);
- обычный YouTube (`ytsearch`, `RD<id>`) — запасной путь и для книг/подкастов.
