<script setup lang="ts">
defineProps<{
  text: string
  undo: boolean
}>()

const emit = defineEmits<{
  undo: []
}>()
</script>

<template>
  <div class="toast" role="status">
    <span class="toast__text">{{ text }}</span>
    <button v-if="undo" class="toast__undo" @click="emit('undo')">Отменить</button>
  </div>
</template>

<style scoped lang="scss">
.toast {
  position: fixed;
  left: 50%;
  bottom: calc(88px + env(safe-area-inset-bottom));
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 12px;
  max-width: calc(100% - 32px);
  padding: 10px 14px;
  border-radius: 12px;
  background: var(--surface-2);
  color: var(--text);
  font-size: 13px;
  box-shadow: 0 6px 24px rgb(0 0 0 / 25%);
  transform: translateX(-50%);

  &__text {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &__undo {
    flex: none;
    padding: 0;
    border: none;
    background: none;
    color: var(--accent);
    font: inherit;
    font-weight: 600;
    cursor: pointer;
  }
}
</style>
