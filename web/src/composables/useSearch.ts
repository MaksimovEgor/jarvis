import { ref, watch } from 'vue'

import { searchCatalog } from '../api'
import type { SearchSection, SearchTab } from '../types'

const DEBOUNCE_MS = 400
const STORE_KEY = 'jarvis:search'

function restore(): { q: string; tab: SearchTab } {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) ?? '{}') as { q?: string; tab?: SearchTab }
    return { q: saved.q ?? '', tab: saved.tab ?? 'yandex' }
  } catch {
    return { q: '', tab: 'yandex' }
  }
}

// Поиск по каталогам: один запрос на паузу в наборе (Яндекс отвечал 429),
// устаревший ответ отменяется, запрос и вкладка переживают закрытие экрана.
export function useSearch() {
  const saved = restore()
  const query = ref(saved.q)
  const tab = ref<SearchTab>(saved.tab)
  const sections = ref<SearchSection[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  let timer: number | null = null
  let controller: AbortController | null = null

  async function run(): Promise<void> {
    controller?.abort()
    const q = query.value.trim()
    if (!q && tab.value !== 'kids') {
      sections.value = []
      loading.value = false
      return
    }
    controller = new AbortController()
    const current = controller
    loading.value = true
    error.value = null
    try {
      sections.value = await searchCatalog(q, tab.value, current.signal)
    } catch (e) {
      if (current.signal.aborted) return
      error.value = e instanceof Error ? e.message : String(e)
      sections.value = []
    } finally {
      if (controller === current) loading.value = false
    }
  }

  watch([query, tab], ([q, t], [, prevTab]) => {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({ q, tab: t }))
    } catch {
      // приватный режим — не запоминаем
    }
    if (timer !== null) clearTimeout(timer)
    // Смена вкладки — сразу, набор текста — после паузы.
    timer = window.setTimeout(() => void run(), t !== prevTab ? 0 : DEBOUNCE_MS)
  })

  void run()

  return { query, tab, sections, loading, error, run }
}
