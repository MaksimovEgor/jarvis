# Program Design: Яндекс Музыка как основной источник

## Files
| Файл | Что и почему |
|---|---|
| `app/music/yandex.py` **new** | Всё про Яндекс: токен, вход кодом, клиент, поиск, волна rotor + фидбек, лайки, скачивание (FLAC → MP3 320). Один модуль — одна внешняя система, как `youtube.py`. |
| `app/music/sources.py` **new** | Фасад источников: `resolve`, `download`, `is_song`, `similar`, оценка совпадения. Убирает `source == "youtube"` из плеера/выходов. |
| `app/music/models.py` | `Source += yandex, soundcloud`; `Track.url`; `Track.service += yandex, soundcloud`. |
| `app/music/youtube.py` | `soundcloud_search`; `download` берёт `track.url`, если есть (SoundCloud). |
| `app/music/storage.py` | `pin(ref)` без изменения API; добавлено `replace_in_liked(ref, path)` для FLAC. |
| `app/music/player.py` | `play_query` (бывш. `play_youtube` — имя остаётся алиасом), `_journaled → sources.is_song`, фидбек Яндексу, лайки в Яндекс, похожие через `sources.similar`. |
| `app/music/wave.py` | Корзина `yandex`, доли `BUCKETS_YANDEX`; `Wave.more` берёт `yandex.wave_tracks`. |
| `app/music/library.py` | `tracks.url` (миграция), `import_likes(tracks)`, `stats().by_source`. |
| `app/music/outputs.py` | `_src/_target/prefetch` через `sources.download`; `src = media/file/<ref>`. |
| `app/music/media.py` | `GET /media/file/{ref}` (Range через FileResponse). |
| `app/music/library_api.py` | `/library/yandex/{connect,status,disconnect}`. |
| `app/main.py` | lifespan: `yandex.start()` (загрузка токена), суточная синхронизация лайков. |
| `app/agent/router.py`, `app/mcp_server.py` | `play_youtube` → `play_query` (поведение то же, источник — любой). |
| `web/src/components/YandexCard.vue` **new** | Карточка подключения в «Моей музыке» (состояния off / код / on / broken). |
| `web/src/components/MyMusic.vue`, `NowPlaying.vue`, `api.ts`, `types.ts` | Карточка; бейдж «FLAC · 16 бит / 44,1 кГц», «нет в Яндексе». |
| `requirements.txt` | `yandex-music>=3,<4`. |
| `tests/music/test_sources.py`, `test_yandex.py` **new** | см. Test plan. |

## Types & signatures
```python
# models.py
Source = Literal["youtube", "radio", "local", "yandex", "soundcloud"]
Service = Literal["youtube", "ytmusic", "yandex", "soundcloud"]
@dataclass(frozen=True)
class Track:
    ...                                  # как было
    service: Service = "youtube"
    url: str | None = None               # страница трека (SoundCloud) — для yt-dlp

# yandex.py
YM_PREFIX = "ym-"
Quality = Literal["lossless", "mp3"]
State = Literal["off", "pending", "on", "broken"]

@dataclass(frozen=True)
class Status:
    state: State; login: str | None; plus: bool; code: str | None; url: str | None; expires_at: float | None

@dataclass(frozen=True)
class WaveBatch:
    tracks: list[Track]; batch_id: str

def ref_of(track_id: int | str) -> str: ...            # 123 → "ym-123"
def track_id(ref: str) -> str: ...                      # "ym-123" → "123"
def is_on() -> bool: ...
def status() -> Status: ...
async def start() -> None: ...                          # lifespan: токен из файла, account_status
async def connect() -> Status: ...                      # код устройства + фоновый опрос
async def disconnect() -> None: ...
async def search(query: str, limit: int = 5) -> list[Track]: ...
async def wave_tracks(mood: Mood, queue_after: str | None = None) -> WaveBatch: ...
async def similar(ref: str) -> list[Track]: ...         # rotor track:<id>
async def feedback(kind: Literal["started", "finished", "skip"], ref: str,
                   played: float = 0, batch_id: str | None = None) -> None: ...
async def set_like(ref: str, value: Rating | None) -> None: ...
async def liked_tracks() -> list[Track]: ...
async def download(track: Track, quality: Quality, dest: Path) -> Path: ...
    # lossless: get-file-info (подпись) → url, codec, key → качаем → encraw? AES-CTR →
    #           flac-mp4? ffmpeg -c copy → .flac ; ошибка → mp3 (download_info, 320)

# sources.py
MATCH_THRESHOLD = 0.8
def match_score(query: str, track: Track) -> float: ...   # нормализация, токены исполнителя+названия, штраф cover/live/remix, если их нет в запросе
async def resolve(query: str, kind: Kind = "music") -> list[Track]: ...  # кандидаты, лучший первым
async def download(track: Track, liked: bool = False, timeout: float = 180) -> Path: ...
def is_song(track: Track) -> bool: ...                   # yandex | soundcloud | youtube короткое
async def similar(track: Track) -> list[Track]: ...      # yandex → rotor, youtube → Mix
SERVICE_LABEL: dict[Service, str]                        # «Яндекс Музыка», «YouTube Music», …

# wave.py
Bucket = Literal["yandex", "liked", "similar", "discover"]
BUCKETS_YANDEX: dict[Mood, tuple[float, float, float, float]]   # auto=(.5,.25,.15,.10)
MOOD_TO_ROTOR: dict[Mood, tuple[str, str]]   # (moodEnergy, diversity): energetic=(active,default), calm=(calm,default), sleep=(calm,favorite), focus=(calm,default), discover=(all,discover), auto=(all,default)

# library.py
def import_likes(self, tracks: list[Track]) -> int: ...       # upsert + rating=+1, не трогая дизлайки
@dataclass(frozen=True)
class Stats: ...; by_source: dict[str, dict[str, int]]         # ym/yt/sc → {finished, skipped, ...}
```
```ts
// types.ts
export interface YandexStatus { state: 'off' | 'pending' | 'on' | 'broken'; login: string | null; plus: boolean; code: string | null; url: string | null; expiresAt: number | null }
// api.ts
export async function yandexStatus(): Promise<YandexStatus>
export async function yandexConnect(): Promise<YandexStatus>
export async function yandexDisconnect(): Promise<void>
```

## Call stack
**«Включи Кино Группа крови»**
```
router._try_fast → player.play_query(q)
  sources.resolve(q): gather(yandex.search(q), youtube.music_search(q))   ← параллельно, быстрее
     ym-кандидат с score ≥ 0.8 → первым; иначе лучший из YT; иначе youtube.search; иначе soundcloud
  player._start_queue(candidates[:3])
     output.load(track) → WebOutput._src → sources.download(track) → media/file/<ref>
  фон: sources.similar(track) → очередь (Яндекс: rotor track:<id>; YT: Mix)
```
**Волна**
```
player.play_wave(mood) → Wave.first (лайк с диска) | Wave.more(tag=False)
Wave.more: yandex.wave_tracks(mood, queue_after=последний ym-трек)  [bucket yandex]
           + liked (library, в т.ч. импортированные из Яндекса)       [bucket liked]
           + youtube.music_radio / dj discover                         [similar/discover]
           → compose(BUCKETS_YANDEX[mood])
player._open_play(track) → track.origin=wave & service=yandex → yandex.feedback("started", batch)
player._close_play(outcome) → finished | skip(played) → yandex.feedback
```
**Лайк**: `player.rate(+1)` → library → `yandex.set_like` (фон) → `sources.download(track, liked=True)`
(FLAC → liked/, MP3 из cache/ удалить; FLAC не вышел → MP3 320 → pin).
**Подключение**: `POST /library/yandex/connect` → `yandex.connect` → `request_device_code` →
фон `poll_device_token` каждые `interval` с → токен в файл → `account_status` → `import_likes`.

## Test plan
`tests/music/test_sources.py`
- `test_match_score_exact_artist_title` — «кино группа крови» vs Кино / Группа крови ≥ 0.9.
- `test_match_score_penalizes_cover_live_remix` — «Кукушка (cover)» < 0.8, если в запросе нет cover.
- `test_match_score_keeps_live_when_asked` — «кино кукушка live» vs «Кукушка (Live)» ≥ 0.8.
- `test_resolve_prefers_yandex_when_good` / `test_resolve_falls_back_to_youtube_when_poor` /
  `test_resolve_soundcloud_last` (моки источников).
- `test_resolve_works_when_yandex_off` — Яндекс не подключён → только YouTube, без ошибок.
- `test_is_song` — yandex/soundcloud/youtube-короткое → True; радио/книга → False.

`tests/music/test_yandex.py`
- `test_ref_roundtrip` — `ref_of(123) == "ym-123"`, `track_id("ym-123") == "123"`.
- `test_download_falls_back_to_mp3_when_lossless_fails` (мок get-file-info → 403).
- `test_decrypt_encraw` — AES-CTR известным ключом/данными даёт исходные байты.
- `test_mood_to_rotor_covers_all_moods`.
- `test_feedback_errors_are_swallowed` — Яндекс упал → плеер не падает.

`tests/music/test_wave.py` (+)
- `test_compose_yandex_bucket_share` — при BUCKETS_YANDEX доля yandex ≈ 50% ±5.
`tests/music/test_player_rating.py` (+)
- `test_like_yandex_track_calls_set_like_and_downloads_liked` (моки).
- `test_skip_sends_yandex_feedback`.
`tests/music/test_library.py` (+)
- `test_import_likes_does_not_override_dislike`.
- `test_stats_by_source`.

Ручная проверка на asus после подключения аккаунта: поиск, FLAC реально скачивается и играет
в Safari, волна Яндекса, лайк появляется в приложении Яндекса.

## Least confident decisions
1. **FLAC-подпись.** Ключ для `get-file-info` — из клиентов Яндекса (в библиотеке есть ключ
   Android-приложения, у сторонних загрузчиков — десктопный). Пробуем оба; не сработали —
   MP3 320. Проверить можно только с твоим токеном.
2. **Порог совпадения 0.8** и штрафы за cover/live/remix — подобраны на глаз; калибрую на
   реальных запросах в слайсе поиска.
3. **Параллельный поиск Яндекс + YT Music** на каждую просьбу — быстрее (≈1 с вместо 1+3 с),
   но лишний запрос в YouTube, когда Яндекс нашёл.
4. **Импорт всех лайков Яндекса** в библиотеку: если их тысячи — корзина liked будет в
   основном из Яндекса. Это и есть твой вкус, но скачиваем их только при проигрывании.
5. **Фидбек волне Яндекса** отправляем и для треков волны Джарвиса, взятых из Яндекса
   (не только из rotor) — чтобы Яндекс учился шире; если Яндекс начнёт «дурить», отключу.
6. **FLAC в iOS Safari** — поддерживается, но FLAC 24/96, если придёт, может не играть в
   `<audio>` на старых iPhone; тогда отдаём MP3 320 для воспроизведения, FLAC храним.
