# Status: Музыкальная библиотека и «Моя волна»

- Gate 1 — Product: APPROVED 2026-09-24
- Gate 2 — Architecture: APPROVED 2026-09-24
- Gate 3 — Program Design: APPROVED 2026-09-24 (pytest разрешён)
- Gate 4 — Slice plan: APPROVED 2026-09-24

## Slices
- [x] Slice 1 — tracer bullet: ♡/👎 насквозь (оценка в памяти) — задеплоено 2026-09-24, проверено curl+SSE
- [x] Slice 2 — настоящие лайки: SQLite + liked/cache + pytest
- [x] Slice 3 — журнал прослушиваний и метрика
- [x] Slice 4 — волна v1 без LLM (YT Music)
- [x] Slice 5 — профиль Hermes dj
- [x] Slice 6 — стабильность
- [x] Slice 7 — экран «Моя музыка»
- [x] Slice 8 — dj учится
- [x] Slice 9 — доки (architecture.html, README); неделя метрики — ждёт реального пользования

## Notes for a fresh session
- Уже есть (до фичи): очередь в app/music/player.py, «похожие» через YouTube Mix (RD<id>),
  LRU-кэш data/music/cache (MUSIC_CACHE_MAX_MB), веб-плеер через SSE (useMusic.ts, PlayerBar.vue),
  Hermes зовёт плеер через MCP /mcp (app/mcp_server.py).
- Пользователь хочет: 10 ГБ под музыку, лайк/дизлайк голосом и в UI, лайкнутое хранится навсегда,
  отдельный «вкусовой» контекст рекомендаций в Hermes (время суток, эвристики), кэш/интернет вперемешку,
  стабильность уровня Алисы.
- Gate 1 одобрен как есть (волна 30/50/20, исполнитель приглушается после 2 дизлайков).
- Hermes на asus: профили = отдельные HERMES_HOME (~/.hermes/profiles/<name>), есть `coder`.
  Основной API: 127.0.0.1:8642. Мультиплексор gateway не включён — решили не трогать,
  dj — отдельный gateway на :8643. CLI hermes не в PATH по ssh (искать бинарь при настройке).
- На asus свободно 187 ГБ, data/music сейчас 312 МБ. Тестов (pytest) в репо пока нет.
- Slice 1: оценки в `player._ratings` (память) — slice 2 заменяет на library.py. `Output.set_meta()`,
  `WebOutput` state += ref/rating; `/library/rate`; router `_LIKE/_DISLIKE`; PlayerBar ♡/👎.
- Проверка на asus: тестовое устройство `web:tracer-test-0001`, `/chat/text` + SSE `/player/events`.
- Деплой: `SKIP_PIP=1 ./scripts/deploy.sh` (перезапускает jarvis-core). Коммиты — только по просьбе.

## Итог реализации (2026-09-24)
- Всё задеплоено на asus, 72 теста зелёные (`.venv/bin/pytest` на asus; локально venv нет).
- Код: app/music/{library,storage,wave,taste,library_api}.py (new), player/outputs/youtube/models/
  web_player/mcp_server/router/prompts/config/main; web: MyMusic.vue, RateToast.vue, useLibrary.ts.
- Hermes dj: ~/.hermes/profiles/dj, hermes-dj.service (user unit), API 127.0.0.1:8643,
  ставится scripts/hermes-dj/setup.sh. Основной gateway мягко перезапущен (USR1) — видит новые MCP.
- Тестовые данные (оценки, прослушивания, заметки, зёрна) удалены; память dj сброшена к
  MEMORY.seed.md (бэкап загрязнённой — /tmp/dj-memory-before-reset.md на asus). Разметка энергии
  107 треков оставлена.
- Отклонения от Gate 3: slot_at/is_weekend в models.py (не wave.py) — избегаем цикла импортов;
  YouTube Music вместо обычного YouTube для волны; энергия кандидатов размечается dj при досыпании
  (taste.energies_for); клиент шлёт held — сторож не путает удержание с зависанием.
- Известное: LLM_API_KEY ядра даёт 401 (запасной путь dj→LLM не работает); старт волны в строгом
  настроении без лайков ~9 с (цель ≤ 3 с); метрику мерить через неделю: GET /library/stats?days=7.
