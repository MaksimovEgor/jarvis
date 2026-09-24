<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { libraryPlay } from '../api'
import { useLibrary } from '../composables/useLibrary'
import type { LibraryTrack, WaveSettings } from '../types'
import YandexCard from './YandexCard.vue'

const emit = defineEmits<{
  close: []
  played: [text: string]
}>()

// Настройки как в «Моей волне» Яндекса. Занятие и эпоха — станции (одна на
// выбор), настроение/характер/язык — поверх. Без Яндекса они ведут нашу волну.
type Option<T extends string> = { id: T; label: string }
const ACTIVITIES: Option<string>[] = [
  { id: 'activity:wake-up', label: 'Просыпаюсь' },
  { id: 'activity:work-background', label: 'Работаю' },
  { id: 'activity:study-background', label: 'Концентрация' },
  { id: 'activity:driving', label: 'За рулём' },
  { id: 'activity:road-trip', label: 'В дороге' },
  { id: 'activity:workout', label: 'Тренируюсь' },
  { id: 'activity:run', label: 'Бег' },
  { id: 'activity:party', label: 'Вечеринка' },
  { id: 'activity:romantic-date', label: 'Свидание' },
  { id: 'activity:beloved', label: 'Для влюблённых' },
  { id: 'activity:fall-asleep', label: 'Засыпаю' },
]
const MOODS: Option<WaveSettings['mood_energy']>[] = [
  { id: 'active', label: 'Бодрое' },
  { id: 'fun', label: 'Весёлое' },
  { id: 'calm', label: 'Спокойное' },
  { id: 'sad', label: 'Грустное' },
]
const CHARACTERS: Option<WaveSettings['diversity']>[] = [
  { id: 'favorite', label: 'Любимое' },
  { id: 'discover', label: 'Незнакомое' },
  { id: 'popular', label: 'Популярное' },
]
const LANGUAGES: Option<WaveSettings['language']>[] = [
  { id: 'russian', label: 'Русское' },
  { id: 'not-russian', label: 'Иностранное' },
  { id: 'without-words', label: 'Без слов' },
]
const EPOCHS: Option<string>[] = [
  { id: 'epoch:the-greatest-hits', label: 'Вечные хиты' },
  { id: 'epoch:seventies', label: '70-е' },
  { id: 'epoch:eighties', label: '80-е' },
  { id: 'epoch:nineties', label: '90-е' },
  { id: 'epoch:zeroes', label: '2000-е' },
  { id: 'epoch:tenths', label: '2010-е' },
  { id: 'epoch:twenties', label: '2020-е' },
]
const GROUPS = [
  { title: 'Занятие', key: 'station', options: ACTIVITIES },
  { title: 'Настроение', key: 'mood_energy', options: MOODS },
  { title: 'Характер', key: 'diversity', options: CHARACTERS },
  { title: 'Язык', key: 'language', options: LANGUAGES },
  { title: 'Эпоха', key: 'station', options: EPOCHS },
] as const
type GroupKey = (typeof GROUPS)[number]['key']
const DEFAULTS: Record<GroupKey, string> = { station: 'user:onyourwave', mood_energy: 'all', diversity: 'default', language: 'any' }

const library = useLibrary()
const tab = ref<'likes' | 'hidden'>('likes')
const wave = ref<Record<GroupKey, string>>({ ...DEFAULTS })

function isOn(key: GroupKey, id: string): boolean {
  return wave.value[key] === id
}

// Повторный тап снимает выбор — как в Яндексе.
function pick(key: GroupKey, id: string): void {
  wave.value = { ...wave.value, [key]: isOn(key, id) ? DEFAULTS[key] : id }
}

function waveSettings(): WaveSettings {
  const v = wave.value
  return {
    station: v.station,
    mood_energy: MOODS.find((o) => o.id === v.mood_energy)?.id ?? 'all',
    diversity: CHARACTERS.find((o) => o.id === v.diversity)?.id ?? 'default',
    language: LANGUAGES.find((o) => o.id === v.language)?.id ?? 'any',
    label: waveLabel.value,
  }
}

const waveLabel = computed(() =>
  GROUPS.flatMap((g) => g.options.filter((o) => isOn(g.key, o.id)).map((o) => o.label.toLowerCase())).join(' · '),
)
const starting = ref(false)
let searchTimer: number | null = null

// Та же разбивка суток, что у волны на ядре (app/music/models.py:slot_at).
const moment = computed(() => {
  const now = new Date()
  const hour = now.getHours()
  const slot = hour >= 6 && hour < 11 ? 'утро' : hour >= 11 && hour < 17 ? 'день' : hour >= 17 && hour < 23 ? 'вечер' : 'ночь'
  const day = now.toLocaleDateString('ru-RU', { weekday: 'long' })
  const hint: Record<string, string> = {
    утро: 'подберу бодрее',
    день: 'подберу под день',
    вечер: 'подберу поспокойнее',
    ночь: 'подберу потише',
  }
  return `Сейчас ${slot}, ${day} — ${hint[slot]}`
})

const storageParts = computed(() => {
  const s = library.storage.value
  if (!s) return null
  return {
    liked: Math.min(100, (s.likedMb / s.limitMb) * 100),
    cache: Math.min(100, (s.cacheMb / s.limitMb) * 100),
    text: `${gb(s.likedMb + s.cacheMb)} из ${gb(s.limitMb)}`,
    likedText: gb(s.likedMb),
    cacheText: gb(s.cacheMb),
  }
})

function gb(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(1).replace('.', ',')} ГБ` : `${Math.round(mb)} МБ`
}

function clock(seconds: number | null): string {
  if (!seconds) return ''
  const s = Math.round(seconds)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function subtitle(track: LibraryTrack): string {
  return [track.artist, clock(track.duration)].filter(Boolean).join(' · ')
}

async function play(mode: 'wave' | 'liked' | 'track', ref?: string): Promise<void> {
  starting.value = true
  try {
    await libraryPlay(mode, mode === 'wave' ? { wave: waveSettings() } : { ref })
    emit('played', mode === 'wave' ? 'Включаю волну' : 'Включаю')
    emit('close')
  } catch {
    emit('played', 'Не получилось включить')
  } finally {
    starting.value = false
  }
}

function onSearch(): void {
  if (searchTimer !== null) clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => void library.loadLikes(), 300)
}

onMounted(() => void library.refresh())
</script>

<template>
  <section class="lib" aria-label="Моя музыка">
    <header class="lib__header">
      <button class="lib__back" aria-label="Назад" @click="emit('close')">
        <svg viewBox="0 0 24 24"><path d="M15.4 7.4L14 6l-6 6 6 6 1.4-1.4-4.6-4.6z" /></svg>
      </button>
      Моя музыка
    </header>

    <div class="lib__scroll">
      <YandexCard />
      <div class="wave">
        <h2 class="wave__title">Моя волна</h2>
        <p class="wave__hint">{{ moment }}</p>
        <button class="wave__play" :disabled="starting" @click="play('wave')">
          {{ starting ? 'Подбираю…' : '▶ Слушать' }}
        </button>
        <div v-for="group in GROUPS" :key="group.title" class="wave__group">
          <span class="wave__group-title">{{ group.title }}</span>
          <div class="wave__moods">
            <button
              v-for="o in group.options"
              :key="o.id"
              class="wave__mood"
              :class="{ 'wave__mood--on': isOn(group.key, o.id) }"
              @click="pick(group.key, o.id)"
            >
              {{ o.label }}
            </button>
          </div>
        </div>
      </div>

      <nav class="lib__tabs">
        <button :class="{ on: tab === 'likes' }" @click="tab = 'likes'">Лайки · {{ library.total.value }}</button>
        <button :class="{ on: tab === 'hidden' }" @click="tab = 'hidden'">
          Скрытые · {{ library.hiddenTracks.value.length + library.hiddenArtists.value.length }}
        </button>
      </nav>

      <template v-if="tab === 'likes'">
        <div class="lib__toolbar">
          <input
            v-model="library.query.value"
            class="lib__search"
            type="search"
            placeholder="Поиск по лайкам"
            @input="onSearch"
          />
          <button class="lib__shuffle" :disabled="!library.total.value || starting" @click="play('liked')">
            ▶ Перемешать
          </button>
        </div>
        <p v-if="library.error.value" class="lib__empty">Не загрузилось: {{ library.error.value }}</p>
        <p v-else-if="!library.loading.value && !library.likes.value.length" class="lib__empty">
          Пока пусто. Скажи «лайк» или нажми ♡ в плеере на понравившемся треке.
        </p>
        <ul class="lib__list">
          <li v-for="track in library.likes.value" :key="track.ref" class="item">
            <button class="item__main" @click="play('track', track.ref)">
              <span class="item__title">{{ track.title }}</span>
              <span class="item__sub">{{ subtitle(track) }}</span>
            </button>
            <button class="item__heart" aria-label="Убрать из лайков" @click="library.unlike(track)">♥</button>
          </li>
        </ul>
        <button
          v-if="library.likes.value.length < library.total.value && !library.query.value"
          class="lib__more"
          :disabled="library.loading.value"
          @click="library.loadLikes(false)"
        >
          Ещё
        </button>
      </template>

      <template v-else>
        <p v-if="!library.hiddenTracks.value.length && !library.hiddenArtists.value.length" class="lib__empty">
          Скрытого нет. «Дизлайк» убирает трек, два дизлайка исполнителю — он звучит реже.
        </p>
        <ul class="lib__list">
          <li v-for="artist in library.hiddenArtists.value" :key="artist.artist" class="item">
            <div class="item__main">
              <span class="item__title">{{ artist.artist }}</span>
              <span class="item__sub">исполнитель звучит реже · {{ artist.dislikes }} дизлайка</span>
            </div>
            <button class="item__restore" @click="library.restoreArtist(artist)">Вернуть</button>
          </li>
          <li v-for="track in library.hiddenTracks.value" :key="track.ref" class="item">
            <div class="item__main">
              <span class="item__title">{{ track.title }}</span>
              <span class="item__sub">{{ track.artist ?? 'трек скрыт' }}</span>
            </div>
            <button class="item__restore" @click="library.restoreTrack(track)">Вернуть</button>
          </li>
        </ul>
      </template>

      <div v-if="storageParts" class="storage">
        Место на сервере: {{ storageParts.text }}
        <div class="storage__meter">
          <i class="storage__liked" :style="{ width: `${storageParts.liked}%` }" />
          <i class="storage__cache" :style="{ width: `${storageParts.cache}%` }" />
        </div>
        <span class="storage__legend storage__legend--liked">■</span> лайки {{ storageParts.likedText }}
        &nbsp;
        <span class="storage__legend">■</span> кэш {{ storageParts.cacheText }} (вытесняется сам)
      </div>
    </div>
  </section>
</template>

<style scoped lang="scss">
.lib {
  position: fixed;
  inset: 0;
  z-index: 10;
  display: flex;
  flex-direction: column;
  max-width: 720px;
  margin: 0 auto;
  background: var(--bg);

  &__header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: calc(8px + env(safe-area-inset-top)) 16px 8px 6px;
    font-weight: 600;
    border-bottom: 1px solid var(--border);
  }

  &__back {
    width: 40px;
    height: 40px;
    display: grid;
    place-items: center;
    border: none;
    border-radius: 50%;
    background: transparent;
    color: var(--text);
    cursor: pointer;

    svg {
      width: 24px;
      height: 24px;
      fill: currentColor;
    }
  }

  &__scroll {
    flex: 1;
    overflow-y: auto;
    padding-bottom: calc(16px + env(safe-area-inset-bottom));
  }

  &__tabs {
    display: flex;
    gap: 18px;
    padding: 0 16px;
    border-bottom: 1px solid var(--border);

    button {
      padding: 10px 0;
      border: none;
      border-bottom: 2px solid transparent;
      background: none;
      color: var(--text-dim);
      font: inherit;
      font-size: 14px;
      cursor: pointer;

      &.on {
        color: var(--text);
        border-bottom-color: var(--accent);
      }
    }
  }

  &__toolbar {
    display: flex;
    gap: 10px;
    align-items: center;
    padding: 12px 16px 4px;
  }

  &__search {
    flex: 1;
    min-width: 0;
    padding: 8px 12px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text);
    font: inherit;
    font-size: 16px;
  }

  &__shuffle,
  &__more {
    flex: none;
    padding: 8px 12px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text);
    font: inherit;
    font-size: 13px;
    cursor: pointer;

    &:disabled {
      opacity: 0.5;
    }
  }

  &__more {
    display: block;
    margin: 8px auto 0;
  }

  &__empty {
    margin: 16px;
    color: var(--text-dim);
    font-size: 14px;
  }

  &__list {
    margin: 0;
    padding: 4px 0;
    list-style: none;
  }
}

.wave {
  margin: 12px 16px 16px;
  padding: 18px;
  border-radius: 18px;
  background: linear-gradient(135deg, color-mix(in srgb, var(--accent) 22%, var(--surface)), var(--surface-2));

  &__title {
    margin: 0 0 4px;
    font-size: 20px;
  }

  &__hint {
    margin: 0 0 14px;
    color: var(--text-dim);
    font-size: 13px;
  }

  &__play {
    padding: 9px 18px;
    border: none;
    border-radius: 20px;
    background: var(--accent);
    color: var(--bg);
    font: inherit;
    font-weight: 600;
    cursor: pointer;

    &:disabled {
      opacity: 0.7;
    }
  }

  &__group {
    margin-top: 12px;
  }

  &__group-title {
    display: block;
    margin-bottom: 6px;
    color: var(--text-dim);
    font-size: 11px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  &__moods {
    display: flex;
    gap: 8px;
    overflow-x: auto;
    scrollbar-width: none;
  }

  &__mood {
    flex: none;
    padding: 5px 11px;
    border-radius: 14px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text);
    font: inherit;
    font-size: 13px;
    cursor: pointer;

    &--on {
      border-color: var(--accent);
      color: var(--accent);
    }
  }
}

.item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 8px 0 16px;

  &__main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    padding: 10px 0;
    border: none;
    background: none;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }

  &__title {
    font-size: 14px;
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__sub {
    font-size: 12px;
    color: var(--text-dim);
  }

  &__heart,
  &__restore {
    flex: none;
    min-width: 40px;
    height: 40px;
    border: none;
    background: none;
    font: inherit;
    cursor: pointer;
  }

  &__heart {
    color: var(--danger);
    font-size: 18px;
  }

  &__restore {
    color: var(--accent);
    font-size: 13px;
  }
}

.storage {
  margin: 16px;
  color: var(--text-dim);
  font-size: 12px;

  &__meter {
    display: flex;
    height: 6px;
    margin: 6px 0;
    overflow: hidden;
    border-radius: 3px;
    background: var(--surface-2);

    i {
      display: block;
      height: 6px;
    }
  }

  &__liked {
    background: var(--danger);
  }

  &__cache {
    background: var(--text-dim);
  }

  &__legend--liked {
    color: var(--danger);
  }
}
</style>
