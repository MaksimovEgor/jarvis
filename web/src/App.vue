<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { cancelTurn, fetchHistory, fetchSpeechChunk, sendAudio, sendText, type SpeechMore } from './api'
import BackgroundTasks from './components/BackgroundTasks.vue'
import ChatLog from './components/ChatLog.vue'
import MyMusic from './components/MyMusic.vue'
import NowPlaying from './components/NowPlaying.vue'
import PlayerBar from './components/PlayerBar.vue'
import RateToast from './components/RateToast.vue'
import TalkButton from './components/TalkButton.vue'
import { useEarcon } from './composables/useEarcon'
import { useMusic } from './composables/useMusic'
import { usePlayer } from './composables/usePlayer'
import { usePush } from './composables/usePush'
import { useRecorder } from './composables/useRecorder'
import { useWakeWord } from './composables/useWakeWord'
import type { Message, Status, TurnEvent, TurnsEvent } from './types'

const recorder = useRecorder()
const player = usePlayer()
const earcon = useEarcon()
const notifications = usePush()
// Объявления таймеров играет голосовой плеер, музыка на это время молчит.
const music = useMusic((url) => player.playUrl(url), onTurn, onTurns)
const {
  title: musicTitle,
  wantPlaying: musicPlaying,
  isLoading: musicLoading,
  live: musicLive,
  position: musicPosition,
  duration: musicDuration,
  trackRef: musicRef,
  rating: musicRating,
  origin: musicOrigin,
  from: musicFrom,
  meta: musicMeta,
} = music

// «Моя музыка» — экран поверх чата; всплывашка после оценки с «Отменить».
const showLibrary = ref(false)
const showNowPlaying = ref(false)

function onStop(): void {
  showNowPlaying.value = false
  music.stop()
}
const toast = ref<{ text: string; undoRef: string | null } | null>(null)
let toastTimer: number | null = null

function showToast(text: string, undoRef: string | null = null): void {
  toast.value = { text, undoRef }
  if (toastTimer !== null) clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => (toast.value = null), 4000)
}

function onLike(): void {
  const wasLiked = musicRating.value === 1
  music.like()
  showToast(wasLiked ? 'Убрал из «Моей музыки»' : '♥ Сохранил в «Мою музыку»')
}

function onDislike(): void {
  const ref = musicRef.value
  const title = musicTitle.value
  music.dislike()
  showToast(`Больше не включу «${title ?? 'этот трек'}»`, ref)
}

function onUndo(): void {
  if (toast.value?.undoRef) music.unrate(toast.value.undoRef)
  toast.value = null
}

// «Джарвис» слышен всегда, кроме момента записи команды: и пока Джарвис
// думает над прошлой просьбой, и пока говорит (голос тогда замолкает).
const wake = useWakeWord({
  onAlert: () => {
    interrupt()
    earcon.listen()
    music.hold()
  },
  onWake: (command) => {
    if (command) {
      void ask((turnId) => sendText(command, true, turnId), command)
      // Статус мог не смениться (уже «думаю») — снова слушать явно.
      syncWake()
    } else {
      void startRecording()
    }
  },
  onCancel: () => music.release(),
})

const messages = ref<Message[]>([])
const draft = ref('')
// Реплик в работе может быть несколько: новая не ждёт старые, а ядро само
// отменяет те, что она заменяет (app/turns.py).
const pending = ref(0)

let nextId = 1

// После перезагрузки чат на месте: история хранится на ядре (app/history.py).
void fetchHistory().then((entries) => {
  const restored: Message[] = []
  for (const e of entries) {
    restored.push({ id: nextId++, role: 'user', text: e.user, cancelled: e.cancelled })
    if (e.reply) restored.push({ id: nextId++, role: 'assistant', text: e.reply, tools: e.tools })
  }
  messages.value = [...restored, ...messages.value]
})

// --- ходы: ответ приходит событием SSE, а не в ответе на POST ---

// Досланный после переподключения ответ старше этого — только текстом.
const SPEAK_FRESH_SECONDS = 60

interface InFlight {
  msg: Message
  // POST вернул «принято» — ход точно есть на ядре.
  accepted: boolean
  // Музыка удержана на этот ход (реплика с этой вкладки).
  held: boolean
  // Ушёл в фон: «думаю» и музыку уже не держит.
  background: boolean
}

// Ходы в работе по id. Сюда же попадают идущие ходы, о которых вкладка
// узнала после перезагрузки (событие turns).
const inFlight = new Map<string, InFlight>()
// Завершённые — повторно досланный итог не показываем дважды.
const settled = new Set<string>()

function begin(turnId: string, msg: Message, held: boolean, background = false): InFlight {
  const entry: InFlight = { msg, accepted: false, held, background }
  msg.pending = { turnId, since: Date.now(), background }
  inFlight.set(turnId, entry)
  if (!background) pending.value += 1
  return entry
}

const backgroundTasks = computed(() => messages.value.filter((m) => m.pending?.background))

function isFresh(event: TurnEvent): boolean {
  return (event.age ?? 0) < SPEAK_FRESH_SECONDS
}

// Ход ушёл в фон: основной план свободен — снять «думаю», озвучить
// подтверждение и вернуть музыку.
async function toBackground(entry: InFlight, event: TurnEvent): Promise<void> {
  entry.background = true
  if (entry.msg.pending) entry.msg.pending.background = true
  pending.value -= 1
  const held = entry.held
  entry.held = false
  if (event.ack) push('assistant', event.ack)
  if (event.speech && isFresh(event)) await speak(event.speech)
  if (held) music.release()
}

// Ход закончился: снять «думаю». Музыку отпускает вызывающий — после озвучки.
function settle(turnId: string): InFlight | undefined {
  settled.add(turnId)
  const entry = inFlight.get(turnId)
  if (!entry) return undefined
  inFlight.delete(turnId)
  entry.msg.pending = undefined
  if (!entry.background) pending.value -= 1
  return entry
}

async function onTurn(event: TurnEvent): Promise<void> {
  const entry = inFlight.get(event.id)
  if (event.transcript !== undefined && entry) entry.msg.text = event.transcript || '(не расслышал)'
  if (event.tool && entry?.msg.pending) entry.msg.pending.tool = event.tool
  if (event.background && entry && !entry.background) {
    await toBackground(entry, event)
    return
  }
  const final = event.reply !== undefined || event.cancelled || event.error !== undefined
  if (!final || settled.has(event.id)) return

  const done = settle(event.id)
  if (event.cancelled) {
    if (done) done.msg.cancelled = true
  } else if (event.error !== undefined) {
    push('error', event.error)
  } else {
    push('assistant', event.reply ?? '', event.tools)
    if (event.speech && isFresh(event)) {
      if (done?.background) {
        // Фоновая задача готова: сигнал, и музыка молчит, пока звучит ответ.
        music.hold()
        earcon.ready()
        await speak(event.speech)
        music.release()
      } else {
        await speak(event.speech)
      }
    }
  }
  if (done?.held) music.release()
}

// После (пере)подключения SSE: ядро прислало пропущенное и список идущих ходов.
function onTurns(event: TurnsEvent): void {
  const active = new Set(event.active.map((t) => t.id))
  // Принятый ход пропал с ядра, а итога нет — ядро перезапускалось.
  const lost = [...inFlight].filter(([turnId, entry]) => entry.accepted && !active.has(turnId))
  if (lost.length) void recoverLost(lost)
  // Идущие ходы, о которых вкладка не знает (её перезагрузили), — на экран.
  for (const t of event.active) {
    if (inFlight.has(t.id) || settled.has(t.id)) continue
    const entry = begin(t.id, push('user', t.text || '…'), false, t.background)
    entry.accepted = true
    if (entry.msg.pending && t.tool) entry.msg.pending.tool = t.tool
  }
}

// Ответ мог успеть записаться в историю до перезапуска — ищем его там.
async function recoverLost(lost: [string, InFlight][]): Promise<void> {
  const history = await fetchHistory()
  for (const [turnId, entry] of lost) {
    if (!settle(turnId)) continue
    const found = history.find((e) => e.turn_id === turnId)
    if (found?.cancelled) entry.msg.cancelled = true
    else if (found) push('assistant', found.reply, found.tools)
    else push('error', 'Ответ потерялся: Джарвис перезапускался. Повтори, пожалуйста.')
    if (entry.held) music.release()
  }
}

function onCancel(turnId: string): void {
  void cancelTurn(turnId).catch((e) => push('error', errorText(e)))
}

const status = computed<Status>(() => {
  if (recorder.isRecording.value) return 'recording'
  if (player.isPlaying.value) return 'speaking'
  if (pending.value > 0) return 'thinking'
  return 'idle'
})

const placeholder = computed(() => {
  if (status.value === 'recording') return 'Слушаю…'
  if (wake.enabled.value && !wake.blocked.value) return 'Скажи «Джарвис» или напиши…'
  return 'Написать…'
})

function syncWake(): void {
  if (status.value !== 'recording' && document.visibilityState === 'visible') wake.resume()
  else wake.pause()
}
watch(status, syncWake, { immediate: true })
document.addEventListener('visibilitychange', syncWake)

// --- озвучка ответов: по очереди, и только когда пользователь не говорит ---

interface Speech {
  more: SpeechMore
  done: () => void
}
const speechQueue: Speech[] = []
let draining = false
// Меняется при каждом «перебили» — недоигранный длинный ответ это видит.
let speechGeneration = 0

function speak(more: SpeechMore): Promise<void> {
  return new Promise((done) => {
    speechQueue.push({ more, done })
    void drainSpeech()
  })
}

async function drainSpeech(): Promise<void> {
  if (draining) return
  draining = true
  while (speechQueue.length && !recorder.isRecording.value) {
    const next = speechQueue.shift()!
    await playSpeech(next)
    next.done()
  }
  draining = false
}

// Куски озвучки по очереди: следующий качается, пока звучит текущий.
async function playSpeech({ more }: Speech): Promise<void> {
  const generation = speechGeneration
  let upcoming: Promise<Blob> | null = fetchSpeechChunk(more.id, 0)
  for (let n = 0; upcoming; n++) {
    let blob: Blob
    try {
      blob = await upcoming
    } catch {
      return
    }
    upcoming = n + 1 < more.count ? fetchSpeechChunk(more.id, n + 1) : null
    if (generation !== speechGeneration) return
    const url = URL.createObjectURL(blob)
    await player.playUrl(url)
    URL.revokeObjectURL(url)
  }
}

// Пользователь заговорил — Джарвис замолкает, недосказанное не досказывает.
function interrupt(): void {
  speechGeneration += 1
  for (const s of speechQueue.splice(0)) s.done()
  player.stop()
}

// Любой тап — шанс разблокировать звук на iOS (он разрешает только в жесте).
function unlockAudio(): void {
  player.unlock()
  music.unlock()
  earcon.unlock()
}

// Не только микрофон: «Слушать» в «Моей музыке», ♡, ▶ — любое касание экрана.
// Звук, который iOS заблокировал, запускается прямо в этом жесте.
document.addEventListener(
  'pointerdown',
  () => {
    unlockAudio()
    if (music.blocked.value) music.retry()
  },
  { capture: true },
)

function onWakeToggle(): void {
  unlockAudio()
  wake.setEnabled(wake.blocked.value || !wake.enabled.value)
}

// Одна кнопка: есть текст в поле — отправить его, нет — говорить голосом.
function onPress(): void {
  void (draft.value.trim() ? onSubmit() : onTalk())
}

async function onTalk(): Promise<void> {
  if (status.value === 'recording') {
    await finishRecording()
    return
  }
  // В любом другом состоянии — сразу слушать, как у Алисы.
  interrupt()
  unlockAudio()
  earcon.listen()
  // Музыка молчит от начала записи до конца ответа.
  music.hold()
  // Сигнал не должен попасть в калибровку шума автостопа.
  await new Promise((r) => setTimeout(r, 250))
  await startRecording()
}

// Музыка к этому моменту уже удержана (тап или «Джарвис»).
async function startRecording(): Promise<void> {
  wake.pause()
  try {
    await recorder.start({
      onEnd: () => void finishRecording(),
      onNoSpeech: () => void cancelRecording(),
    })
  } catch (e) {
    music.release()
    push('error', `Нет доступа к микрофону: ${errorText(e)}`)
  }
}

async function finishRecording(): Promise<void> {
  if (!recorder.isRecording.value) return
  const blob = await recorder.stop()
  void drainSpeech()
  await ask((turnId) => sendAudio(blob, turnId), null)
}

async function cancelRecording(): Promise<void> {
  if (!recorder.isRecording.value) return
  await recorder.stop()
  earcon.cancel()
  music.release()
  void drainSpeech()
}

async function onSubmit(): Promise<void> {
  const text = draft.value.trim()
  if (!text) return
  draft.value = ''
  interrupt()
  unlockAudio()
  music.hold()
  await ask((turnId) => sendText(text, true, turnId), text)
}

// Одна реплика. text=null — голос: текст придёт событием (расшифровка).
// Музыка удержана вызывающим; отпускается, когда ответ доозвучен (onTurn).
async function ask(request: (turnId: string) => Promise<void>, text: string | null): Promise<void> {
  const turnId = crypto.randomUUID()
  const entry = begin(turnId, push('user', text ?? '…'), true)
  try {
    await request(turnId)
    entry.accepted = true
  } catch (e) {
    // Итог мог успеть прийти событием раньше ошибки запроса.
    if (!settle(turnId)) return
    push('error', errorText(e))
    music.release()
  }
}

function push(role: Message['role'], text: string, tools?: string[]): Message {
  messages.value.push({ id: nextId++, role, text, tools })
  // Возвращаем реактивную копию — правки текста видны в ленте.
  return messages.value[messages.value.length - 1]
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}
</script>

<template>
  <main class="app">
    <header class="app__header">
      <span
        class="app__dot"
        :class="[`app__dot--${status}`, { 'app__dot--wake': status === 'idle' && wake.listening.value }]"
      />
      Джарвис
      <span class="app__spacer" />
      <button
        class="app__icon"
        aria-label="Моя музыка"
        title="Моя музыка: лайки, «Моя волна», место на сервере"
        @click="showLibrary = true"
      >
        <svg viewBox="0 0 24 24"><path d="M12 3v10.6A4 4 0 1 0 14 17V7h4V3h-6z" /></svg>
      </button>
      <button
        v-if="notifications.supported && !notifications.enabled.value"
        class="app__wake"
        :title="notifications.error.value ?? 'Сообщать о готовых задачах, когда Джарвис свёрнут'"
        @click="notifications.enable"
      >
        {{ notifications.error.value ? 'Уведомления: ошибка' : 'Уведомления' }}
      </button>
      <button
        v-if="wake.supported"
        class="app__wake"
        :class="{ 'app__wake--on': wake.enabled.value && !wake.blocked.value }"
        :title="wake.blocked.value ? 'Нажми, чтобы снова слушать «Джарвис»' : 'Откликаться на «Джарвис»'"
        @click="onWakeToggle"
      >
        {{ wake.blocked.value ? 'Нажми: «Джарвис»' : '«Джарвис»' }}
      </button>
    </header>


    <BackgroundTasks :tasks="backgroundTasks" @cancel="onCancel" />

    <ChatLog :messages="messages" @cancel="onCancel" />

    <PlayerBar
      v-if="musicMeta"
      :meta="musicMeta"
      :playing="musicPlaying"
      :loading="musicLoading"
      :live="musicLive"
      :position="musicPosition"
      :duration="musicDuration"
      :rateable="musicRef !== null"
      :rating="musicRating"
      :origin="musicOrigin"
      @open="showNowPlaying = true"
      @toggle="music.toggle"
      @next="music.next"
      @like="onLike"
    />

    <footer class="app__footer">
      <form class="app__form" @submit.prevent="onSubmit">
        <input
          v-model="draft"
          class="app__input"
          type="text"
          :placeholder="placeholder"
          enterkeyhint="send"
        />
        <TalkButton :status="status" :send="draft.trim().length > 0" @press="onPress" />
      </form>
    </footer>

    <NowPlaying
      v-if="showNowPlaying && musicMeta"
      :meta="musicMeta"
      :playing="musicPlaying"
      :loading="musicLoading"
      :live="musicLive"
      :position="musicPosition"
      :duration="musicDuration"
      :rateable="musicRef !== null"
      :rating="musicRating"
      :origin="musicOrigin"
      :from="musicFrom"
      @close="showNowPlaying = false"
      @toggle="music.toggle"
      @next="music.next"
      @previous="music.previous"
      @stop="onStop"
      @seek="music.seek"
      @seek-by="music.seekBy"
      @like="onLike"
      @dislike="onDislike"
    />
    <MyMusic v-if="showLibrary" @close="showLibrary = false" @played="(text) => showToast(text)" />
    <RateToast v-if="music.blocked.value && !toast" text="Коснись экрана, чтобы включить звук" :undo="false" />
    <RateToast v-if="toast" :text="toast.text" :undo="toast.undoRef !== null" @undo="onUndo" />
  </main>
</template>

<style scoped lang="scss">
.app {
  height: 100dvh;
  display: flex;
  flex-direction: column;
  max-width: 720px;
  margin: 0 auto;

  &__header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: calc(12px + env(safe-area-inset-top)) 16px 12px;
    font-weight: 600;
    letter-spacing: 0.02em;
    border-bottom: 1px solid var(--border);
    min-width: 0;
  }

  &__dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--accent);

    &--recording {
      background: var(--danger);
    }

    &--thinking {
      background: var(--warn);
    }

    // Ждёт «Джарвис» — медленно «дышит».
    &--wake {
      animation: wake 2.4s ease-in-out infinite;
    }
  }

  &__spacer {
    flex: 1;
  }

  &__icon {
    flex: none;
    width: 34px;
    height: 34px;
    display: grid;
    place-items: center;
    border-radius: 50%;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text);
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;

    svg {
      width: 18px;
      height: 18px;
      fill: currentColor;
    }
  }

  &__wake {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    padding: 6px 12px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-dim);
    font: inherit;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;

    &--on {
      border-color: var(--accent);
      color: var(--accent);
    }
  }

  &__footer {
    padding: 12px 16px calc(12px + env(safe-area-inset-bottom));
    border-top: 1px solid var(--border);
  }

  &__form {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  &__input {
    flex: 1;
    min-width: 0;
    padding: 12px 14px;
    border-radius: 14px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text);
    font: inherit;
    font-size: 16px; // < 16px — iOS зумит страницу при фокусе

    &:focus {
      outline: 2px solid var(--accent);
      outline-offset: -1px;
    }
  }
}

@keyframes wake {
  50% {
    opacity: 0.35;
  }
}
</style>
