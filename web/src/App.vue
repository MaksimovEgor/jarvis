<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { cancelTurn, fetchHistory, fetchSpeechChunk, sendAudio, sendText, type ChatReply, type SpeechMore } from './api'
import ChatLog from './components/ChatLog.vue'
import PlayerBar from './components/PlayerBar.vue'
import TalkButton from './components/TalkButton.vue'
import { useEarcon } from './composables/useEarcon'
import { useMusic } from './composables/useMusic'
import { usePlayer } from './composables/usePlayer'
import { useRecorder } from './composables/useRecorder'
import { useWakeWord } from './composables/useWakeWord'
import type { Message, Status, TurnEvent } from './types'

const recorder = useRecorder()
const player = usePlayer()
const earcon = useEarcon()
// Объявления таймеров играет голосовой плеер, музыка на это время молчит.
const music = useMusic((url) => player.playUrl(url), onTurn)
const {
  title: musicTitle,
  wantPlaying: musicPlaying,
  isLoading: musicLoading,
  live: musicLive,
  position: musicPosition,
  duration: musicDuration,
} = music

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

// Прогресс просьбы: какой инструмент сейчас работает.
function onTurn(event: TurnEvent): void {
  const msg = messages.value.find((m) => m.pending?.turnId === event.id)
  if (!msg?.pending) return
  if (event.cancelled) {
    msg.pending = undefined
    msg.cancelled = true
  } else if (event.tool) {
    msg.pending.tool = event.tool
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
  audio: string
  more?: SpeechMore | null
  done: () => void
}
const speechQueue: Speech[] = []
let draining = false
// Меняется при каждом «перебили» — недоигранный длинный ответ это видит.
let speechGeneration = 0

function speak(audio: string, more?: SpeechMore | null): Promise<void> {
  return new Promise((done) => {
    speechQueue.push({ audio, more, done })
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

// Длинный ответ: первый кусок сразу, следующий качается, пока звучит текущий.
async function playSpeech({ audio, more }: Speech): Promise<void> {
  const generation = speechGeneration
  let upcoming = more && more.count > 1 ? fetchSpeechChunk(more.id, 1) : null
  await player.play(audio)
  for (let n = 1; more && upcoming && n < more.count; n++) {
    if (generation !== speechGeneration) return
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

// Одна реплика. text=null — голос: текст придёт с ответом (расшифровка).
// Музыка удержана вызывающим; отпускается, когда ответ доозвучен.
async function ask(request: (turnId: string) => Promise<ChatReply>, text: string | null): Promise<void> {
  const turnId = crypto.randomUUID()
  const mine = push('user', text ?? '…')
  mine.pending = { turnId, since: Date.now() }
  pending.value += 1
  try {
    const res = await request(turnId)
    mine.pending = undefined
    if (res.transcript !== undefined) mine.text = res.transcript || '(не расслышал)'
    if (res.cancelled) {
      mine.cancelled = true
      return
    }
    push('assistant', res.reply, res.tool_calls)
    if (res.audio_base64) {
      pending.value -= 1
      await speak(res.audio_base64, res.audio_more)
      pending.value += 1
    }
  } catch (e) {
    push('error', errorText(e))
  } finally {
    mine.pending = undefined
    pending.value -= 1
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

    <PlayerBar
      v-if="musicTitle !== null"
      :title="musicTitle"
      :playing="musicPlaying"
      :loading="musicLoading"
      :live="musicLive"
      :position="musicPosition"
      :duration="musicDuration"
      @toggle="music.toggle"
      @next="music.next"
      @previous="music.previous"
      @stop="music.stop"
      @seek="music.seek"
    />

    <ChatLog :messages="messages" @cancel="onCancel" />

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

  &__wake {
    margin-left: auto;
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
