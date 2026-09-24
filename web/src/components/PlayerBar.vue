<script setup lang="ts">
import { computed } from 'vue'

import type { FromWhere, Rating } from '../types'

const props = defineProps<{
  title: string
  playing: boolean
  loading: boolean
  live: boolean
  position: number
  duration: number
  // Оценивать можно только треки YouTube, у радио и файлов кнопок нет.
  rateable: boolean
  rating: Rating
  origin: string | null
  from: FromWhere | null
}>()

const emit = defineEmits<{
  toggle: []
  next: []
  previous: []
  stop: []
  seek: [position: number]
  like: []
  dislike: []
}>()

const FROM_LABEL: Record<FromWhere, string> = { liked: '♥ сохранено', cache: 'кэш', net: 'сеть', stream: 'поток' }

const source = computed(() => (props.from ? FROM_LABEL[props.from] : null))

const progress = computed(() => (props.duration ? Math.min(1, props.position / props.duration) : 0))

const time = computed(() => {
  if (props.live) return 'эфир'
  return props.duration ? `${clock(props.position)} / ${clock(props.duration)}` : clock(props.position)
})

function clock(seconds: number): string {
  const s = Math.floor(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

function onSeek(e: MouseEvent): void {
  if (props.live || !props.duration) return
  const bar = e.currentTarget as HTMLElement
  const { left, width } = bar.getBoundingClientRect()
  emit('seek', ((e.clientX - left) / width) * props.duration)
}
</script>

<template>
  <section class="bar" :class="{ 'bar--loading': loading }" aria-label="Плеер">
    <div class="bar__row">
      <div class="bar__info">
        <p class="bar__title">{{ title }}</p>
        <p class="bar__meta">
          <span class="bar__time">{{ loading ? 'загрузка…' : time }}</span>
          <span v-if="origin" class="bar__chip bar__chip--accent">{{ origin }}</span>
          <span v-if="source" class="bar__chip">{{ source }}</span>
        </p>
      </div>

      <div class="bar__controls">
        <template v-if="rateable">
          <button class="bar__btn bar__btn--rate" aria-label="Не нравится" @click="emit('dislike')">
            <svg viewBox="0 0 24 24">
              <path
                d="M15 3H6c-.8 0-1.5.5-1.8 1.2l-3 7.1c-.1.2-.2.5-.2.7v2c0 1.1.9 2 2 2h6.3l-.9 4.6v.3c0 .4.2.8.4 1.1L9.8 23l6.6-6.6c.4-.4.6-.9.6-1.4V5c0-1.1-.9-2-2-2zm4 0v12h4V3h-4z"
              />
            </svg>
          </button>
          <button
            class="bar__btn bar__btn--rate"
            :class="{ 'bar__btn--liked': rating === 1 }"
            :aria-label="rating === 1 ? 'Убрать из моей музыки' : 'Нравится'"
            :aria-pressed="rating === 1"
            @click="emit('like')"
          >
            <svg v-if="rating === 1" viewBox="0 0 24 24">
              <path d="M12 21.4l-1.5-1.3C5.4 15.4 2 12.3 2 8.5 2 5.4 4.4 3 7.5 3c1.7 0 3.4.8 4.5 2.1C13.1 3.8 14.8 3 16.5 3 19.6 3 22 5.4 22 8.5c0 3.8-3.4 6.9-8.5 11.5L12 21.4z" />
            </svg>
            <svg v-else viewBox="0 0 24 24">
              <path
                d="M16.5 3c-1.7 0-3.4.8-4.5 2.1C10.9 3.8 9.2 3 7.5 3 4.4 3 2 5.4 2 8.5c0 3.8 3.4 6.9 8.5 11.5l1.5 1.3 1.5-1.3C18.6 15.4 22 12.3 22 8.5 22 5.4 19.6 3 16.5 3zm-4.4 15.6l-.1.1-.1-.1C7.1 14.2 4 11.4 4 8.5 4 6.5 5.5 5 7.5 5c1.5 0 3 1 3.6 2.4h1.9C13.5 6 15 5 16.5 5c2 0 3.5 1.5 3.5 3.5 0 2.9-3.1 5.7-7.9 10.1z"
              />
            </svg>
          </button>
        </template>
        <button class="bar__btn" aria-label="Предыдущий" @click="emit('previous')">
          <svg viewBox="0 0 24 24"><path d="M7 6h2v12H7zM20 6v12l-9-6z" /></svg>
        </button>
        <button class="bar__btn bar__btn--main" :aria-label="playing ? 'Пауза' : 'Играть'" @click="emit('toggle')">
          <svg v-if="playing" viewBox="0 0 24 24"><path d="M7 5h4v14H7zM13 5h4v14h-4z" /></svg>
          <svg v-else viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
        </button>
        <button class="bar__btn" aria-label="Следующий" @click="emit('next')">
          <svg viewBox="0 0 24 24"><path d="M15 6h2v12h-2zM4 6v12l9-6z" /></svg>
        </button>
        <button class="bar__btn bar__btn--close" aria-label="Выключить" @click="emit('stop')">
          <svg viewBox="0 0 24 24">
            <path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
        </button>
      </div>
    </div>

    <div v-if="!live" class="bar__track" @click="onSeek">
      <div class="bar__fill" :style="{ transform: `scaleX(${progress})` }" />
    </div>
  </section>
</template>

<style scoped lang="scss">
.bar {
  position: relative;
  z-index: 1;
  padding: 8px 12px 10px 16px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);

  &__row {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  &__info {
    flex: 1;
    min-width: 0;
  }

  &__title {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__meta {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 2px 0 0;
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
  }

  &__time {
    font-size: 12px;
    color: var(--text-dim);
    font-variant-numeric: tabular-nums;
  }

  &__chip {
    flex: none;
    padding: 1px 6px;
    border-radius: 8px;
    background: var(--surface-2);
    color: var(--text-dim);
    font-size: 10px;
    line-height: 16px;

    &--accent {
      color: var(--accent);
    }
  }

  &__controls {
    display: flex;
    align-items: center;
    gap: 2px;
  }

  &__btn {
    width: 40px;
    height: 40px;
    display: grid;
    place-items: center;
    border: none;
    border-radius: 50%;
    background: transparent;
    color: var(--text);
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;

    svg {
      width: 22px;
      height: 22px;
      fill: currentColor;
    }

    &:active {
      background: var(--surface-2);
    }

    &--main {
      background: var(--accent);
      color: var(--bg);

      &:active {
        background: var(--accent);
        opacity: 0.8;
      }
    }

    &--rate svg {
      width: 20px;
      height: 20px;
    }

    &--liked {
      color: var(--danger);
    }

    &--close {
      color: var(--text-dim);

      svg {
        width: 18px;
        height: 18px;
      }
    }
  }

  // Высокая невидимая зона клика — по тонкой полоске пальцем не попасть.
  // Узкий телефон: 6 кнопок съедали название трека.
  @media (max-width: 420px) {
    padding-right: 6px;

    &__btn {
      width: 34px;
    }

    &__controls {
      gap: 0;
    }
  }

  &__track {
    position: absolute;
    left: 0;
    right: 0;
    bottom: -8px;
    height: 16px;
    cursor: pointer;

    &::before {
      content: '';
      position: absolute;
      left: 0;
      right: 0;
      top: 7px;
      height: 2px;
      background: var(--surface-2);
    }
  }

  &__fill {
    position: absolute;
    left: 0;
    right: 0;
    top: 7px;
    height: 2px;
    background: var(--accent);
    transform-origin: left;
  }

  &--loading &__fill {
    opacity: 0.5;
  }
}
</style>
