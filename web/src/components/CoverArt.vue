<script setup lang="ts">
import { ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    src: string | null
    live?: boolean
    // Превью YouTube 4:3 с чёрными полями — увеличить и обрезать.
    crop?: boolean
  }>(),
  { live: false, crop: true },
)

const failed = ref(false)
watch(
  () => props.src,
  () => (failed.value = false),
)
</script>

<template>
  <div class="cover">
    <img v-if="src && !failed" :class="{ crop }" :src="src" alt="" @error="failed = true" />
    <svg v-else viewBox="0 0 24 24" aria-hidden="true">
      <path v-if="live" d="M3.2 6.2L17.6 1l.7 1.9L7.9 6.7H20c1.1 0 2 .9 2 2V20c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V8.7c0-1 .5-1.8 1.2-2.5zM7 19a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm13-7V9H4v3h16z" />
      <path v-else d="M12 3v10.6A4 4 0 1 0 14 17V7h4V3h-6z" />
    </svg>
  </div>
</template>

<style scoped lang="scss">
// Превью YouTube — 4:3 с чёрными полями у квадратных обложек: увеличиваем
// на 4/3 и режем по центру, остаётся сама обложка.
.cover {
  display: grid;
  place-items: center;
  overflow: hidden;
  background: var(--surface-2);
  color: var(--text-dim);

  img {
    width: 100%;
    height: 100%;
    object-fit: cover;

    &.crop {
      transform: scale(1.34);
    }
  }

  svg {
    width: 45%;
    height: 45%;
    fill: currentColor;
  }
}
</style>
