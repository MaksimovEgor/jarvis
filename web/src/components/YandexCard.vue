<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { yandexConnect, yandexDisconnect, yandexStatus } from '../api'
import type { YandexStatus } from '../types'

const POLL_MS = 3000

const status = ref<YandexStatus | null>(null)
const busy = ref(false)
const error = ref<string | null>(null)
let timer: number | null = null

// Код набирается на другой вкладке — дефис в середине, как у Яндекса.
const code = computed(() => {
  const raw = status.value?.code ?? ''
  return raw.length === 6 ? `${raw.slice(0, 3)}-${raw.slice(3)}` : raw
})

async function refresh(): Promise<void> {
  try {
    status.value = await yandexStatus()
    error.value = null
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
  // Пока ждём подтверждения на ya.ru/device — спрашиваем чаще.
  if (status.value?.state === 'pending') schedule()
}

function schedule(): void {
  if (timer !== null) clearTimeout(timer)
  timer = window.setTimeout(() => void refresh(), POLL_MS)
}

async function connect(): Promise<void> {
  busy.value = true
  try {
    status.value = await yandexConnect()
    schedule()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

async function disconnect(): Promise<void> {
  busy.value = true
  await yandexDisconnect().catch(() => undefined)
  busy.value = false
  await refresh()
}

async function copyCode(): Promise<void> {
  await navigator.clipboard?.writeText(status.value?.code ?? '').catch(() => undefined)
}

onMounted(() => void refresh())
onUnmounted(() => timer !== null && clearTimeout(timer))
</script>

<template>
  <div v-if="status" class="ya">
    <div class="ya__logo">Я</div>

    <div v-if="status.state === 'on'" class="ya__body">
      <h3>Яндекс Музыка подключена</h3>
      <p class="ya__ok">{{ status.login ?? 'аккаунт' }} · {{ status.plus ? 'Плюс активен' : 'без Плюса' }}</p>
      <p>Лайки синхронизируются, волна учится на Яндексе. Чего нет в Яндексе — найду на YouTube.</p>
      <button class="ya__link" :disabled="busy" @click="disconnect">Отключить</button>
    </div>

    <div v-else-if="status.state === 'pending'" class="ya__body">
      <h3>Введи код на ya.ru/device</h3>
      <button class="ya__code" title="Скопировать" @click="copyCode">{{ code }}</button>
      <p>Войди в аккаунт с Плюсом и введи код. Жду подтверждения…</p>
      <a class="ya__btn" :href="status.url ?? 'https://ya.ru/device'" target="_blank" rel="noopener" @click="copyCode">
        Открыть ya.ru/device
      </a>
    </div>

    <div v-else class="ya__body">
      <h3>{{ status.state === 'broken' ? 'Яндекс отключился' : 'Яндекс Музыка' }}</h3>
      <p>
        {{
          status.state === 'broken'
            ? 'Токен больше не действует — играю с YouTube. Переподключи, чтобы вернуть качество и волну Яндекса.'
            : 'Подключи Плюс — песни в FLAC и 320 кбит/с и «Моя волна» Яндекса. Чего нет в Яндексе — найду на YouTube.'
        }}
      </p>
      <button class="ya__btn" :disabled="busy" @click="connect">{{ busy ? 'Минуту…' : 'Подключить' }}</button>
      <p v-if="error" class="ya__err">{{ error }}</p>
    </div>
  </div>
</template>

<style scoped lang="scss">
.ya {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  margin: 12px 16px 0;
  padding: 16px;
  border-radius: 18px;
  background: var(--surface);

  &__logo {
    flex: none;
    width: 44px;
    height: 44px;
    display: grid;
    place-items: center;
    border-radius: 12px;
    background: #ffdb4d;
    color: #000;
    font-weight: 800;
    font-size: 20px;
  }

  &__body {
    flex: 1;
    min-width: 0;

    h3 {
      margin: 0 0 4px;
      font-size: 16px;
    }

    p {
      margin: 0 0 12px;
      color: var(--text-dim);
      font-size: 13px;
      line-height: 1.4;
    }
  }

  &__ok {
    color: var(--accent) !important;
    margin-bottom: 6px !important;
  }

  &__code {
    display: block;
    margin: 6px 0 8px;
    padding: 0;
    border: none;
    background: none;
    color: var(--text);
    font: inherit;
    font-size: 30px;
    font-weight: 700;
    letter-spacing: 0.16em;
    font-variant-numeric: tabular-nums;
    cursor: copy;
  }

  &__btn {
    display: inline-block;
    padding: 9px 16px;
    border: none;
    border-radius: 18px;
    background: #ffdb4d;
    color: #000;
    font: inherit;
    font-size: 14px;
    font-weight: 600;
    text-decoration: none;
    cursor: pointer;

    &:disabled {
      opacity: 0.6;
    }
  }

  &__link {
    padding: 0;
    border: none;
    background: none;
    color: var(--text-dim);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
  }

  &__err {
    margin-top: 8px !important;
    color: var(--danger) !important;
  }
}
</style>
