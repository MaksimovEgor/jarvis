# Program Design: Тексты песен и поиск по каталогам

## Files
| Файл | Что и почему |
|---|---|
| `app/music/lyrics.py` **new** | Текст трека: Яндекс LRC → LRCLIB; разбор LRC; дисковый кэш. Отдельно от плеера — чистые функции + два HTTP-источника. |
| `app/music/catalog.py` **new** | Поиск по вкладкам и `Entity` для запуска; кэш 10 мин. Всё «что показать в поиске» — здесь, плеер про это не знает. |
| `app/music/yandex.py` | `lyrics`, `artist_tracks`, `album_tracks`, `playlist_tracks`, `search_all`, `search_books`; `to_track` ставит `kind` для книг/подкастов. |
| `app/music/models.py` | `Entity`, `EntityType`, `SearchTab`. |
| `app/music/sources.py` | `is_song` исключает книги/подкасты любого источника. |
| `app/music/player.py` | `play_entity`, `add_next`, `queue_view/move/remove`, `play_at`; `_display` для книг Яндекса. |
| `app/music/library_api.py` | `GET /library/lyrics`, `GET /library/search`; `POST /library/play` mode `entity` / `next`. |
| `app/music/web_player.py` | `GET /player/queue`, `POST /player/queue/{move,remove,play}`. |
| `app/music/outputs.py` | `WebOutput.show(what)` — событие `{type: "ui", open}` экрану. |
| `app/agent/router.py` | «покажи текст», «что он поёт» → `output.show("lyrics")`; «включи аудиокнигу/подкаст X» → Яндекс-книги сначала. |
| `app/mcp_server.py` | `show_lyrics()`; `play_music(kind=audiobook|podcast)` → Яндекс, затем YouTube. |
| `web/src/components/LyricsView.vue` **new** | Караоке по позиции `<audio>`, тап по строке — seek. |
| `web/src/components/QueueView.vue` **new** | Список очереди, ▲▼ перестановка, ✕ удалить, тап — играть. |
| `web/src/components/SearchScreen.vue` **new** | Поле, вкладки, секции, «⋯»-меню трека. |
| `web/src/composables/useSearch.ts` **new** | Debounce 400 мс, отмена устаревших запросов, запоминание запроса/вкладки (localStorage). |
| `web/src/components/NowPlaying.vue`, `App.vue`, `api.ts`, `types.ts`, `useMusic.ts` | Кнопки «Текст/Очередь/Поиск», 🔍 в шапке, событие `ui`. |
| `tests/music/test_lyrics.py`, `test_catalog.py`, `test_queue.py` **new** | см. Test plan. |

## Types & signatures
```python
# models.py
EntityType = Literal["track", "artist", "album", "playlist", "audiobook", "podcast", "station"]
SearchTab = Literal["yandex", "youtube", "soundcloud", "books", "podcasts", "kids"]

@dataclass(frozen=True)
class Entity:
    source: Literal["yandex", "youtube", "soundcloud"]
    type: EntityType
    id: str                  # ym-трек ref | yandex artist/album id | "uid:kind" плейлиста | станция | YouTube id | sc-ref
    title: str
    subtitle: str = ""       # «КИНО · 4:46», «Исполнитель», «Аудиокнига · 36 глав»
    cover: str | None = None # для трека — media/cover/<ref>; иначе URL Яндекса 200x200
    cover_crop: bool = False
    track: Track | None = None   # для type=track — готовый трек (не искать заново)

# lyrics.py
@dataclass(frozen=True)
class Lyrics:
    synced: list[tuple[float, str]] | None
    plain: str | None
    source: Literal["yandex", "lrclib"] | None     # None — текста нет

def parse_lrc(text: str) -> list[tuple[float, str]]: ...   # [mm:ss.xx] строка; несколько меток на строку; пустые строки — пауза
async def get(track: Track) -> Lyrics: ...                 # кэш → Яндекс (ym-) → LRCLIB get → LRCLIB search
async def _lrclib(artist: str, title: str, duration: float | None) -> Lyrics | None: ...

# catalog.py
KIDS_GENRES = {"childrensliterature", "children", "fairytales", "kids", "forchildren"}
KIDS_STATIONS = ["editorial:station-1", "editorial:station-4", "editorial:station-5", "editorial:station-17", "editorial:station-13"]

@dataclass(frozen=True)
class Section:
    kind: Literal["best", "tracks", "artists", "albums", "playlists", "books", "podcasts", "stations", "videos"]
    title: str
    items: list[Entity]

async def search(query: str, tab: SearchTab) -> list[Section]: ...   # кэш 10 мин по (tab, query)
async def resolve_entity(entity: Entity) -> tuple[list[Track], int]: ... # треки для очереди + индекс старта
    # track → [track]; artist → популярные; album → по порядку; playlist → по порядку;
    # audiobook → главы, старт — первая недослушанная; podcast → выпуски от новых к старым

# yandex.py
async def lyrics(ref: str) -> tuple[str | None, str | None]: ...      # (LRC, TEXT)
async def search_all(query: str) -> Any: ...                          # Search целиком (best/tracks/artists/albums/playlists/podcasts)
async def artist_tracks(artist_id: str, limit: int = 30) -> list[Track]: ...
async def album_tracks(album_id: str) -> list[Track]: ...             # volumes → плоско; книги/подкасты kind=audiobook|podcast
async def playlist_tracks(owner: str, kind: str) -> list[Track]: ...

# player.py
async def play_entity(self, entity: Entity) -> str: ...
async def add_next(self, track: Track) -> str: ...
def queue_view(self) -> dict[str, Any]: ...            # {pos, tracks: [{ref, song, artist, cover, coverCrop}]}
async def queue_move(self, src: int, dst: int) -> None: ...   # только будущие; текущий не двигается
async def queue_remove(self, index: int) -> None: ...
async def play_at(self, index: int) -> str: ...

# outputs.py
class WebOutput:
    def show(self, what: Literal["lyrics", "queue"]) -> None: ...
```
```ts
// types.ts
export interface LyricsData { synced: { t: number; line: string }[] | null; plain: string | null; source: 'yandex' | 'lrclib' | null }
export type SearchTab = 'yandex' | 'youtube' | 'soundcloud' | 'books' | 'podcasts' | 'kids'
export interface Entity { source: string; type: 'track' | 'artist' | 'album' | 'playlist' | 'audiobook' | 'podcast' | 'station'; id: string; title: string; subtitle: string; cover: string | null; coverCrop: boolean }
export interface SearchSection { kind: string; title: string; items: Entity[] }
export interface QueueData { pos: number; tracks: UpcomingTrack[] }
// api.ts
fetchLyrics(ref): Promise<LyricsData>; search(q, tab, signal): Promise<SearchSection[]>
playEntity(entity): Promise<void>; playNext(entity): Promise<void>
fetchQueue(): Promise<QueueData>; queueMove(from, to); queueRemove(i); queuePlay(i)
```

## Call stack
**Текст**: `NowPlaying «Текст»` → `LyricsView` mount → `fetchLyrics(ref)` → `library_api.lyrics` →
`lyrics.get(track)` (кэш → `yandex.lyrics` → `parse_lrc` | `_lrclib`) → JSON. На клиенте
`watch(position)` → бинарный поиск строки → `scrollIntoView({block:'center', behavior:'smooth'})`;
тап → `music.seek(t)`. Ручная прокрутка пользователем — автопрокрутка пауза 4 с.
**Поиск**: `useSearch` (debounce, AbortController) → `GET /library/search` → `catalog.search(q, tab)`
→ (кэш) → Яндекс `search_all` / `youtube.music_search + youtube.search` / `soundcloud_search` /
Яндекс `search(type_="podcast")` с разделением по `album.type` / стансции + фильтр `KIDS_GENRES`.
Тап → `POST /library/play {mode: entity}` → `player.play_entity` → `catalog.resolve_entity` →
`_start_queue(tracks)` (+ `_extend_with_mix` для трека; `wave` для станции).
**Очередь**: `QueueView` → `GET /player/queue` → действия → `Player.queue_*` → `_publish_upcoming`.
**Голос «покажи текст»**: `router` → `devices.web_output(device).show("lyrics")` → SSE `ui` →
`App` открывает `NowPlaying` с текстом.

## Test plan
`tests/music/test_lyrics.py`
- `test_parse_lrc_basic` — метки → секунды, порядок.
- `test_parse_lrc_multiple_timestamps_and_blank` — `[00:10][00:50]припев`, пустые строки.
- `test_get_prefers_yandex_for_ym` / `test_get_falls_back_to_lrclib` / `test_get_none_cached` (моки).
`tests/music/test_catalog.py`
- `test_books_and_podcasts_split_by_album_type` (фейковый ответ поиска).
- `test_kids_filters_by_genre_and_adds_stations`.
- `test_search_cached_10_min` — второй вызов без запроса к источнику.
- `test_resolve_audiobook_starts_at_first_unfinished` (позиции).
- `test_resolve_podcast_newest_first`.
`tests/music/test_queue.py`
- `test_queue_move_keeps_current`, `test_queue_remove_future_only`, `test_play_at`, `test_add_next_inserts_after_current`.
`tests/test_router_music.py` (+) — `test_show_lyrics_phrases`.
Ручная: текст «Группа крови» синхронно; Vein — LRCLIB; поиск по всем вкладкам; «Незнайка на Луне» с
главы; подкаст — последний выпуск; очередь на телефоне.

## Least confident decisions
1. **Детское по жанрам** (`childrensliterature` и т.п.) — список жанров Яндекса угадан частично;
   что не попадёт — ищется в «Книгах». Дополню по реальным выдачам.
2. **Подкаст: последний выпуск, дальше — предыдущие** (а не по порядку с первого) — как у
   Яндекса для новостных подкастов; для сериальных подкастов удобнее с первого — возможно, нужна
   кнопка «с начала».
3. **Очередь без drag-and-drop** — кнопки ▲▼ вместо перетаскивания: надёжно на iPhone без
   библиотек; перетаскивание — позже, если захочешь.
4. **LRCLIB** — открытый проект сообщества; для редкого русского андеграунда текста может не быть.
5. Книги/подкасты Яндекса качаются **целиком** перед игрой (главы 20–60 мин, 20–60 МБ, 3–10 с
   ожидания) — поток Яндекса без скачивания отдельно не реализую в этой версии.
