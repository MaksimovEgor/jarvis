<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { playEntity, playNext, rateTrack } from '../api'
import { useSearch } from '../composables/useSearch'
import type { Entity, SearchSection, SearchTab } from '../types'
import CoverArt from './CoverArt.vue'

const emit = defineEmits<{
  close: []
  played: [text: string]
  lyrics: []
}>()

const TABS: { id: SearchTab; label: string }[] = [
  { id: 'yandex', label: 'Яндекс' },
  { id: 'youtube', label: 'YouTube' },
  { id: 'soundcloud', label: 'SoundCloud' },
  { id: 'books', label: 'Книги' },
  { id: 'podcasts', label: 'Подкасты' },
  { id: 'kids', label: 'Детям' },
]
const PLACEHOLDER: Record<SearchTab, string> = {
  yandex: 'Исполнитель, трек, альбом',
  youtube: 'Песня, клип, лайв',
  soundcloud: 'Ремиксы, демо, андеграунд',
  books: 'Книга или автор',
  podcasts: 'Подкаст',
  kids: 'Сказка, мультик, детская песня',
}
// Горизонтальной лентой — альбомы, книги, подкасты, исполнители; остальное — списком.
const GRID: SearchSection['kind'][] = ['albums', 'artists', 'books', 'podcasts', 'stations']

const search = useSearch()
const input = ref<HTMLInputElement | null>(null)
const menu = ref<Entity | null>(null)
const busy = ref<string | null>(null)

onMounted(() => {
  if (!search.query.value) input.value?.focus()
})

async function open(entity: Entity): Promise<void> {
  busy.value = entity.id
  try {
    await playEntity(entity)
    emit('played', `Включаю: ${entity.title}`)
    emit('close')
  } catch {
    emit('played', 'Не получилось включить')
  } finally {
    busy.value = null
  }
}

async function next(entity: Entity): Promise<void> {
  menu.value = null
  await playNext(entity).catch(() => undefined)
  emit('played', `Следующим: ${entity.title}`)
}

async function like(entity: Entity): Promise<void> {
  const liked = entity.rating === 1
  entity.rating = liked ? null : 1
  await rateTrack(liked ? 'none' : 'like', entity.id).catch(() => (entity.rating = liked ? 1 : null))
}

async function lyrics(entity: Entity): Promise<void> {
  menu.value = null
  await open(entity)
  emit('lyrics')
}

function kindLabel(entity: Entity): string {
  return { artist: 'Исполнитель', album: 'Альбом', playlist: 'Плейлист', audiobook: 'Книга', podcast: 'Подкаст', station: 'Станция', track: '' }[
    entity.type
  ]
}
</script>

<template>
  <section class="se" aria-label="Поиск">
    <header class="se__head">
      <button class="se__back" aria-label="Назад" @click="emit('close')">
        <svg viewBox="0 0 24 24"><path d="M15.4 7.4L14 6l-6 6 6 6 1.4-1.4-4.6-4.6z" /></svg>
      </button>
      <label class="se__field">
        <svg viewBox="0 0 24 24"><path d="M15.5 14h-.8l-.3-.3A6.5 6.5 0 1 0 14 15.5l.3.3v.8l5 5 1.5-1.5-5-5zm-6 0a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9z" /></svg>
        <input
          ref="input"
          v-model="search.query.value"
          type="search"
          enterkeyhint="search"
          :placeholder="PLACEHOLDER[search.tab.value]"
          autocapitalize="none"
          autocorrect="off"
        />
        <button v-if="search.query.value" class="se__clear" aria-label="Очистить" @click="search.query.value = ''">✕</button>
      </label>
    </header>

    <nav class="se__tabs">
      <button
        v-for="t in TABS"
        :key="t.id"
        class="se__tab"
        :class="{ 'se__tab--on': search.tab.value === t.id }"
        @click="search.tab.value = t.id"
      >
        <span v-if="t.id === 'yandex'" class="se__ya">Я</span>{{ t.label }}
      </button>
    </nav>

    <div class="se__body">
      <p v-if="search.loading.value && !search.sections.value.length" class="se__empty">Ищу…</p>
      <p v-else-if="search.error.value" class="se__empty">Поиск не удался: {{ search.error.value }}</p>
      <p v-else-if="!search.sections.value.length && search.query.value" class="se__empty">
        {{
          search.tab.value === 'yandex'
            ? 'В Яндексе не нашлось (или он не подключён) — попробуй YouTube и SoundCloud.'
            : 'Ничего не нашлось.'
        }}
      </p>
      <p v-else-if="!search.sections.value.length" class="se__empty">Что ищем?</p>

      <template v-for="section in search.sections.value" :key="section.kind + section.title">
        <h4 class="se__title">{{ section.title }}</h4>

        <div v-if="section.kind === 'best'" class="se__best" @click="open(section.items[0])">
          <CoverArt
            class="se__best-cover"
            :class="{ 'se__round': section.items[0].type === 'artist' }"
            :src="section.items[0].cover"
            :crop="section.items[0].coverCrop"
          />
          <div class="se__best-text">
            <b>{{ section.items[0].title }}</b>
            <small>{{ kindLabel(section.items[0]) || section.items[0].subtitle }}</small>
          </div>
          <span class="se__go">▶</span>
        </div>

        <div v-else-if="GRID.includes(section.kind)" class="se__grid">
          <button v-for="e in section.items" :key="e.id" class="se__card" :disabled="busy === e.id" @click="open(e)">
            <CoverArt class="se__card-cover" :class="{ 'se__round': e.type === 'artist' }" :src="e.cover" :crop="e.coverCrop" />
            <b>{{ e.title }}</b>
            <small>{{ e.subtitle }}</small>
          </button>
        </div>

        <ul v-else class="se__list">
          <li v-for="e in section.items" :key="e.id" class="se__row">
            <button class="se__main" :disabled="busy === e.id" @click="open(e)">
              <CoverArt class="se__cover" :src="e.cover" :crop="e.coverCrop" />
              <span class="se__text">
                <b>{{ e.title }}</b>
                <small>{{ e.subtitle }}</small>
              </span>
            </button>
            <template v-if="e.type === 'track'">
              <button class="se__act" :class="{ 'se__act--liked': e.rating === 1 }" aria-label="Нравится" @click="like(e)">
                {{ e.rating === 1 ? '♥' : '♡' }}
              </button>
              <button class="se__act" aria-label="Ещё" @click="menu = e">⋯</button>
            </template>
          </li>
        </ul>
      </template>
    </div>

    <div v-if="menu" class="se__sheet-bg" @click.self="menu = null">
      <div class="se__sheet">
        <p class="se__sheet-title">{{ menu.title }}</p>
        <button @click="open(menu)">▶ Играть сейчас</button>
        <button @click="next(menu)">⤵ Играть следующим</button>
        <button @click="lyrics(menu)">❝ Текст</button>
        <button class="se__sheet-cancel" @click="menu = null">Отмена</button>
      </div>
    </div>
  </section>
</template>

<style scoped lang="scss">
.se {
  position: fixed;
  inset: 0;
  z-index: 12;
  display: flex;
  flex-direction: column;
  max-width: 720px;
  margin: 0 auto;
  background: var(--bg);

  &__head {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: calc(8px + env(safe-area-inset-top)) 16px 8px 6px;
  }

  &__back {
    width: 40px;
    height: 40px;
    display: grid;
    place-items: center;
    border: none;
    background: none;
    color: var(--text);
    cursor: pointer;

    svg {
      width: 24px;
      height: 24px;
      fill: currentColor;
    }
  }

  &__field {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    padding: 0 12px;
    border: 1px solid var(--border);
    border-radius: 14px;
    background: var(--surface);

    svg {
      flex: none;
      width: 18px;
      height: 18px;
      fill: var(--text-dim);
    }

    input {
      flex: 1;
      min-width: 0;
      padding: 11px 0;
      border: none;
      outline: none;
      background: none;
      color: var(--text);
      font: inherit;
      font-size: 16px;
    }
  }

  &__clear {
    border: none;
    background: none;
    color: var(--text-dim);
    cursor: pointer;
  }

  &__tabs {
    display: flex;
    gap: 8px;
    padding: 4px 16px 12px;
    overflow-x: auto;
    scrollbar-width: none;
    border-bottom: 1px solid var(--border);

    &::-webkit-scrollbar {
      display: none;
    }
  }

  &__tab {
    flex: none;
    display: flex;
    align-items: center;
    gap: 5px;
    padding: 7px 14px;
    border: 1px solid var(--border);
    border-radius: 16px;
    background: none;
    color: var(--text);
    font: inherit;
    font-size: 14px;
    cursor: pointer;

    &--on {
      background: var(--text);
      border-color: var(--text);
      color: var(--bg);
    }
  }

  &__ya {
    width: 15px;
    height: 15px;
    display: grid;
    place-items: center;
    border-radius: 4px;
    background: #ffdb4d;
    color: #000;
    font-size: 10px;
    font-weight: 800;
  }

  &__body {
    flex: 1;
    overflow-y: auto;
    padding-bottom: calc(24px + env(safe-area-inset-bottom));
  }

  &__empty {
    margin: 24px 16px;
    color: var(--text-dim);
    font-size: 14px;
  }

  &__title {
    margin: 18px 16px 8px;
    color: var(--text-dim);
    font-size: 12px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  &__best {
    display: flex;
    align-items: center;
    gap: 14px;
    margin: 0 16px;
    padding: 12px;
    border-radius: 16px;
    background: var(--surface);
    cursor: pointer;

    b {
      display: block;
      font-size: 18px;
    }

    small {
      color: var(--text-dim);
    }
  }

  &__best-cover {
    flex: none;
    width: 72px;
    height: 72px;
    border-radius: 12px;
  }

  &__best-text {
    flex: 1;
    min-width: 0;
  }

  &__go {
    flex: none;
    width: 42px;
    height: 42px;
    display: grid;
    place-items: center;
    border-radius: 50%;
    background: var(--accent);
    color: var(--bg);
  }

  &__round {
    border-radius: 50% !important;
  }

  &__grid {
    display: flex;
    gap: 12px;
    padding: 0 16px;
    overflow-x: auto;
    scrollbar-width: none;

    &::-webkit-scrollbar {
      display: none;
    }
  }

  &__card {
    flex: none;
    width: 118px;
    padding: 0;
    border: none;
    background: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;

    b,
    small {
      display: block;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    b {
      margin-top: 6px;
      font-size: 13px;
      font-weight: 500;
    }

    small {
      color: var(--text-dim);
      font-size: 11px;
    }
  }

  &__card-cover {
    width: 118px;
    height: 118px;
    border-radius: 12px;
  }

  &__list {
    margin: 0;
    padding: 0;
    list-style: none;
  }

  &__row {
    display: flex;
    align-items: center;
    padding: 0 6px 0 16px;
  }

  &__main {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 7px 0;
    border: none;
    background: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }

  &__cover {
    flex: none;
    width: 46px;
    height: 46px;
    border-radius: 8px;
  }

  &__text {
    min-width: 0;

    b,
    small {
      display: block;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    b {
      font-size: 14px;
      font-weight: 500;
    }

    small {
      color: var(--text-dim);
      font-size: 12px;
    }
  }

  &__act {
    flex: none;
    width: 38px;
    height: 38px;
    border: none;
    background: none;
    color: var(--text-dim);
    font-size: 18px;
    cursor: pointer;

    &--liked {
      color: var(--danger);
    }
  }

  &__sheet-bg {
    position: fixed;
    inset: 0;
    z-index: 13;
    display: flex;
    align-items: flex-end;
    background: rgb(0 0 0 / 45%);
  }

  &__sheet {
    width: 100%;
    max-width: 720px;
    margin: 0 auto;
    padding: 8px 8px calc(12px + env(safe-area-inset-bottom));
    border-radius: 18px 18px 0 0;
    background: var(--surface);

    button {
      display: block;
      width: 100%;
      padding: 14px 16px;
      border: none;
      border-radius: 12px;
      background: none;
      color: var(--text);
      font: inherit;
      font-size: 16px;
      text-align: left;
      cursor: pointer;

      &:active {
        background: var(--surface-2);
      }
    }
  }

  &__sheet-title {
    margin: 8px 16px;
    color: var(--text-dim);
    font-size: 13px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__sheet-cancel {
    color: var(--text-dim) !important;
    text-align: center !important;
  }
}
</style>
