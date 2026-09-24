import { ref, watch, type Ref } from 'vue'

// Средний цвет обложки — фон мини-плеера и «Сейчас играет», как в Яндекс Музыке.
// Обложка идёт через ядро (/media/cover), тот же origin — canvas её читает.
export function useCoverColor(url: Ref<string | null | undefined>) {
  const color = ref<string | null>(null)
  const cache = new Map<string, string>()

  watch(
    url,
    (src) => {
      if (!src) {
        color.value = null
        return
      }
      const known = cache.get(src)
      if (known) {
        color.value = known
        return
      }
      const img = new Image()
      img.onload = () => {
        if (url.value !== src) return
        const canvas = document.createElement('canvas')
        canvas.width = canvas.height = 1
        const ctx = canvas.getContext('2d')
        if (!ctx) return
        // Центр превью — сама обложка, без чёрных полей 4:3.
        ctx.drawImage(img, img.width * 0.2, img.height * 0.2, img.width * 0.6, img.height * 0.6, 0, 0, 1, 1)
        const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data
        // Приглушаем: на ярком фоне белый текст не читается.
        const value = `rgb(${Math.round(r * 0.55)} ${Math.round(g * 0.55)} ${Math.round(b * 0.55)})`
        cache.set(src, value)
        color.value = value
      }
      img.src = src
    },
    { immediate: true },
  )

  return color
}
