<script setup lang="ts">
import { computed, ref, toRef } from 'vue'

import { useCoverColor } from '../composables/useCoverColor'
import type { FromWhere, Rating, TrackMeta } from '../types'
import CoverArt from './CoverArt.vue'

const props = defineProps<{
  meta: TrackMeta
  playing: boolean
  loading: boolean
  live: boolean
  position: number
  duration: number
  rateable: boolean
  rating: Rating
  origin: string | null
  from: FromWhere | null
}>()

const emit = defineEmits<{
  close: []
  toggle: []
  next: []
  previous: []
  stop: []
  seek: [position: number]
  seekBy: [delta: number]
  like: []
  dislike: []
}>()

const FROM_LABEL: Record<FromWhere, string> = { liked: 'из «Моей музыки»', cache: 'из кэша', net: 'из сети', stream: 'поток' }

const color = useCoverColor(toRef(() => props.meta.cover))

const progress = computed(() => (props.duration ? Math.min(1, props.position / props.duration) : 0))

const badges = computed(() => {
  const list: string[] = []
  if (props.meta.service) list.push(props.meta.service)
  if (props.meta.codec) list.push(props.meta.bitrate ? `${props.meta.codec} · ${props.meta.bitrate} кбит/с` : props.meta.codec)
  if (props.from) list.push(FROM_LABEL[props.from])
  return list
})

function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

function onSeek(e: MouseEvent): void {
  if (props.live || !props.duration) return
  const { left, width } = (e.currentTarget as HTMLElement).getBoundingClientRect()
  emit('seek', ((e.clientX - left) / width) * props.duration)
}

// Свайп вниз закрывает экран, как в Яндекс Музыке.
const dragY = ref(0)
let startY: number | null = null

function onTouchStart(e: TouchEvent): void {
  startY = e.touches[0].clientY
}

function onTouchMove(e: TouchEvent): void {
  if (startY === null) return
  dragY.value = Math.max(0, e.touches[0].clientY - startY)
}

function onTouchEnd(): void {
  if (dragY.value > 110) emit('close')
  dragY.value = 0
  startY = null
}
</script>

<template>
  <section
    class="np"
    :style="{ '--tint': color ?? 'var(--surface-2)', transform: dragY ? `translateY(${dragY}px)` : undefined }"
    aria-label="Сейчас играет"
    @touchstart.passive="onTouchStart"
    @touchmove.passive="onTouchMove"
    @touchend="onTouchEnd"
  >
    <div class="np__bg" :style="meta.cover ? { backgroundImage: `url(${meta.cover})` } : undefined" />

    <header class="np__top">
      <button class="np__icon" aria-label="Свернуть" @click="emit('close')">
        <svg viewBox="0 0 24 24"><path d="M7.4 8.6L12 13.2l4.6-4.6L18 10l-6 6-6-6z" /></svg>
      </button>
      <div class="np__source">
        <small>Играет</small>
        {{ origin ?? meta.service ?? 'Музыка' }}
      </div>
      <button class="np__icon" aria-label="Выключить" @click="emit('stop')">
        <svg viewBox="0 0 24 24">
          <path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
        </svg>
      </button>
    </header>

    <CoverArt class="np__cover" :src="meta.cover" :live="live" />

    <div class="np__meta">
      <button v-if="rateable" class="np__round" aria-label="Не нравится" @click="emit('dislike')">
        <svg viewBox="0 0 24 24">
          <path d="M15 3H6c-.8 0-1.5.5-1.8 1.2l-3 7.1c-.1.2-.2.5-.2.7v2c0 1.1.9 2 2 2h6.3l-.9 4.6v.3c0 .4.2.8.4 1.1L9.8 23l6.6-6.6c.4-.4.6-.9.6-1.4V5c0-1.1-.9-2-2-2zm4 0v12h4V3h-4z" />
        </svg>
      </button>
      <div class="np__titles">
        <h1>{{ meta.song }}</h1>
        <p>{{ meta.artist ?? (live ? 'прямой эфир' : ' ') }}</p>
      </div>
      <button
        v-if="rateable"
        class="np__round"
        :class="{ 'np__round--liked': rating === 1 }"
        :aria-label="rating === 1 ? 'Убрать из моей музыки' : 'Нравится'"
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
    </div>

    <div class="np__badges">
      <span v-if="rating === 1" class="np__badge np__badge--accent">♥ в «Моей музыке»</span>
      <span v-for="b in badges" :key="b" class="np__badge">{{ b }}</span>
    </div>

    <template v-if="!live">
      <div class="np__bar" @click="onSeek">
        <i :style="{ width: `${progress * 100}%` }" />
      </div>
      <div class="np__times">
        <span>{{ clock(position) }}</span>
        <span>{{ loading ? 'загрузка…' : duration ? `−${clock(duration - position)}` : '' }}</span>
      </div>
    </template>

    <div class="np__controls">
      <button class="np__ctl np__ctl--small" :disabled="live" aria-label="Назад на 15 секунд" @click="emit('seekBy', -15)">
        ⤺15
      </button>
      <button class="np__ctl" aria-label="Предыдущий" @click="emit('previous')">
        <svg viewBox="0 0 24 24"><path d="M6 6h2v12H6zM20 6v12l-9-6z" /></svg>
      </button>
      <button class="np__play" :aria-label="playing ? 'Пауза' : 'Играть'" @click="emit('toggle')">
        <svg v-if="playing" viewBox="0 0 24 24"><path d="M7 5h4v14H7zM13 5h4v14h-4z" /></svg>
        <svg v-else viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
      </button>
      <button class="np__ctl" aria-label="Следующий" @click="emit('next')">
        <svg viewBox="0 0 24 24"><path d="M16 6h2v12h-2zM4 6v12l9-6z" /></svg>
      </button>
      <button class="np__ctl np__ctl--small" :disabled="live" aria-label="Вперёд на 30 секунд" @click="emit('seekBy', 30)">
        30⤻
      </button>
    </div>

    <div v-if="meta.upcoming.length" class="np__next">
      <b>{{ origin?.startsWith('волна') ? 'Дальше в волне' : 'Дальше' }}</b>
      <div v-for="t in meta.upcoming" :key="t.ref" class="np__row">
        <CoverArt class="np__thumb" :src="t.cover" />
        <span class="np__row-text">
          {{ t.song }}<small v-if="t.artist"> · {{ t.artist }}</small>
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped lang="scss">
.np {
  position: fixed;
  inset: 0;
  z-index: 15;
  display: flex;
  flex-direction: column;
  max-width: 720px;
  margin: 0 auto;
  padding: calc(8px + env(safe-area-inset-top)) 24px calc(20px + env(safe-area-inset-bottom));
  // Всё на одном экране, без прокрутки: сжимаются обложка и отступы,
  // на низких экранах прячутся строки «Дальше».
  overflow: hidden;
  overscroll-behavior: none;
  touch-action: pan-x;
  background: #0b0f14;
  color: #e7ecf2;
  transition: transform 0.15s;

  // Фон — размытая обложка и затемнение книзу.
  &__bg {
    position: absolute;
    inset: -60px;
    z-index: -2;
    background: var(--tint) center / cover;
    filter: blur(60px) saturate(1.4);
    opacity: 0.55;
  }

  &::before {
    content: '';
    position: absolute;
    inset: 0;
    z-index: -1;
    background: linear-gradient(180deg, rgb(11 15 20 / 30%), rgb(11 15 20 / 92%) 70%);
  }

  &__top {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  &__icon {
    width: 40px;
    height: 40px;
    display: grid;
    place-items: center;
    border: none;
    background: none;
    color: inherit;
    cursor: pointer;

    svg {
      width: 26px;
      height: 26px;
      fill: currentColor;
    }
  }

  &__source {
    min-width: 0;
    text-align: center;
    font-size: 13px;
    line-height: 1.3;
    color: #d6dde5;

    small {
      display: block;
      font-size: 11px;
      color: #8a96a3;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
  }

  &__cover {
    flex: none;
    width: min(100%, 360px, 40dvh);
    aspect-ratio: 1;
    margin: clamp(8px, 2.5dvh, 24px) auto 0;
    border-radius: 18px;
    box-shadow: 0 24px 60px rgb(0 0 0 / 55%);
  }

  &__meta {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: clamp(12px, 3dvh, 28px);
  }

  &__titles {
    flex: 1;
    min-width: 0;
    text-align: center;

    h1 {
      margin: 0;
      font-size: 22px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    p {
      margin: 4px 0 0;
      font-size: 16px;
      color: #c7cfd8;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
  }

  &__round {
    flex: none;
    width: 44px;
    height: 44px;
    display: grid;
    place-items: center;
    border: none;
    border-radius: 50%;
    background: rgb(255 255 255 / 8%);
    color: inherit;
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

  &__badges {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 6px;
    min-height: 22px;
    margin-top: clamp(8px, 1.6dvh, 14px);
  }

  &__badge {
    padding: 3px 8px;
    border-radius: 8px;
    background: rgb(255 255 255 / 9%);
    color: #d6dde5;
    font-size: 11px;

    &--accent {
      background: rgb(79 209 197 / 14%);
      color: #4fd1c5;
    }
  }

  // Высокая зона клика вокруг тонкой полоски.
  &__bar {
    position: relative;
    height: 20px;
    margin-top: 12px;
    cursor: pointer;

    &::before,
    i {
      position: absolute;
      left: 0;
      top: 8px;
      height: 4px;
      border-radius: 2px;
    }

    &::before {
      content: '';
      right: 0;
      background: rgb(255 255 255 / 18%);
    }

    i {
      display: block;
      background: #e7ecf2;

      &::after {
        content: '';
        position: absolute;
        right: -6px;
        top: -4px;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background: #e7ecf2;
      }
    }
  }

  &__times {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: #8a96a3;
    font-variant-numeric: tabular-nums;
  }

  &__controls {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: clamp(6px, 1.6dvh, 14px) 0 clamp(12px, 3dvh, 28px);
  }

  &__ctl {
    width: 52px;
    height: 52px;
    display: grid;
    place-items: center;
    border: none;
    background: none;
    color: inherit;
    font: inherit;
    cursor: pointer;

    svg {
      width: 32px;
      height: 32px;
      fill: currentColor;
    }

    &--small {
      font-size: 20px;
      color: #c7cfd8;
    }

    &:disabled {
      opacity: 0.3;
    }
  }

  &__play {
    width: 76px;
    height: 76px;
    display: grid;
    place-items: center;
    border: none;
    border-radius: 50%;
    background: #e7ecf2;
    color: #0b0f14;
    cursor: pointer;

    svg {
      width: 34px;
      height: 34px;
      fill: currentColor;
    }
  }

  &__next {
    flex: none;
    margin-top: auto;
    padding: 12px 14px;
    border-radius: 14px;
    background: rgb(255 255 255 / 6%);
    font-size: 13px;

    b {
      display: block;
      margin-bottom: 8px;
      font-size: 11px;
      font-weight: 600;
      color: #8a96a3;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
  }

  &__row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 4px 0;
  }

  &__thumb {
    flex: none;
    width: 34px;
    height: 34px;
    border-radius: 6px;
  }

  // Не влезает — меньше строк, но без прокрутки.
  @media (max-height: 820px) {
    &__row:nth-of-type(n + 3) {
      display: none;
    }
  }

  @media (max-height: 740px) {
    &__row:nth-of-type(n + 2) {
      display: none;
    }
  }

  @media (max-height: 660px) {
    &__next {
      display: none;
    }
  }

  &__row-text {
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;

    small {
      color: #8a96a3;
    }
  }
}
</style>
