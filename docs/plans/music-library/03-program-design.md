# Program Design: Музыкальная библиотека и «Моя волна»

## Files

### Ядро (Python)
| Файл | Что и почему здесь |
|---|---|
| `app/music/library.py` **new** | `Library` — SQLite (`data/music/library.db`): треки, оценки, прослушивания, заметки, зёрна. Единственный, кто пишет в БД. Синхронный `sqlite3` (запросы < 1 мс, новых зависимостей нет). |
| `app/music/storage.py` **new** | Где лежит файл трека: `liked/` (вечно) или `cache/` (LRU); лимит `10 ГБ − liked`. Всё про файлы из `youtube.py` (`_cached`, `_prune_cache`) переезжает сюда. |
| `app/music/wave.py` **new** | «Моя волна»: слот времени, чистая функция `compose()` (что ставить в очередь) + `Wave` (источник очереди, досыпает треки). Чистая часть — чтобы тестировать без сети. |
| `app/music/taste.py` **new** | Клиент профиля dj: зёрна, разметка треков, суточная сводка. Hermes dj → запасной `LLM_*` → ничего. Разбор JSON-ответов. |
| `app/music/library_api.py` **new** | `/library/*` для веба. Рядом с `web_player.py` по тому же образцу. |
| `app/music/models.py` | `Track` + `artist`, `origin`; `Slot`, `Mood`, `Outcome`, `Rating`. |
| `app/music/youtube.py` | `_list` печатает канал → `artist`; кэш через `storage`; `download(track, timeout)`. |
| `app/music/player.py` | `play_wave`, `play_liked`, `rate`; журнал прослушиваний (старт/исход); досыпание из `Wave`; `next()` различает пропуск и дослушанное. |
| `app/music/outputs.py` | `WebOutput`: в снимок `ref/rating/origin/from`; сторож зависаний; `Output.set_meta()`. |
| `app/music/web_player.py` | `PlayerReport.stalled`; `PlayerControl.action` + `like`/`dislike`. |
| `app/music/media.py` | `/media/yt/<id>` берёт путь через `storage.path_for` (liked или cache). |
| `app/mcp_server.py` | `play_wave`, `rate_track`, `play_liked`, `music_taste_note`; `now_playing` говорит «из лайков». |
| `app/agent/router.py` | Быстрые фразы: лайк/дизлайк, волна с настроением, «моя музыка». |
| `app/agent/prompts.py` | `VOICE_INSTRUCTIONS` — новые инструменты. |
| `app/config.py` | `music_storage_max_mb`, `music_liked_dir`, `dj_hermes_url`, `dj_hermes_api_key`. |
| `app/main.py` | `include_router(library_api.router)`; в lifespan — `library.open()`, суточный `taste.digest` в 04:00. |
| `app/timers.py` | не меняется (digest — отдельная asyncio-задача в lifespan, не будильник). |

### Hermes dj (вне приложения, но в репо)
| `scripts/hermes-dj/SOUL.md` **new** | Личность dj: «музыкальный куратор, отвечает строго JSON по схеме из запроса, устойчивые выводы о вкусе — в память». |
| `scripts/hermes-dj/setup.sh` **new** | `profile create dj --clone`, SOUL, порт 8643 в `.env` профиля, копия музыкальной части USER.md, юнит. |
| `scripts/systemd/hermes-dj.service` **new** | `hermes -p dj gateway run` как systemd --user. |

### Веб (Vue)
| `web/src/types.ts` | `PlayerState` + `ref, rating, origin, from`; `LibraryTrack`, `LibraryStorage`, `HiddenItem`. |
| `web/src/api.ts` | `rateTrack`, `libraryPlay`, `fetchLikes`, `fetchHidden`, `fetchStorage`. |
| `web/src/composables/useMusic.ts` | отдаёт `rating/origin/from`, `like()/dislike()/undo()`; `waiting` > 10 с → `report({stalled})`. |
| `web/src/composables/useLibrary.ts` **new** | Данные экрана «Моя музыка»: лайки (пагинация), скрытые, место. |
| `web/src/components/PlayerBar.vue` | ♡/👎, чипы «волна · вечер», «кэш/сеть/♥ сохранено». |
| `web/src/components/RateToast.vue` **new** | «Сохранил» / «Больше не включу… Отменить» (4 с). |
| `web/src/components/MyMusic.vue` **new** | Экран: карточка волны + чипы настроений, вкладки Лайки/Скрытые, шкала места. |
| `web/src/App.vue` | Кнопка «♪ Моя музыка» в шапке, экран поверх чата (`v-if`, без vue-router). |

### Тесты и доки
| `tests/music/test_library.py`, `test_wave.py`, `test_storage.py`, `test_taste.py`, `tests/test_router_music.py` **new** | pytest (см. решение №1). |
| `requirements-dev.txt` **new** | `pytest`, `pytest-asyncio` — **нужно твоё подтверждение на установку**. |
| `docs/architecture.html`, `README.md` | Актуализация схемы (правило из памяти) и раздела «Музыка». |

## Types & signatures

```python
# app/music/models.py
Slot = Literal["morning", "day", "evening", "night"]          # 6–11, 11–17, 17–23, 23–6
Mood = Literal["auto", "energetic", "calm", "focus", "sleep", "discover"]
Origin = Literal["wave", "liked", "query", "mix", "radio", "local"]
Outcome = Literal["finished", "skipped", "disliked", "error", "stalled", "stopped"]
Rating = Literal[1, -1]
FromWhere = Literal["liked", "cache", "net", "stream"]

@dataclass(frozen=True)
class Track:
    title: str
    source: Source
    ref: str
    duration: float | None = None
    kind: Kind = "music"
    artist: str | None = None       # новое: канал «X - Topic»/«X» или «X - Song» из заголовка
    origin: Origin = "query"        # новое: откуда трек в очереди (для метрики и чипа)
```

```python
# app/music/library.py
@dataclass(frozen=True)
class TrackInfo:
    ref: str; title: str; artist: str | None; duration: float | None
    energy: int | None; tags: list[str]; rating: Rating | None; added_at: float

@dataclass(frozen=True)
class ArtistStats:
    dislikes: int; skips_30d: int; finished_30d: int
    slot_finished: dict[Slot, int]                    # дослушано по слотам

@dataclass(frozen=True)
class Stats:                                          # метрика Gate 1
    wave_finished: int; wave_skipped: int; wave_disliked: int; stalls: int
    from_cache_share: float; median_start_ms: int | None

class Library:
    def __init__(self, path: Path) -> None: ...
    def open(self) -> None: ...                       # миграции CREATE IF NOT EXISTS, WAL
    def upsert(self, track: Track) -> None: ...
    def rate(self, ref: str, value: Rating | None) -> None: ...      # None — снять
    def rating(self, ref: str) -> Rating | None: ...
    def liked(self, offset: int = 0, limit: int = 50, query: str | None = None) -> list[TrackInfo]: ...
    def disliked(self) -> list[TrackInfo]: ...
    def banned_refs(self) -> set[str]: ...
    def artist_stats(self) -> dict[str, ArtistStats]: ...           # ключ — artist.casefold()
    def recent_refs(self, hours: float) -> set[str]: ...
    def set_tags(self, ref: str, artist: str | None, energy: int | None, tags: list[str]) -> None: ...
    def untagged_liked(self, limit: int) -> list[TrackInfo]: ...
    def start_play(self, track: Track, device: str, mood: Mood | None, where: FromWhere, start_ms: int | None) -> int: ...
    def finish_play(self, play_id: int, outcome: Outcome, listened_s: float) -> None: ...
    def add_note(self, text: str) -> None: ...
    def notes(self, since: float | None = None) -> list[str]: ...
    def save_seeds(self, seeds: list["Seed"], slot: Slot, mood: Mood) -> None: ...
    def seeds(self, slot: Slot, mood: Mood, max_age_h: float) -> list["Seed"]: ...
    def taste_summary(self, slot: Slot) -> dict[str, Any]: ...      # компактно для dj: топ/анти-топ артистов, лайки, пропуски по слоту
    def stats(self, days: int = 7) -> Stats: ...

library: Library                                       # модульный синглтон, как timers
```

```python
# app/music/storage.py
@dataclass(frozen=True)
class Usage:
    liked_mb: float; cache_mb: float; limit_mb: int

def path_for(ref: str) -> Path | None: ...             # liked/ первым, потом cache/ (touch для LRU)
def where(ref: str) -> FromWhere: ...                  # "liked" | "cache" | "net"
def cache_dir() -> Path: ...                           # куда качает yt-dlp
def pin(ref: str) -> bool: ...                         # cache→liked; False — файла нет (докачать)
def unpin(ref: str) -> None: ...                       # liked→cache
def drop(ref: str) -> None: ...                        # удалить из cache (дизлайк)
def prune(keep: Path | None = None) -> None: ...       # LRU cache до limit − liked
def usage() -> Usage: ...
```

```python
# app/music/wave.py
BUCKETS: dict[Mood, tuple[float, float, float]]        # (liked, similar, discover); auto=(.3,.5,.2), discover=(.1,.4,.5)
ENERGY: dict[tuple[Slot, Mood], tuple[int, int]]       # допустимая энергия: (night,auto)=(1,2), (morning,auto)=(3,5)…
ARTIST_GAP = 4; RECENT_HOURS = 3.0; DISLIKED_ARTIST_FACTOR = 0.25; AHEAD = 8; REFILL_BELOW = 3

def slot_at(ts: float) -> Slot: ...
def is_weekend(ts: float) -> bool: ...

@dataclass(frozen=True)
class Candidate:
    track: Track
    bucket: Literal["liked", "similar", "discover"]
    energy: int | None = None

@dataclass(frozen=True)
class Context:
    slot: Slot; mood: Mood
    banned: set[str]; recent: set[str]
    artists: dict[str, ArtistStats]
    recent_artists: list[str]                          # последние ARTIST_GAP в очереди

def weight(c: Candidate, ctx: Context) -> float: ...   # 0 — нельзя; энергия, штрафы, слот
def compose(pool: list[Candidate], ctx: Context, n: int, rng: random.Random) -> list[Track]: ...
    # чистая: пропорции корзин, взвешенный выбор без повторов, ARTIST_GAP, fallback между корзинами

class Wave:
    def __init__(self, device: str, mood: Mood) -> None: ...
    mood: Mood
    async def first(self) -> Track: ...                # мгновенный старт: лайк из liked/ под слот, иначе зерно
    async def more(self, queued: list[Track], n: int = AHEAD) -> list[Track]: ...
    def forget(self, ref: str, artist: str | None) -> None: ...     # после дизлайка
```

```python
# app/music/taste.py
@dataclass(frozen=True)
class Seed:
    query: str; bucket: Literal["similar", "discover"]; reason: str

@dataclass(frozen=True)
class TrackTag:
    ref: str; artist: str | None; energy: int | None; tags: list[str]

async def ask_seeds(slot: Slot, mood: Mood, weekend: bool) -> list[Seed]: ...     # dj → LLM → []
async def tag_tracks(tracks: list[TrackInfo]) -> list[TrackTag]: ...               # пачкой до 20
async def digest() -> None: ...                      # сводка за сутки → dj сохраняет в память
def refresh_seeds_soon(slot: Slot, mood: Mood) -> None: ...                         # фон, одна задача на (slot,mood)
def tag_soon(ref: str) -> None: ...                  # фон, склеивает в пачки раз в 30 с
def parse_seeds(text: str) -> list[Seed]: ...        # терпит ```json, мусор вокруг, лишние поля
def parse_tags(text: str) -> list[TrackTag]: ...
async def run_digest_daily(at_hour: int = 4) -> None: ...                           # задача lifespan
```

```python
# app/music/player.py (новое/изменённое)
SKIP_BEFORE = 30.0                                     # сек: раньше — «пропуск»
class Player:
    wave: Wave | None
    async def play_wave(self, mood: Mood = "auto") -> str: ...
    async def play_liked(self, query: str | None = None) -> str: ...
    async def play_ref(self, ref: str) -> str: ...     # с экрана «Моя музыка»
    async def rate(self, value: Rating | None, which: Literal["current", "previous"] = "current",
                   ref: str | None = None) -> str: ...
    # внутри: _begin_play(track, where), _end_play(outcome), _refill() — после каждого трека
```

```python
# app/music/outputs.py
class Output(ABC):
    def set_meta(self, meta: dict[str, Any]) -> None: ...   # ref, rating, origin, from — WebOutput в снимок, mpv игнорирует
    on_stall: StallHandler | None                            # Callable[[], Awaitable[None]]
class WebOutput(Output):
    STALL_SECONDS = 25.0                                      # серверный сторож (браузер шлёт позицию раз в 10 с)
```

```python
# app/music/library_api.py
class RateRequest(BaseModel):  device: str; ref: str | None = None; value: Literal["like", "dislike", "none"]
class PlayRequest(BaseModel):  device: str; mode: Literal["wave", "liked", "track"]; mood: Mood = "auto"; ref: str | None = None
GET  /library/likes?offset&limit&q -> list[TrackInfoOut]
GET  /library/hidden -> {"tracks": [...], "artists": [{"artist", "dislikes"}]}
POST /library/rate -> {"text": str, "rating": 1 | -1 | None}
POST /library/play -> {"text": str}
GET  /library/storage -> {"liked_mb", "cache_mb", "limit_mb"}
GET  /library/stats?days=7 -> Stats
```

```ts
// web/src/types.ts
export type Rating = 1 | -1 | null
export type FromWhere = 'liked' | 'cache' | 'net' | 'stream'
export interface PlayerState { /* как было */ ref?: string | null; rating?: Rating; origin?: string | null; from?: FromWhere | null }
export interface LibraryTrack { ref: string; title: string; artist: string | null; duration: number | null; addedAt: number }
export interface LibraryStorage { likedMb: number; cacheMb: number; limitMb: number }
export type Mood = 'auto' | 'energetic' | 'calm' | 'focus' | 'sleep' | 'discover'

// web/src/api.ts
export async function rateTrack(value: 'like' | 'dislike' | 'none', ref?: string): Promise<{ text: string; rating: Rating }>
export async function libraryPlay(mode: 'wave' | 'liked' | 'track', opts?: { mood?: Mood; ref?: string }): Promise<void>
export async function fetchLikes(offset: number, q?: string): Promise<LibraryTrack[]>
export async function fetchHidden(): Promise<{ tracks: LibraryTrack[]; artists: { artist: string; dislikes: number }[] }>
export async function fetchStorage(): Promise<LibraryStorage>
```

## Call stack

**«Включи музыку» / «что-нибудь бодрое»**
```
router._try_fast  (_WAVE: «включи (мою) (волну|музыку)», «что-нибудь <mood>»)
└ Player.play_wave(mood)
   ├ self.wave = Wave(device, mood)
   ├ track = await wave.first()             ─ library.liked() ∩ storage.where=="liked", compose(n=1)
   ├ _start_queue([track])                  ─ output.load → _begin_play → library.start_play
   ├ taste.refresh_seeds_soon(slot, mood)   ─ фон: dj → library.save_seeds
   └ create_task(_refill())                 ─ wave.more(queue) → extend + prefetch
        wave.more:
          pool = liked(slot)  +  youtube.mix(liked-зерно)×2  +  seeds(discover)→youtube.search
          ctx  = Context(banned=library.banned_refs(), recent=library.recent_refs(3), artists=library.artist_stats())
          return compose(pool, ctx, n)
```

**Трек закончился / «дальше»**
```
Output.on_end("eof") → Player._on_end → _end_play("finished") → _play_index(+1) → _refill() если впереди < 3
Player.next()        → _end_play("skipped" если позиция < SKIP_BEFORE и < 50%, иначе "finished") → …
```

**Лайк** (router «лайк» | MCP rate_track | POST /library/rate | control like)
```
Player.rate(1) → library.upsert + rate → storage.pin(ref) or create_task(youtube.download → pin)
              → taste.tag_soon(ref) → output.set_meta(rating=1) → «Сохранил в „Мою музыку“.»
```

**Дизлайк**
```
Player.rate(-1) → library.rate → _end_play("disliked") → wave.forget(ref, artist)
               → next() (очередь уже без забаненного) → storage.drop(ref) → «Больше не включу.»
```

**Зависание**
```
браузер: <audio> 'waiting'/'stalled' > 10 с при wantPlaying → report({stalled: true})
ядро:    WebOutput.report(stalled) | сторож: не на паузе, отчёт позиции не рос STALL_SECONDS
         → on_stall → Player._end_play("stalled") → _play_index(+1)
```

**Суточная сводка**: `lifespan → taste.run_digest_daily() → 04:00 → digest() → dj /v1/responses`.

## Test plan
Все тесты — без сети: `youtube`, dj и время подменяются.

`tests/music/test_library.py` (временная БД в tmp_path)
- `test_rate_like_then_none_clears_rating` — like → rating 1; none → None, трек пропал из `liked()`.
- `test_banned_refs_contains_only_disliked`.
- `test_artist_stats_counts_dislikes_case_insensitive` — «Кино»/«КИНО» — один исполнитель.
- `test_recent_refs_respects_window` — сыгранный 2 ч назад в окне 3 ч, 4 ч назад — нет.
- `test_stats_wave_share` — 7 finished, 2 skipped, 1 disliked в волне + 5 query → 0.7, query не считается.
- `test_stats_ignores_older_than_days`.

`tests/music/test_wave.py`
- `test_slot_boundaries` — 05:59 night, 06:00 morning, 17:00 evening, 23:00 night.
- `test_compose_never_returns_banned`.
- `test_compose_skips_recent`.
- `test_compose_bucket_proportions_auto` — на 1000 прогонах n=10 доли 30/50/20 ±5%.
- `test_compose_artist_gap` — один исполнитель не ближе 4 позиций, пока есть альтернатива.
- `test_compose_falls_back_when_bucket_empty` — нет discover → добирает из других, длина n.
- `test_weight_disliked_artist_quartered` — 2 дизлайка у артиста → вес ×0.25.
- `test_weight_energy_out_of_slot_zero` — энергия 5 ночью в auto → 0; неразмеченный → > 0.
- `test_compose_offline_only_local` — pool только liked → волна не пустая.

`tests/music/test_storage.py` (tmp_path, лимит маленький)
- `test_pin_moves_cache_to_liked_and_prune_never_deletes_liked`.
- `test_prune_limit_is_total_minus_liked` — лайки 6 МБ из 10 → кэш урезан до 4 МБ, старые первыми.
- `test_unpin_returns_to_cache`, `test_drop_removes_cache_only`.
- `test_path_for_prefers_liked`.

`tests/music/test_taste.py`
- `test_parse_seeds_from_fenced_json`, `test_parse_seeds_ignores_bad_items`, `test_parse_tags_clamps_energy_1_5`.
- `test_ask_seeds_falls_back_to_llm_when_dj_down` (httpx-мок: dj ConnectError → LLM ответ).
- `test_ask_seeds_returns_empty_when_both_down`.

`tests/music/test_player_rating.py` (фейковый Output в памяти)
- `test_dislike_skips_current_within_same_call` — после `rate(-1)` играет следующий, исход «disliked».
- `test_next_before_30s_records_skipped`, `test_next_after_half_records_finished`.
- `test_stall_advances_and_records_stalled`.
- `test_refill_when_less_than_3_ahead`.

`tests/test_router_music.py`
- `test_wave_phrases` — «включи музыку», «включи мою волну», «поставь что-нибудь бодрое» → play_wave с нужным mood (раньше «включи музыку» уходило в Hermes → тест падает на старом коде).
- `test_like_phrases` — «лайк», «мне нравится», «поставь лайк»; `test_dislike_phrases` — «дизлайк», «не нравится», «не включай больше это».
- `test_play_liked_phrase` — «включи мою музыку», «включи лайкнутое».
- `test_play_queen_still_youtube` — регрессия: «включи Queen» → play_youtube.

Ручная проверка после каждого слайса: curl `/library/*` на asus + телефон (Safari) и Mac.

## Least confident decisions
1. **pytest как dev-зависимость** (`requirements-dev.txt`: pytest, pytest-asyncio). Тестов в репо нет совсем; без них волну и хранилище проверять только руками. Нужно твоё «да» на установку.
2. **Исполнитель берётся из YouTube** (канал «X - Topic» или «X - Song» в заголовке), dj потом нормализует при разметке. Ошибка тут = штраф не тому исполнителю. Альтернатива (Last.fm/MusicBrainz) — внешний API, откладываю.
3. **«Похожее» = YouTube Mix от лайков.** Качество по вкусу в среднем хорошее, но Mix иногда уезжает в попсу/каверы. Фильтры (бан, штраф, энергия) это смягчают, но не лечат полностью.
4. **Порог пропуска: < 30 с и < 50% трека.** Пропуск на 2-й минуте считаем «дослушал», т.е. нейтрально-положительным.
5. **Энергию знает только LLM** (у YouTube нет аудио-признаков, Spotify audio-features закрыт). Пока трек не размечен — он проходит в любой слот с весом ×0.7.
6. **«Из интернета» для коротких треков = скачать (~3 с) и играть файл**, как сейчас — не поток: iPhone не играет сырой DASH. Следующий трек качается заранее, поэтому в волне задержка обычно 0.
7. **Холодный старт без лайков**: первый трек — зерно dj; если dj ещё не ответил — синхронный запрос к `LLM_*` с таймаутом 8 с; иначе — самые частые исполнители из `plays`; иначе — «Foo Fighters» из USER.md, прописанный в SOUL dj как стартовый вкус.
8. **Один вкус на всех** (одна `library.db`, устройства различаются только в `plays.device`) — соответствует «один пользователь» из Gate 1.
9. **SQLite синхронно в event loop** — запросы на тысячи строк < 1 мс; `aiosqlite` не тащим.
