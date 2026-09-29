<script setup lang="ts">
import { computed } from 'vue'

import type { TurnTiming } from '../types'

const props = defineProps<{ timing: TurnTiming }>()

// Цель итерации «Быстрее» (docs/plans/it1-faster/01-product.md): до 2,5 с.
const SLOW_SECONDS = 2.5

const seconds = computed(() => props.timing.firstSound)
const slow = computed(() => (seconds.value ?? 0) > SLOW_SECONDS)

// Этапы на ядре — отметки от начала хода; здесь — сколько занял каждый.
const steps = computed(() => {
  const s = props.timing.stages ?? {}
  const result: { label: string; value: number }[] = []
  let prev = 0
  const add = (label: string, at: number | undefined): void => {
    if (at === undefined) return
    result.push({ label, value: Math.max(0, at - prev) })
    prev = at
  }
  add('распознал', s.stt)
  add('понял', s.route)
  add('думал', s.llm_total)
  return result
})
const widest = computed(() => Math.max(seconds.value ?? 0, ...steps.value.map((s) => s.value), 0.01))

function format(s: number): string {
  return `${s.toFixed(s < 0.1 ? 2 : 1).replace('.', ',')} с`
}
</script>

<template>
  <details v-if="seconds !== undefined" class="latency" :class="{ 'latency--slow': slow }">
    <summary>
      <template v-if="timing.kind === 'fast'">⚡ быстро · </template>
      {{ timing.silent ? 'ответил' : 'заговорил' }} через <b>{{ format(seconds) }}</b>
      <template v-if="steps.length"> ▸</template>
    </summary>
    <div v-if="steps.length" class="latency__bars">
      <template v-for="step in steps" :key="step.label">
        <span>{{ step.label }}</span>
        <i class="latency__bar" :style="{ width: `${(step.value / widest) * 100}%` }" />
        <span class="latency__num">{{ format(step.value) }}</span>
      </template>
    </div>
  </details>
</template>

<style scoped lang="scss">
.latency {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-dim);

  summary {
    list-style: none;
    cursor: pointer;
    user-select: none;

    &::-webkit-details-marker {
      display: none;
    }
  }

  b {
    color: var(--text);
    font-weight: 600;
  }

  &--slow b {
    color: var(--warn);
  }

  &__bars {
    display: grid;
    grid-template-columns: auto 1fr auto;
    gap: 3px 8px;
    align-items: center;
    margin-top: 6px;
    max-width: 280px;
  }

  &__bar {
    display: block;
    height: 6px;
    min-width: 2px;
    border-radius: 3px;
    background: var(--accent);
  }

  &__num {
    text-align: right;
  }
}
</style>
