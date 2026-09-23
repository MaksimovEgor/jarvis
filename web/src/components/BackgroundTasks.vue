<script setup lang="ts">
import { toolLabel } from '../toolLabels'
import type { Message } from '../types'

// Фоновые просьбы (долгий ресерч): основной план свободен, а здесь видно,
// что работа идёт, что именно делается, и её можно отменить.
defineProps<{ tasks: Message[] }>()
const emit = defineEmits<{ cancel: [turnId: string] }>()
</script>

<template>
  <ul v-if="tasks.length" class="bg">
    <li v-for="t in tasks" :key="t.id" class="bg__task">
      <span class="bg__spinner" />
      <span class="bg__text">{{ t.text }}</span>
      <span class="bg__tool">{{ toolLabel(t.pending?.tool) }}</span>
      <button
        class="bg__cancel"
        type="button"
        aria-label="Отменить фоновую задачу"
        @click="t.pending && emit('cancel', t.pending.turnId)"
      >
        ✕
      </button>
    </li>
  </ul>
</template>

<style scoped lang="scss">
.bg {
  list-style: none;
  margin: 0;
  padding: 8px 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-bottom: 1px solid var(--border);

  &__task {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    font-size: 13px;
  }

  &__spinner {
    flex: none;
    width: 10px;
    height: 10px;
    border: 2px solid var(--warn);
    border-right-color: transparent;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  &__text {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }

  &__tool {
    flex: none;
    color: var(--text-dim);
  }

  &__cancel {
    flex: none;
    padding: 2px 8px;
    border: none;
    border-radius: 999px;
    background: var(--surface-2);
    color: var(--text);
    font: inherit;
    cursor: pointer;
  }
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
