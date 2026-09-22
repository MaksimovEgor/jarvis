<script setup lang="ts">
import { computed } from 'vue'

import type { Status } from '../types'

const props = defineProps<{ status: Status }>()
const emit = defineEmits<{ press: [] }>()

const label = computed(() => {
  switch (props.status) {
    case 'recording':
      return 'Слушаю — нажми, чтобы отправить'
    case 'thinking':
      return 'Думаю…'
    case 'speaking':
      return 'Нажми, чтобы остановить'
    default:
      return 'Нажми и говори'
  }
})
</script>

<template>
  <div class="talk">
    <button
      class="talk__button"
      :class="`talk__button--${status}`"
      :disabled="status === 'thinking'"
      :aria-label="label"
      @click="emit('press')"
    >
      <svg v-if="status === 'speaking'" viewBox="0 0 24 24" class="talk__icon">
        <rect x="7" y="7" width="10" height="10" rx="2" />
      </svg>
      <svg v-else viewBox="0 0 24 24" class="talk__icon">
        <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Z" />
        <path d="M18 11a6 6 0 0 1-12 0M12 17v4" fill="none" stroke-width="2" stroke-linecap="round" />
      </svg>
    </button>
    <p class="talk__label">{{ label }}</p>
  </div>
</template>

<style scoped lang="scss">
.talk {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;

  &__button {
    width: 76px;
    height: 76px;
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

    &--thinking {
      opacity: 0.5;
      animation: breathe 1.6s ease-in-out infinite;
    }

    &--speaking {
      background: var(--surface-2);
      color: var(--text);
    }
  }

  &__icon {
    width: 32px;
    height: 32px;
    fill: currentColor;
    stroke: currentColor;
  }

  &__label {
    margin: 0;
    font-size: 13px;
    color: var(--text-dim);
  }
}

@keyframes pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--danger) 60%, transparent);
  }
  50% {
    box-shadow: 0 0 0 14px transparent;
  }
}

@keyframes breathe {
  50% {
    transform: scale(0.96);
  }
}
</style>
