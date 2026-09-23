<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'

import { toolLabel } from '../toolLabels'
import type { Message } from '../types'

const props = defineProps<{ messages: Message[] }>()
const emit = defineEmits<{ cancel: [turnId: string] }>()

const log = ref<HTMLElement | null>(null)

// Секундомер для выполняющихся просьб — тикает, только пока они есть.
const now = ref(Date.now())
const hasPending = computed(() => props.messages.some((m) => m.pending))
let ticker: number | null = null
watch(
  hasPending,
  (active) => {
    if (active && ticker === null) {
      ticker = window.setInterval(() => (now.value = Date.now()), 1000)
    } else if (!active && ticker !== null) {
      clearInterval(ticker)
      ticker = null
    }
  },
  { immediate: true },
)
onUnmounted(() => ticker !== null && clearInterval(ticker))

function elapsed(since: number): string {
  const s = Math.max(0, Math.floor((now.value - since) / 1000))
  return s < 60 ? `${s} с` : `${Math.floor(s / 60)} мин ${s % 60} с`
}

watch(
  () => props.messages.length,
  async () => {
    await nextTick()
    log.value?.scrollTo({ top: log.value.scrollHeight, behavior: 'smooth' })
  },
)
</script>

<template>
  <section ref="log" class="log">
    <p v-if="!messages.length" class="log__empty">
      Спроси что-нибудь голосом или текстом.<br />Разговор общий с Джарвисом дома.
    </p>
    <article
      v-for="m in messages"
      :key="m.id"
      class="log__msg"
      :class="[`log__msg--${m.role}`, { 'log__msg--cancelled': m.cancelled }]"
    >
      {{ m.text }}
      <small v-if="m.cancelled" class="log__tools">отменено</small>
      <small v-else-if="m.pending" class="log__pending">
        <span class="log__spinner" />
        {{ elapsed(m.pending.since) }} · {{ m.pending.background ? 'в фоне · ' : '' }}{{ toolLabel(m.pending.tool) }}
        <button class="log__cancel" type="button" aria-label="Отменить" @click="emit('cancel', m.pending.turnId)">
          ✕
        </button>
      </small>
      <small v-else-if="m.tools?.length" class="log__tools">{{ m.tools.join(' · ') }}</small>
    </article>
  </section>
</template>

<style scoped lang="scss">
.log {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;

  &__empty {
    margin: auto;
    text-align: center;
    color: var(--text-dim);
    line-height: 1.5;
  }

  &__msg {
    max-width: 85%;
    padding: 10px 14px;
    border-radius: 18px;
    line-height: 1.4;
    white-space: pre-wrap;
    overflow-wrap: anywhere;

    &--user {
      align-self: flex-end;
      background: var(--accent);
      color: var(--bg);
      border-bottom-right-radius: 6px;

      // Серый на бирюзовом не читается — подписи цветом текста пузыря.
      .log__tools {
        color: inherit;
        opacity: 0.7;
      }
    }

    &--assistant {
      align-self: flex-start;
      background: var(--surface);
      border-bottom-left-radius: 6px;
    }

    &--cancelled {
      opacity: 0.5;
    }

    &--error {
      align-self: center;
      font-size: 13px;
      color: var(--danger);
    }
  }

  &__pending {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 6px;
    font-size: 12px;
    opacity: 0.8;
  }

  &__spinner {
    width: 10px;
    height: 10px;
    border: 2px solid currentColor;
    border-right-color: transparent;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  &__cancel {
    margin-left: 4px;
    padding: 2px 8px;
    border: none;
    border-radius: 999px;
    background: color-mix(in srgb, currentColor 18%, transparent);
    color: inherit;
    font: inherit;
    cursor: pointer;
  }

  &__tools {
    display: block;
    margin-top: 6px;
    font-size: 11px;
    color: var(--text-dim);
  }
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
