# Architecture: Яндекс Музыка как основной источник

## Принцип
Плеер, волна, библиотека и хранилище остаются как есть — появляется слой **источников**.
Сейчас код в семи местах спрашивает `source == "youtube"`; это заменяется одним фасадом
`sources.py`, за которым Яндекс, YouTube (Music) и SoundCloud.

```
player / wave / outputs / media
          │  resolve(query) · download(track) · is_song(track) · feedback(...)
          ▼
   app/music/sources.py  ── фасад: порядок источников, «та ли песня», единый download
     ├ yandex.py      ── yandex-music (async): поиск, волна rotor + фидбек, лайки, MP3 320 / FLAC
     ├ youtube.py     ── как сейчас: YT Music поиск/радио, YouTube, длинное потоком
     └ soundcloud     ── yt-dlp scsearch (в youtube.py: тот же yt-dlp и прокси)
```

## Fit
- `models.py` — `Source += "yandex" | "soundcloud"`; `Track.url` (ссылка страницы для
  SoundCloud), `Track.quality` не нужен — качество узнаём у файла (ffprobe, уже есть).
- `yandex.py` **new** — клиент `ClientAsync` (pip `yandex-music` 3.x), токен в
  `data/yandex_token.json` (600), вход кодом устройства; поиск; `rotor_station_tracks`
  `user:onyourwave` + настройки настроения + фидбек; лайки/дизлайки; скачивание:
  lossless (`get-file-info`, подпись HMAC, при `transport=encraw` расшифровка AES-CTR,
  `flac-mp4` → ремукс ffmpeg в `.flac`) → иначе `download_info` MP3 320.
- `sources.py` **new** — `resolve(query, kind)`: Яндекс → YT Music → YouTube → SoundCloud с
  оценкой совпадения; `download(track, quality)`; `is_song(track)`; `service_label`.
- `youtube.py` — + `soundcloud_search` (yt-dlp `scsearch`), скачивание по `track.url`.
- `storage.py` — файлы по ref (`ym-<id>.flac|.mp3`, `sc-<id>.mp3`, YouTube как было);
  `pin(ref)` для Яндекса = «докачать FLAC в liked/, MP3 из cache/ удалить».
- `player.py` — `play_youtube` → `play_query` через `sources.resolve`; `_journaled` →
  `sources.is_song`; лайк/дизлайк Яндекс-трека дублируется в Яндекс; фидбек волне Яндекса
  (старт/дослушал/пропуск) из `_open_play/_close_play`.
- `wave.py` — новая корзина **yandex**: треки «Моей волны» Яндекса с настройками под
  настроение; при подключённом Яндексе доли: yandex 50 / liked 25 / similar (YT) 15 /
  discover (dj) 10. Без Яндекса — как сейчас. Бан/недавнее/энергия/ARTIST_GAP — те же.
- `library.py` — колонка `tracks.url`; импорт лайков Яндекса (`sync_yandex_likes`) при
  подключении и раз в сутки; `stats` по источникам.
- `outputs.py` / `media.py` — `/media/yt/<id>` → общий `/media/file/<ref>` для всего, что
  лежит на диске; для youtube-длинного поток как был.
- `library_api.py` — эндпоинты подключения Яндекса.
- Веб: карточка подключения в «Моей музыке», бейджи источника/качества уже есть
  (`service`, `codec`, `bitrate`) — добавить «16 бит / 44,1 кГц» для FLAC и «нет в Яндексе».

## Endpoints
- `POST /library/yandex/connect` → `{code, url, expiresIn}` — запрос кода устройства;
  ядро само опрашивает Яндекс в фоне до подтверждения.
- `GET  /library/yandex/status` → `{state: off|pending|on|broken, account, plus, code?}`.
- `POST /library/yandex/disconnect` — удалить токен.
- `GET  /media/file/{ref}` — файл трека с диска (любой источник), с Range; заменяет
  YouTube-частный путь для коротких треков (`/media/yt` остаётся для длинного потока).
- `GET /library/stats` — + разбивка по источникам (доля Яндекса).

## Data
- `tracks`: + `url TEXT` (SoundCloud), `ALTER TABLE ... ADD COLUMN` при старте, если нет.
- `ratings` — без изменений; ref Яндекса `ym-<id>`, SoundCloud `sc-<id>`, YouTube — id видео.
- `data/yandex_token.json` — `{token, uid, login, plus, connected_at}`, права 600.
- Файлы: `liked/ym-123.flac`, `cache/ym-123.mp3`, `cache/sc-456.mp3`.
- Запросы: доля источников `SELECT substr(ref,1,3), outcome, COUNT(*) FROM plays ...`.

## Flow
**«Включи Кино Группа крови»**
```
router/MCP → player.play_query(q) → sources.resolve(q):
   yandex.search(q) → лучший трек, score ≥ 0.8 (исполнитель+название) → Track(yandex)
   иначе youtube.music_search → youtube.search → soundcloud_search (score по тому же правилу)
→ _start_queue([track]) → output.load → sources.download(track, quality="mp3")
   (liked FLAC на диске? играет он) → /media/file/ym-123
→ дальше: Яндекс-трек → «похожие» из rotor track:<id>; YouTube → Mix как сейчас
```
**Волна (Яндекс подключён)**
```
play_wave(mood) → Wave.more: yandex.wave(mood, last feedback) ─┐
                            liked (лайки Джарвиса + Яндекса)   ├─ compose() (как сейчас) → очередь
                            YT Music радио / dj discover      ─┘
_open_play  → yandex.feedback trackStarted (если трек из волны Яндекса)
_close_play → trackFinished | skip (с секундами прослушивания)
```
**Лайк**: `rate(+1)` → library + (Яндекс-трек) `users_likes_tracks_add` + фон: скачать FLAC
в liked/ (не вышло — MP3 320 в liked/) → удалить MP3 из cache/.
**Дизлайк**: library + `users_dislikes_tracks_add` + фидбек skip волне + как сейчас.
**Подключение**: connect → код → фон `poll_device_token` → токен в файл → `account_status`
(логин, Плюс) → `sync_yandex_likes()`.

## External
- pip `yandex-music` (3.0.0, MarshalX; тянет `requests[socks]`) — **новая зависимость**.
- API `api.music.yandex.net` — с asus напрямую (без прокси), проверено.
- Lossless: `get-file-info` с подписью HMAC-SHA256 ключом из клиента Яндекса (обратная
  разработка, может сломаться → фолбэк MP3 320). AES-CTR — `cryptography` (уже в зависимостях).
- `ffmpeg` на asus — ремукс `flac-mp4` → `.flac` (Safari на iOS играет FLAC).
- SoundCloud — yt-dlp `scsearch` через `YOUTUBE_PROXY` (проверить доступность в слайсе).
- env: `YANDEX_TOKEN_FILE` (по умолчанию `data/yandex_token.json`), `YANDEX_QUALITY_LIKED`
  (`lossless`), `YANDEX_QUALITY_CACHE` (`mp3`).
