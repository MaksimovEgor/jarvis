# Architecture: Тексты песен и поиск по каталогам

## Fit
```
web: NowPlaying ─ «❝ Текст» ─► LyricsView ── GET /library/lyrics?ref ─► lyrics.py ─┬ Яндекс tracks_lyrics(LRC)
                ─ «≡ Очередь» ─► QueueView ── /player/queue*  ─► Player (очередь)   └ LRCLIB api/get → search
     шапка 🔍 ──► SearchScreen ─ GET /library/search?q&tab ─► catalog.py ─┬ Яндекс search (all | podcast)
                                ─ POST /library/play {entity}           ├ youtube.music_search + search
                                                                        └ youtube.soundcloud_search
```
- `app/music/lyrics.py` **new** — текст трека: Яндекс LRC/TEXT для `ym-…`; иначе LRCLIB
  (`/api/get` по исполнителю, названию, длительности; не нашёл — `/api/search`). Разбор LRC →
  `[(секунды, строка)]`. Кэш на диске `data/music/lyrics/<ref>.json` (и «нет текста» — на сутки).
- `app/music/catalog.py` **new** — поиск по вкладке и «сущности» для запуска:
  Яндекс: лучший результат, треки, исполнители, альбомы, плейлисты; Книги/Подкасты: Яндекс
  `podcasts` по `album.type` (audiobook/podcast), для книг запасной — YouTube audiobook; Детям:
  детские станции + Яндекс-поиск с фильтром детского (жанр children/fairy-tales у альбома).
  Кэш ответов 10 мин (Яндекс отвечал 429).
- `app/music/yandex.py` — `artist_tracks`, `album_tracks` (главы/выпуски по порядку),
  `playlist_tracks`, `lyrics`; `to_track` помечает `kind=audiobook|podcast` для не-музыки.
- `app/music/player.py` — `play_entity(entity)` (трек/исполнитель/альбом/плейлист/книга/подкаст/
  станция), `add_next(track)`, очередь: `queue_view`, `queue_move`, `queue_remove`, `play_at`.
  Книги/подкасты Яндекса — длинное: позиция сохраняется (positions.py уже работает по Track.key).
- `app/music/sources.py` — `is_song` не для книг/подкастов (их не лайкают в «Мою музыку» и не
  пишут в журнал волны); `download` для длинного Яндекса — MP3 целиком в кэш.
- `app/agent/router.py` — «покажи текст», «что он поёт» → событие экрану `ui: lyrics`;
  «включи аудиокнигу X» / «подкаст X» → Яндекс-книги/подкасты, иначе YouTube (как сейчас).
- Веб: `SearchScreen.vue`, `LyricsView.vue`, `QueueView.vue` **new**; кнопки в `NowPlaying.vue`;
  🔍 в шапке `App.vue`.

## Endpoints
- `GET  /library/lyrics?ref=` → `{synced: [{t, line}] | null, plain: str | null, source}`.
- `GET  /library/search?q=&tab=yandex|youtube|soundcloud|books|podcasts|kids` →
  `{best?, sections: [{kind, title, items: [Entity]}]}`.
- `POST /library/play` — `mode: entity` + `{entity: {source, type, id, title}}`; `mode: next` —
  поставить трек следующим.
- `GET  /player/queue?device=` → `{pos, tracks: [{ref, song, artist, cover, coverCrop}]}`;
  `POST /player/queue/move {device, from, to}`, `/player/queue/remove {device, index}`,
  `/player/queue/play {device, index}`.
- SSE: событие `{type: "ui", open: "lyrics"}` — голосом «покажи текст».

## Data
- `data/music/lyrics/<ref>.json` — `{synced, plain, source, fetched_at}`; мелкие файлы, вне 10 ГБ.
- Entity (в ответах API): `{source: yandex|youtube|soundcloud, type: track|artist|album|playlist|
  audiobook|podcast|station, id, title, subtitle, cover, coverCrop, duration?, rating?}`.
- Таблицы не меняются; прослушивания книг/подкастов в журнал волны не пишутся.

## Flow
**Текст**: NowPlaying → «Текст» → GET lyrics → синхронный: строка = последняя с `t ≤ position`
(позиция из `<audio>` на клиенте, без запросов к ядру) → автопрокрутка; тап по строке → seek(t).
**Поиск**: ввод (debounce 400 мс) → GET search(tab) → тап: трек → `play_entity` (как «включи X»,
дальше похожие); исполнитель → популярные треки исполнителя; альбом/книга → по порядку
(книга — с сохранённой позиции); подкаст → последний выпуск, дальше предыдущие; станция →
`play_wave(spec=станция)`; «⋯ → играть следующим» → `add_next`.
**Очередь**: QueueView → GET queue → перестановка/удаление → POST → SSE `upcoming` обновляется.

## External
- Яндекс: `tracks_lyrics` (подпись — в библиотеке), `search`, `artists_tracks`,
  `albums_with_tracks`, `users_playlists`; кэш 10 мин от 429.
- **LRCLIB** (lrclib.net, открытый, без ключа) — с asus напрямую, проверено: синхронный текст Vein.
- Новых зависимостей нет.
