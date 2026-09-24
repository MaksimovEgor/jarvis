<script setup lang="ts">
import { computed, toRef } from 'vue'

import { useCoverColor } from '../composables/useCoverColor'
import type { Rating, TrackMeta } from '../types'
import CoverArt from './CoverArt.vue'

const props = defineProps<{
  meta: TrackMeta
  playing: boolean
  loading: boolean
  live: boolean
  position: number
  duration: number
  // Оценивать можно только треки YouTube, у радио и файлов сердечка нет.
  rateable: boolean
  rating: Rating
  origin: string | null
}>()

const emit = defineEmits<{
  open: []
  toggle: []
  next: []
  like: []
}>()

const color = useCoverColor(toRef(() => props.meta.cover))

const progress = computed(() => (props.duration ? Math.min(1, props.position / props.duration) : 0))

const subtitle = computed(() => {
  if (props.loading) return 'загрузка…'
  return [props.meta.artist, props.origin ?? (props.live ? 'эфир' : null)].filter(Boolean).join(' · ')
})
</script>

<template>
  <section
    class="mini"
    :style="{ '--tint': color ?? 'var(--surface)' }"
    aria-label="Плеер, открыть «Сейчас играет»"
    @click="emit('open')"
  >
    <CoverArt class="mini__cover" :src="meta.cover" :crop="meta.coverCrop" :live="live" />
    <div class="mini__info">
      <p class="mini__title">{{ meta.song }}</p>
      <p class="mini__sub">{{ subtitle }}</p>
    </div>
    <button
      v-if="rateable"
      class="mini__btn"
      :class="{ 'mini__btn--liked': rating === 1 }"
      :aria-label="rating === 1 ? 'Убрать из моей музыки' : 'Нравится'"
      @click.stop="emit('like')"
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
    <button class="mini__btn" :aria-label="playing ? 'Пауза' : 'Играть'" @click.stop="emit('toggle')">
      <svg v-if="playing" viewBox="0 0 24 24"><path d="M7 5h4v14H7zM13 5h4v14h-4z" /></svg>
      <svg v-else viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
    </button>
    <button class="mini__btn" aria-label="Следующий" @click.stop="emit('next')">
      <svg viewBox="0 0 24 24"><path d="M15 6h2v12h-2zM4 6v12l9-6z" /></svg>
    </button>
    <div v-if="!live" class="mini__progress" :style="{ transform: `scaleX(${progress})` }" />
  </section>
</template>

<style scoped lang="scss">
.mini {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 8px 8px;
  padding: 8px 6px 8px 8px;
  overflow: hidden;
  border-radius: 16px;
  background: linear-gradient(90deg, var(--tint), var(--surface) 75%);
  box-shadow: 0 8px 24px rgb(0 0 0 / 25%);
  cursor: pointer;
  transition: background 0.4s;
  -webkit-tap-highlight-color: transparent;

  &__cover {
    flex: none;
    width: 48px;
    height: 48px;
    border-radius: 10px;
  }

  &__info {
    flex: 1;
    min-width: 0;
  }

  &__title,
  &__sub {
    margin: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__title {
    font-size: 14px;
    font-weight: 600;
    color: #fff;
  }

  &__sub {
    margin-top: 2px;
    font-size: 12px;
    color: rgb(255 255 255 / 70%);
  }

  &__btn {
    flex: none;
    width: 38px;
    height: 38px;
    display: grid;
    place-items: center;
    border: none;
    border-radius: 50%;
    background: transparent;
    color: #fff;
    cursor: pointer;

    svg {
      width: 22px;
      height: 22px;
      fill: currentColor;
    }

    &--liked {
      color: var(--danger);
    }
  }

  &__progress {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: 2px;
    background: #fff;
    transform-origin: left;
  }
}

// Светлая тема: фон всё равно тёмный от обложки — текст остаётся белым.
@media (prefers-color-scheme: light) {
  .mini {
    background: linear-gradient(90deg, var(--tint), #2a323d 75%);
  }
}
</style>
