# Program Design: Итерация 1 — Быстрее

Проверено вживую 2026-09-27 (DeepSeek `deepseek-flash`, с asus): `stream: true` вместе с `tools` работает.
Обычный ответ: первый текст через **0,8 с**, весь через 1,45 с. Просьба с поиском: сначала текст
«Сейчас проверю.» (0,99 с), затем `tool_calls` в том же потоке.

## Files
Бэкенд:
- `app/metrics.py` — **новый.** Секундомер хода (ContextVar), журнал `data/metrics/turns.jsonl`, медианы.
- `app/main.py` — `_detach`/`_answer` запускают секундомер, открывают потоковую озвучку, в событие `reply`
  кладут `timing`. Новые роуты `POST /metrics/turn`, `GET /metrics/latency`. `/tts/chunk` отдаёт 204.
- `app/schemas.py` — `ClientTurnMetrics` (тело `POST /metrics/turn`).
- `app/services/llm.py` — `LLMClient.stream()` + `ChainLLM.cloud_stream()`: SSE-поток, сборка `tool_calls` из дельт.
- `app/agent/orchestrator.py` — `run_agent(..., on_sentence=)`, `SentenceSplitter`. С колбэком облачный цикл идёт
  потоком, без колбэка всё как раньше.
- `app/services/speech.py` — `SpeechStream` (растущая озвучка), `chunk()` ждёт кусок и возвращает `None` в конце.
- `app/agent/router.py` — быстрые «время / дата / погода / таймеры». Работают и на asus (стоят до проверки `is_web`).
- `app/main.py:_lifespan` — `metrics.prune()` при старте.

Фронт:
- `web/src/types.ts` — `TurnTiming`, `Message.timing`, `Message.interrupted`, `TurnEvent.timing`, `speech.streaming`.
- `web/src/api.ts` — `fetchSpeechChunk()` → `Blob | null` (204 = конец); `sendTurnMetrics()`.
- `web/src/composables/useWakeWord.ts` — `onWake(command, heardAt)`: время последнего распознанного слова.
- `web/src/App.vue` — `t0` хода, проигрывание растущей озвучки, первый звук → `timing.firstSound`,
  перебивание → `interrupted`, отправка замеров.
- `web/src/components/LatencyLine.vue` — **новый.** Строка под ответом, по тапу раскрывает этапы (как в мокапе).
- `web/src/components/ChatLog.vue` — выводит `LatencyLine` у ответов и «— прервано».

Доки: `docs/architecture.html` (поток ответа, метрики), `README.md` (как смотреть `/metrics/latency`).

## Types & signatures

```python
# app/metrics.py
Kind = Literal["fast", "agent", "hermes", "local", "stop", "empty"]
Stage = Literal["stt", "route", "llm_first", "llm_total", "tts_first"]

@dataclass
class Stopwatch:
    turn_id: str
    device: str
    kind: Kind | None = None
    started: float = field(default_factory=time.monotonic)
    stages: dict[str, float] = field(default_factory=dict)   # секунды от started, округление до 0,01

    def mark(self, stage: Stage) -> None: ...    # первая отметка этапа выигрывает, повторные игнорируются
    def as_dict(self) -> dict[str, float]: ...

def begin(turn_id: str, device: str) -> Stopwatch: ...   # кладёт в ContextVar; дочерние задачи наследуют
def mark(stage: Stage) -> None: ...                       # no-op, если секундомера нет (Telegram, MCP, тесты)
def set_kind(kind: Kind) -> None: ...
def record_server(sw: Stopwatch) -> None: ...             # строка {"ts","turn_id","device","kind","stages"}
def record_client(m: ClientTurnMetrics) -> None: ...      # строка {"ts","turn_id","client": {...}}
def summary(days: int = 7) -> dict[str, dict[str, dict[str, float]]]: ...
    # {kind: {stage|"first_sound"|"silenced_after": {"median","p90","n"}}}; сервер и клиент слиты по turn_id
def prune(max_age_days: int = 30) -> None: ...

STORE = Path("data/metrics/turns.jsonl")
```

```python
# app/schemas.py
class ClientTurnMetrics(BaseModel):
    turn_id: str
    device: str
    first_sound: float | None = None      # с, конец фразы → первый звук ответа
    interrupted: bool = False
    silenced_after: float | None = None   # с, «Джарвис» услышан → голос остановлен
```

```python
# app/services/llm.py
class LLMClient:
    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
        on_text: Callable[[str], None],
    ) -> dict[str, Any]: ...
    # Возвращает то же, что chat(): {"role","content","tool_calls"?}. tool_calls склеены по delta.index.

class ChainLLM:
    async def cloud_stream(self, messages, tools, on_text: Callable[[str], None]) -> dict[str, Any]: ...
```

```python
# app/agent/orchestrator.py
class SentenceSplitter:
    def __init__(self, on_sentence: Callable[[str], None]) -> None: ...
    def feed(self, delta: str) -> None: ...   # отдаёт целые фразы по [.!?…] + пробел/перевод строки
    def flush(self) -> None: ...              # хвост без точки — в конце шага LLM

async def run_agent(
    history: list[dict], user_text: str, local: bool = False, fallback_local: bool = True,
    on_sentence: Callable[[str], None] | None = None,
) -> tuple[str, list[str]]: ...
# metrics.mark("llm_first") — на первой фразе; metrics.mark("llm_total") — когда ответ готов.
```

```python
# app/services/speech.py
@dataclass
class _Job:
    chunks: list[str]
    parts: dict[int, asyncio.Task[bytes]]
    created: float
    open: bool = False                                # растущая: куски ещё добавляются
    changed: asyncio.Event = field(default_factory=asyncio.Event)

class SpeechStream:
    job_id: str
    def __init__(self) -> None: ...            # регистрирует _Job(open=True)
    def feed(self, sentence: str) -> None: ...
    # первая фраза — сразу кусок 0 (синтез стартует); дальше копит, пока не превысит GROWTH × прошлый кусок;
    # после SPOKEN_MAX_CHARS новые фразы не озвучиваются
    def close(self) -> Spoken: ...             # дописать хвост (+ «Остальное — на экране.», если резали), open=False
    @property
    def opened(self) -> bool: ...              # была ли хоть одна фраза

async def chunk(job_id: str, n: int) -> bytes | None: ...
# None — растущая озвучка закрыта и куска n нет (→ 204). Ждёт changed не дольше 60 с. KeyError — нет job.
```

```python
# app/main.py
async def _answer(
    session_id: str, text: str, device: str, speak: bool, inline: bool, progress: Callable[[str], None],
    on_speech: Callable[[Spoken], None] | None = None,   # веб: сообщить экрану id растущей озвучки
) -> tuple[str, list[str], Spoken | None]: ...

@app.post("/metrics/turn")
async def metrics_turn(m: ClientTurnMetrics) -> dict: ...
@app.get("/metrics/latency")
async def metrics_latency(days: int = 7) -> dict: ...
```

```python
# app/agent/router.py
_TIME = re.compile(r"^(?:который\s+час|сколько\s+(?:сейчас\s+)?времени|какое\s+(?:сейчас\s+)?время)$")
_DATE = re.compile(r"^(?:какое\s+сегодня\s+число|какой\s+сегодня\s+день(?:\s+недели)?|какое\s+число)$")
_WEATHER_NOW = re.compile(r"^(?:какая\s+)?погода(?:\s+(?:сейчас|сегодня|на\s+улице))?$|^что\s+с\s+погодой$")
_TIMERS = re.compile(r"^(?:какие\s+(?:у\s+меня\s+)?(?:таймеры|напоминания)|что\s+(?:у\s+меня\s+)?(?:по\s+таймерам|стоит))$")

def say_time(now: datetime) -> str: ...     # «Сейчас 21:47.» (цифры прописью делает speakable)
def say_date(now: datetime) -> str: ...     # «Сегодня воскресенье, двадцать седьмое сентября.» — порядковое словами,
                                           #   speakable прочёл бы «27» как «двадцать семь»
async def weather_now() -> str: ...         # первая строка weather.run({}) — «сейчас»
def say_timers(alarms: list[Alarm], now: float) -> str: ...  # «Таймеров нет.» / «Два: в 18:30 — …»
```

```ts
// web/src/types.ts
export interface TurnTiming {
  stages?: Partial<Record<'stt' | 'route' | 'llm_first' | 'llm_total' | 'tts_first', number>>
  kind?: 'fast' | 'agent' | 'hermes' | 'local' | 'stop' | 'empty'
  firstSound?: number        // с, считает клиент
}
// Message += timing?: TurnTiming; interrupted?: boolean
// TurnEvent += timing?: TurnTiming; speech?: { id: string; count: number; streaming?: boolean } | null

// web/src/api.ts
export async function fetchSpeechChunk(id: string, n: number): Promise<Blob | null>  // 204 → null
export function sendTurnMetrics(m: { turn_id: string; first_sound?: number; interrupted?: boolean; silenced_after?: number }): void

// web/src/composables/useWakeWord.ts
onWake: (command: string, heardAt: number) => void   // heardAt — Date.now() последнего onresult

// web/src/components/LatencyLine.vue
defineProps<{ timing: TurnTiming; interrupted?: boolean }>()
```

## Call stack

**Голос → свой агент (поток):**
```
App.onWake(cmd, heardAt) / finishRecording() → t0 → ask()
main._detach.job
  metrics.begin(turn_id, device)
  _transcribe → metrics.mark("stt")
  _start_turn → turns.start → _answer(…, on_speech)
    router.try_fast → None → metrics.mark("route")
    _think → run_agent(history, text, on_sentence=feed)
      _run_cloud → llm.cloud_stream(messages, TOOLS, splitter.feed)
        первая фраза → metrics.mark("llm_first") → feed(s):
          stream = SpeechStream() (лениво) → on_speech(Spoken(id, streaming)) → turns.emit(speech={…streaming})
          stream.feed(s) → speech._ensure(job, 0) → synth → metrics.mark("tts_first")
      ответ готов → metrics.mark("llm_total")
    stream.close() → Spoken (count финальный)
  turns.emit(reply, tools, speech={id, count}, timing=sw.as_dict())
  metrics.record_server(sw)          # после ожидания куска 0, не дольше 30 с
App.onTurn(speech.streaming) → speak() → playSpeech: fetchSpeechChunk(id, n) до null
  первый playUrl → firstSound = now − t0 → msg.timing.firstSound → sendTurnMetrics
App.onTurn(reply) → push('assistant', reply, tools, timing); speech.id уже звучит → не повторять
```

**Быстрая команда:** `router.try_fast` → `FastReply("Сейчас 21:47.", "time")` → `set_kind("fast")` → `speech.speak`
(как раньше) → `emit(reply, timing)`.

**Hermes / локальная / asus (inline):** `on_sentence` не передаётся → `speech.speak(reply)` как раньше.

**Перебивание:** `useWakeWord.onAlert` → `App.interrupt()` запоминает звучавший ход (`speakingTurn`) → `player.stop()`
→ `msg.interrupted = true` → `sendTurnMetrics({interrupted, silenced_after})`.

## Test plan
`tests/test_metrics.py`
- `test_mark_first_wins` — повторный `mark("stt")` не перезаписывает.
- `test_mark_without_stopwatch_is_noop` — вне `begin()` ничего не падает и не пишется.
- `test_stopwatch_inherited_by_child_task` — `mark()` из `asyncio.create_task` попадает в секундомер хода.
- `test_summary_merges_client_and_server` — строки сервера и клиента с одним turn_id дают одну запись; медиана/p90 по kind.
- `test_summary_ignores_older_than_days`, `test_prune_drops_old_lines`.

`tests/test_speech_stream.py` (синтез замокан)
- `test_first_sentence_is_chunk_zero_immediately` — после одного `feed` кусок 0 уже синтезируется.
- `test_chunks_grow` — следующий кусок ≤ GROWTH × предыдущий.
- `test_chunk_waits_for_feed` — `chunk(id, 1)` ждёт и отдаёт кусок после `feed`.
- `test_chunk_after_close_returns_none` — после `close()` запрос за концом → `None`.
- `test_voice_cap` — после 450 символов лишнее не озвучивается, в конце «Остальное — на экране.».
- `test_plain_speak_unchanged` — обычный `speak()` ведёт себя как раньше (count известен сразу).

`tests/test_llm_stream.py` (httpx замокан)
- `test_stream_text_deltas` — `on_text` получает дельты, итог `content` склеен.
- `test_stream_tool_calls_assembled` — аргументы tool_call склеены из кусков по `index`.
- `test_stream_http_error_raises` — 402 → `HTTPStatusError`, как у `chat()`.

`tests/test_orchestrator_stream.py`
- `test_sentences_in_order_across_steps` — «Сейчас проверю.» → поиск → ответ: фразы идут по порядку.
- `test_reply_is_final_step_text` — на экран уходит текст последнего шага.
- `test_no_callback_uses_chat` — без `on_sentence` зовётся `chat()`, не поток.

`tests/test_router_quick.py`
- `test_time` («который час» при 21:47 → «Сейчас 21:47.»), `test_date` (27.09.2026 → «Сегодня воскресенье, двадцать седьмое сентября.»).
- `test_weather_now_first_line` (weather.run замокан), `test_weather_tomorrow_goes_to_agent` («погода завтра» → None).
- `test_timers_empty`, `test_timers_two`.
- `test_quick_commands_work_on_asus` — время/дата не требуют веб-устройства.

`tests/test_core_flow.py` (дополнить)
- `test_stream_speech_emitted_before_reply` — событие `speech.streaming` приходит раньше `reply`, id совпадают.
- `test_reply_event_has_timing` — в `reply` есть `timing.stages.stt/route`.
- `test_hermes_path_does_not_stream` — у Hermes нет события `streaming`.

Фронт: `npm run typecheck`, `npm run build`. Вживую на телефоне: вопрос голосом → строка времени; «Джарвис» посреди
ответа → «прервано»; `curl /metrics/latency`.

## Least confident decisions
1. **Вступление «Сейчас проверю.» озвучивается, но в чат не пишется**: в чате только итоговый ответ. Голосом оно
   полезно (сразу понятно, что работает), в тексте было бы мусором. Вариант: показывать серым над ответом.
2. **Когда кусок «запечатывается».** Правило «копить до GROWTH × прошлый» плюс сброс в конце каждого шага LLM.
   Если модель пишет медленно, кусок 1 может подождать. Можно запечатывать и по таймеру, например 300 мс без новых фраз.
3. **Секундомер в ContextVar**, а не параметром. Меньше правок сигнатур (orchestrator, speech), но менее явно.
   Задачи, созданные вне хода (GET `/tts/chunk`), его не видят, поэтому `tts_first` ставится в `_ensure` самой job.
4. **«Замолчал через» мерит только от `onAlert` до `stop()`**, это почти всегда ~0 с. Задержку самого распознавания
   Apple браузер не показывает. Честная цифра «≤ 0,5 с» по-настоящему измеряется только на asus (итерация 5).
5. **t0 = конец фразы.** Для кнопки это момент автостопа (с хвостом тишины), для «Джарвис» — последнее распознанное
   слово (так видна пауза 1,8 с). Для текста — отправка. Сравниваем виды ходов внутри одного способа ввода.
6. **Облако упало после озвученного вступления → отвечает Hermes.** Растущая озвучка закрывается, ответ Hermes
   звучит второй озвучкой. Редкий случай, отдельный код не пишем, только тест, что не зависает.
7. **Быстрая «погода» без города.** «Погода в Питере» и «завтра» уходят агенту. Правило узкое намеренно.
