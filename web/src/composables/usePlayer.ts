import { ref } from 'vue'

// 0.1с тишины в WAV — для «разблокировки» элемента.
const SILENCE =
  'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA='

export function usePlayer() {
  const isPlaying = ref(false)
  const audio = new Audio()
  audio.onended = () => (isPlaying.value = false)
  audio.onpause = () => (isPlaying.value = false)

  // iOS Safari разрешает play() только синхронно внутри жеста пользователя.
  // Ответ приходит через несколько секунд после тапа — к тому моменту жест
  // «истёк». Поэтому элемент разблокируется пустым звуком прямо в обработчике
  // тапа, а потом переиспользуется для ответа.
  function unlock(): void {
    audio.src = SILENCE
    audio.play().catch(() => undefined)
  }

  async function play(base64Wav: string): Promise<void> {
    audio.src = `data:audio/wav;base64,${base64Wav}`
    isPlaying.value = true
    try {
      await audio.play()
    } catch {
      isPlaying.value = false
    }
  }

  function stop(): void {
    audio.pause()
  }

  return { isPlaying, unlock, play, stop }
}
