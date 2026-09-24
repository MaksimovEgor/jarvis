<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

import { fetchQueue, queueMove, queuePlay, queueRemove } from '../api'
import type { QueueData } from '../types'
import CoverArt from './CoverArt.vue'

const props = defineProps<{
  // Меняется, когда ядро прислало новый «дальше» — перечитать очередь.
  version: string
}>()

const queue = ref<QueueData | null>(null)
const error = ref<string | null>(null)
const busy = ref(false)

async function load(): Promise<void> {
  try {
    queue.value = await fetchQueue()
    error.value = null
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function run(action: () => Promise<QueueData>): Promise<void> {
  if (busy.value) return
  busy.value = true
  try {
    queue.value = await action()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

onMounted(() => void load())
watch(() => props.version, () => void load())
</script>

<template>
  <div class="q">
    <p v-if="error" class="q__empty">Очередь не загрузилась: {{ error }}</p>
    <p v-else-if="queue && !queue.tracks.length" class="q__empty">Очередь пуста.</p>
    <ul v-else-if="queue" class="q__list">
      <li
        v-for="(t, i) in queue.tracks"
        v-show="i >= queue.pos"
        :key="`${t.ref}-${i}`"
        class="q__item"
        :class="{ 'q__item--now': i === queue.pos }"
      >
        <button class="q__main" :disabled="i === queue.pos" @click="run(() => queuePlay(i))">
          <CoverArt class="q__cover" :src="t.cover" :crop="t.coverCrop ?? true" />
          <span class="q__text">
            <span class="q__title">{{ t.song }}</span>
            <span class="q__sub">{{ i === queue.pos ? 'играет' : t.artist }}</span>
          </span>
        </button>
        <template v-if="i > queue.pos">
          <button class="q__btn" aria-label="Выше" :disabled="i === queue.pos + 1" @click="run(() => queueMove(i, i - 1))">▲</button>
          <button class="q__btn" aria-label="Ниже" :disabled="i === queue.tracks.length - 1" @click="run(() => queueMove(i, i + 1))">
            ▼
          </button>
          <button class="q__btn" aria-label="Убрать из очереди" @click="run(() => queueRemove(i))">✕</button>
        </template>
      </li>
    </ul>
  </div>
</template>

<style scoped lang="scss">
.q {
  flex: 1 1 auto;
  min-height: 0;
  margin-top: 16px;
  overflow-y: auto;
  scrollbar-width: none;

  &::-webkit-scrollbar {
    display: none;
  }

  &__list {
    margin: 0;
    padding: 0;
    list-style: none;
  }

  &__item {
    display: flex;
    align-items: center;
    gap: 2px;
    border-radius: 12px;

    &--now {
      background: rgb(255 255 255 / 8%);
    }
  }

  &__main {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 6px 8px;
    border: none;
    background: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }

  &__cover {
    flex: none;
    width: 42px;
    height: 42px;
    border-radius: 8px;
  }

  &__text {
    min-width: 0;
    display: flex;
    flex-direction: column;
  }

  &__title,
  &__sub {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__title {
    font-size: 15px;
  }

  &__sub {
    font-size: 12px;
    color: rgb(255 255 255 / 55%);
  }

  &__btn {
    flex: none;
    width: 34px;
    height: 34px;
    border: none;
    border-radius: 50%;
    background: none;
    color: rgb(255 255 255 / 70%);
    font-size: 13px;
    cursor: pointer;

    &:disabled {
      opacity: 0.25;
    }
  }

  &__empty {
    margin-top: 30%;
    text-align: center;
    color: rgb(255 255 255 / 55%);
  }
}
</style>
