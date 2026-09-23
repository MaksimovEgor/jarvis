<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  title: string
  playing: boolean
  loading: boolean
  live: boolean
  position: number
  duration: number
}>()

const emit = defineEmits<{
  toggle: []
  next: []
  previous: []
  stop: []
  seek: [position: number]
}>()

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
        <p class="bar__time">{{ loading ? 'загрузка…' : time }}</p>
      </div>

      <div class="bar__controls">
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

  &__time {
    margin: 2px 0 0;
    font-size: 12px;
    color: var(--text-dim);
    font-variant-numeric: tabular-nums;
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

    &--close {
      color: var(--text-dim);

      svg {
        width: 18px;
        height: 18px;
      }
    }
  }

  // Высокая невидимая зона клика — по тонкой полоске пальцем не попасть.
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
