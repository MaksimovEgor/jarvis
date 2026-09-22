import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Для `npm run dev` ядро на asus слушает только loopback — пробросить порт:
//   ssh -N -L 8000:127.0.0.1:8000 asus
// Публично фронт живёт на https://point.abrdns.com:8446/ (Caddy на point →
// обратный SSH-туннель → ядро на asus). Отдельный порт = отдельный origin:
// service worker фронта Point на :443 перехватывает любые навигации своего
// origin и на подпути (/jarvis/) отдавал бы оболочку Point.
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/chat': 'http://127.0.0.1:8000',
    },
  },
})
