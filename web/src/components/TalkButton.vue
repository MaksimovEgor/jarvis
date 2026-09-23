<script setup lang="ts">
import { computed } from 'vue'

import type { Status } from '../types'

// send — в поле введён текст: кнопка отправляет его вместо записи голоса.
const props = defineProps<{ status: Status; send: boolean }>()
const emit = defineEmits<{ press: [] }>()

const label = computed(() => {
  if (props.send) return 'Отправить'
  switch (props.status) {
    case 'recording':
      return 'Отправить'
    default:
      return 'Сказать'
  }
})
</script>

<template>
  <button
    type="button"
    class="talk"
    :class="send ? 'talk--send' : `talk--${status}`"
    :aria-label="label"
    @click="emit('press')"
  >
    <svg v-if="send" viewBox="0 0 24 24" class="talk__icon">
      <path d="M12 19V6M6 11l6-6 6 6" fill="none" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
    <svg v-else viewBox="0 0 24 24" class="talk__icon">
      <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Z" />
      <path d="M18 11a6 6 0 0 1-12 0M12 17v4" fill="none" stroke-width="2" stroke-linecap="round" />
    </svg>
  </button>
</template>

<style scoped lang="scss">
.talk {
  flex-shrink: 0;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  border: none;
  display: grid;
  place-items: center;
  background: var(--accent);
  color: var(--bg);
  cursor: pointer;
  transition: transform 0.15s ease, background 0.2s ease, opacity 0.2s ease;
  -webkit-tap-highlight-color: transparent;

  &:active {
    transform: scale(0.94);
  }

  &--recording {
    background: var(--danger);
    animation: pulse 1.2s ease-in-out infinite;
  }

  // Думает над прошлой просьбой, но кнопка живая — можно говорить.
  &--thinking {
    animation: breathe 1.6s ease-in-out infinite;
  }

  &--speaking {
    background: var(--surface-2);
    color: var(--text);
  }

  &__icon {
    width: 24px;
    height: 24px;
    fill: currentColor;
    stroke: currentColor;
  }
}

@keyframes pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--danger) 60%, transparent);
  }
  50% {
    box-shadow: 0 0 0 10px transparent;
  }
}

@keyframes breathe {
  50% {
    transform: scale(0.96);
  }
}
</style>
