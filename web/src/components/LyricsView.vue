<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import { fetchLyrics } from '../api'
import type { LyricsData } from '../types'

const props = defineProps<{
  trackRef: string | null
  position: number
}>()

const emit = defineEmits<{
  seek: [position: number]
}>()

// Ручная прокрутка — автопрокрутка ждёт столько, как в Яндекс Музыке.
const USER_SCROLL_PAUSE_MS = 4000

const data = ref<LyricsData | null>(null)
const loading = ref(false)
const failed = ref(false)
const box = ref<HTMLElement | null>(null)
let userScrollAt = 0
let programmatic = false

watch(
  () => props.trackRef,
  async (trackRef) => {
    data.value = null
    failed.value = false
    if (!trackRef) return
    loading.value = true
    try {
      const found = await fetchLyrics(trackRef)
      if (props.trackRef === trackRef) data.value = found
    } catch {
      failed.value = true
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

const lines = computed(() => data.value?.synced ?? [])

// Текущая строка — последняя с t ≤ позиции (двоичный поиск: строк сотни, позиция тикает часто).
const active = computed(() => {
  const list = lines.value
  let lo = 0
  let hi = list.length - 1
  let found = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (list[mid].t <= props.position + 0.25) {
      found = mid
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  return found
})

watch(active, async (index) => {
  if (index < 0 || Date.now() - userScrollAt < USER_SCROLL_PAUSE_MS) return
  await nextTick()
  const el = box.value?.querySelector<HTMLElement>(`[data-i="${index}"]`)
  if (!el || !box.value) return
  programmatic = true
  box.value.scrollTo({ top: el.offsetTop - box.value.clientHeight * 0.35, behavior: 'smooth' })
  window.setTimeout(() => (programmatic = false), 600)
})

function onScroll(): void {
  if (!programmatic) userScrollAt = Date.now()
}

function onTap(t: number): void {
  userScrollAt = 0
  emit('seek', t)
}

const sourceLabel = computed(() => {
  const source = data.value?.source
  if (!source) return ''
  const name = source === 'yandex' ? 'Яндекс Музыка' : 'LRCLIB'
  return data.value?.synced ? `Текст: ${name} · тап по строке — перемотка` : `Текст: ${name} · без синхронизации`
})
</script>

<template>
  <div class="ly">
    <div ref="box" class="ly__box" @scroll.passive="onScroll">
      <p v-if="loading" class="ly__empty">Ищу текст…</p>
      <p v-else-if="failed" class="ly__empty">Не получилось загрузить текст.</p>
      <template v-else-if="lines.length">
        <button
          v-for="(l, i) in lines"
          :key="i"
          :data-i="i"
          class="ly__line"
          :class="{ 'ly__line--now': i === active, 'ly__line--past': i < active }"
          @click="onTap(l.t)"
        >
          {{ l.line || '♪' }}
        </button>
        <div class="ly__tail" />
      </template>
      <pre v-else-if="data?.plain" class="ly__plain">{{ data.plain }}</pre>
      <p v-else-if="data" class="ly__empty">Текста нет — ни у Яндекса, ни в LRCLIB.</p>
    </div>
    <p v-if="sourceLabel" class="ly__source">{{ sourceLabel }}</p>
  </div>
</template>

<style scoped lang="scss">
.ly {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  margin-top: 16px;

  &__box {
    position: relative;
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    scrollbar-width: none;
    mask-image: linear-gradient(transparent, #000 10%, #000 82%, transparent);

    &::-webkit-scrollbar {
      display: none;
    }
  }

  &__line {
    display: block;
    width: 100%;
    margin: 0 0 16px;
    padding: 0;
    border: none;
    background: none;
    color: rgb(255 255 255 / 32%);
    font: inherit;
    font-size: clamp(20px, 6.2vw, 27px);
    font-weight: 700;
    line-height: 1.3;
    text-align: left;
    cursor: pointer;
    transition: color 0.3s, transform 0.3s;
    transform-origin: left center;

    &:first-child {
      margin-top: 30%;
    }

    &--past {
      color: rgb(255 255 255 / 48%);
    }

    &--now {
      color: #fff;
      transform: scale(1.04);
    }
  }

  &__tail {
    height: 45%;
  }

  &__plain {
    margin: 12px 0 40px;
    white-space: pre-wrap;
    font: inherit;
    font-size: 18px;
    line-height: 1.55;
    color: #e7ecf2;
  }

  &__empty {
    margin-top: 30%;
    text-align: center;
    color: rgb(255 255 255 / 55%);
  }

  &__source {
    margin: 6px 0 0;
    text-align: center;
    font-size: 11px;
    color: rgb(255 255 255 / 45%);
  }
}
</style>
