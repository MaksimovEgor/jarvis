import { createApp } from 'vue'

import App from './App.vue'
import './styles.scss'

// Вход — cookie на год (app/web_auth.py). Истекла или сброшена — ядро отвечает
// 401 на любой запрос API: уходим на страницу входа и возвращаемся сюда же.
const nativeFetch = window.fetch.bind(window)
window.fetch = async (...args: Parameters<typeof fetch>): Promise<Response> => {
  const resp = await nativeFetch(...args)
  if (resp.status === 401) {
    location.href = `login?next=${encodeURIComponent(location.pathname + location.search)}`
  }
  return resp
}

createApp(App).mount('#app')
