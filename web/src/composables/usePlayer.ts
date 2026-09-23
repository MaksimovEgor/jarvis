import { ref } from 'vue'

// 0.1с тишины в WAV — для «разблокировки» элемента.
export const SILENCE =
  'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA='

// Голос Джарвиса: ответы и объявления таймеров. Музыка — отдельно, useMusic.
export function usePlayer() {
  const isPlaying = ref(false)
  const audio = new Audio()
  let finish: (() => void) | null = null

  function done(): void {
    isPlaying.value = false
    finish?.()
    finish = null
  }

  // Не onpause: смена src у играющего элемента тоже шлёт pause, и новая
  // фраза «закончилась» бы сразу. Остановка вручную — через stop().
  audio.onended = done
  audio.onerror = done

  // iOS Safari разрешает play() только синхронно внутри жеста пользователя.
  // Ответ приходит через несколько секунд после тапа — к тому моменту жест
  // «истёк». Поэтому элемент разблокируется пустым звуком прямо в обработчике
  // тапа, а потом переиспользуется для ответа.
  function unlock(): void {
    audio.src = SILENCE
    audio.play().catch(() => undefined)
  }

  // Промис завершается, когда фраза доиграла (или её остановили) — после
  // этого можно возвращать музыку.
  function playUrl(url: string): Promise<void> {
    return new Promise((resolve) => {
      finish?.()
      finish = resolve
      audio.src = url
      isPlaying.value = true
      audio.play().catch(done)
    })
  }

  function play(base64Wav: string): Promise<void> {
    return playUrl(`data:audio/wav;base64,${base64Wav}`)
  }

  function stop(): void {
    audio.pause()
    done()
  }

  return { isPlaying, unlock, play, playUrl, stop }
}
