<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { sendAudio, sendText, type ChatReply } from './api'
import ChatLog from './components/ChatLog.vue'
import TalkButton from './components/TalkButton.vue'
import { usePlayer } from './composables/usePlayer'
import { useRecorder } from './composables/useRecorder'
import type { Message, Status } from './types'

const SPEAK_KEY = 'jarvis:speakText'

const recorder = useRecorder()
const player = usePlayer()

const messages = ref<Message[]>([])
const isThinking = ref(false)
const draft = ref('')
const speakText = ref(readSpeakPref())

let nextId = 1

const status = computed<Status>(() => {
  if (recorder.isRecording.value) return 'recording'
  if (isThinking.value) return 'thinking'
  if (player.isPlaying.value) return 'speaking'
  return 'idle'
})

watch(speakText, (v) => {
  try {
    localStorage.setItem(SPEAK_KEY, v ? '1' : '0')
  } catch {
    // приватный режим — настройка просто не запомнится
  }
})

async function onTalk(): Promise<void> {
  if (status.value === 'speaking') {
    player.stop()
    return
  }
  if (status.value === 'recording') {
    const blob = await recorder.stop()
    await ask(() => sendAudio(blob))
    return
  }
  player.unlock()
  try {
    await recorder.start()
  } catch (e) {
    push('error', `Нет доступа к микрофону: ${errorText(e)}`)
  }
}

async function onSubmit(): Promise<void> {
  const text = draft.value.trim()
  if (!text || isThinking.value) return
  draft.value = ''
  if (speakText.value) player.unlock()
  push('user', text)
  await ask(() => sendText(text, speakText.value))
}

async function ask(request: () => Promise<ChatReply>): Promise<void> {
  isThinking.value = true
  try {
    const res = await request()
    if (res.transcript !== undefined) {
      push('user', res.transcript || '(не расслышал)')
    }
    push('assistant', res.reply, res.tool_calls)
    if (res.audio_base64) {
      await player.play(res.audio_base64)
    }
  } catch (e) {
    push('error', errorText(e))
  } finally {
    isThinking.value = false
  }
}

function push(role: Message['role'], text: string, tools?: string[]): void {
  messages.value.push({ id: nextId++, role, text, tools })
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

function readSpeakPref(): boolean {
  try {
    return localStorage.getItem(SPEAK_KEY) === '1'
  } catch {
    return false
  }
}
</script>

<template>
  <main class="app">
    <header class="app__header">
      <span class="app__dot" :class="`app__dot--${status}`" />
      Джарвис
    </header>

    <ChatLog :messages="messages" />

    <footer class="app__footer">
      <TalkButton :status="status" @press="onTalk" />

      <form class="app__form" @submit.prevent="onSubmit">
        <input
          v-model="draft"
          class="app__input"
          type="text"
          placeholder="Написать…"
          enterkeyhint="send"
          :disabled="isThinking"
        />
        <label class="app__speak" title="Озвучивать ответы на текст">
          <input v-model="speakText" type="checkbox" />
          🔊
        </label>
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
  }

  &__footer {
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 14px 16px calc(14px + env(safe-area-inset-bottom));
    border-top: 1px solid var(--border);
  }

  &__form {
    display: flex;
    gap: 8px;
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

  &__speak {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 0 10px;
    border-radius: 14px;
    background: var(--surface);
    cursor: pointer;

    input {
      accent-color: var(--accent);
    }
  }
}
</style>
