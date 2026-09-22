<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

import type { Message } from '../types'

const props = defineProps<{ messages: Message[] }>()

const log = ref<HTMLElement | null>(null)

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
    <article v-for="m in messages" :key="m.id" class="log__msg" :class="`log__msg--${m.role}`">
      {{ m.text }}
      <small v-if="m.tools?.length" class="log__tools">{{ m.tools.join(' · ') }}</small>
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
    }

    &--assistant {
      align-self: flex-start;
      background: var(--surface);
      border-bottom-left-radius: 6px;
    }

    &--error {
      align-self: center;
      font-size: 13px;
      color: var(--danger);
    }
  }

  &__tools {
    display: block;
    margin-top: 6px;
    font-size: 11px;
    color: var(--text-dim);
  }
}
</style>
