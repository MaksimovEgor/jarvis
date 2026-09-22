import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Для `npm run dev` ядро на asus слушает только loopback — пробросить порт:
//   ssh -N -L 8000:127.0.0.1:8000 asus
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/chat': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
})
